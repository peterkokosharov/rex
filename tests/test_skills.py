# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-sonnet-5-5, claude-opus-5-5
# Licensed under MIT License

"""Tests for SKILL.md skills: discovery, frontmatter, index, tools, CLI."""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from runtimelib.skills import (
    FILE_SIZE_CAP, Skill, discover_skills, parse_frontmatter, read_skill_file, resolve_skill_file, skill_index,
)
from runtimelib.skills.cli import run_skills
from runtimelib.tools.skills import LoadSkillTool, ReadSkillFileTool


def make_skill(root: Path, folder: str, name: str | None, desc: str | None, body: str = "Do the thing.\n") -> Path:
    d = root / folder
    d.mkdir(parents=True, exist_ok=True)
    fm = ["---"]
    if name is not None:
        fm.append(f"name: {name}")
    if desc is not None:
        fm.append(f"description: {desc}")
    fm += ["extra_key: ignored", "---", ""]
    (d / "SKILL.md").write_text("\n".join(fm) + body, encoding="utf-8")
    return d


class Cfg:
    def __init__(self, data):
        self.data = data

    def get(self, key, default=None):
        return self.data.get(key, default)


TRUST = Cfg({"workspace.trust": True})


@pytest.fixture
def env(tmp_path):
    ws = tmp_path / "ws"
    home = tmp_path / "home"
    ws.mkdir()
    home.mkdir()
    return ws, home


def test_frontmatter_basic_and_unknown_keys():
    meta, body = parse_frontmatter('---\nname: a\ndescription: "Use: when x"\nfoo: bar\n---\n\n# Body\ntext\n')
    assert meta == {"name": "a", "description": "Use: when x", "foo": "bar"}
    assert body == "# Body\ntext\n"


def test_frontmatter_folded_and_crlf_and_none():
    meta, _ = parse_frontmatter("---\r\ndescription: >\r\n  one\r\n  two\r\nname: n\r\n---\r\nb")
    assert meta["description"] == "one two" and meta["name"] == "n"
    assert parse_frontmatter("no frontmatter") == ({}, "no frontmatter")


def test_missing_name_uses_folder_and_missing_description_skipped(env):
    ws, home = env
    root = ws / ".ai-rex" / "skills"
    make_skill(root, "folder-name", None, "has desc")
    make_skill(root, "nodesc", "nodesc", None)
    warnings: list[str] = []
    skills = discover_skills(ws, TRUST, home=home, warnings=warnings)
    assert [s.name for s in skills] == ["folder-name"]
    assert any("nodesc" in w and "description" in w for w in warnings)


def test_precedence_and_duplicate_warning(env, tmp_path):
    ws, home = env
    extra = tmp_path / "extra"
    make_skill(ws / ".ai-rex" / "skills", "a", "dup", "from workspace")
    make_skill(home / ".config" / "ai-rex" / "skills", "b", "dup", "from home")
    make_skill(home / ".config" / "ai-rex" / "skills", "c", "only-home", "h")
    make_skill(extra, "d", "from-config", "cfg")
    warnings: list[str] = []
    skills = discover_skills(ws, Cfg({"skills.paths": [str(extra)], "workspace.trust": True}), home=home, warnings=warnings)
    by = {s.name: s for s in skills}
    assert by["dup"].description == "from home"
    assert set(by) == {"dup", "only-home", "from-config"}
    assert any("dup" in w and "ignored" in w for w in warnings)


def test_files_listed_without_skill_md(env):
    ws, home = env
    d = make_skill(ws / ".ai-rex" / "skills", "s", "s", "d")
    (d / "references").mkdir()
    (d / "references" / "api.md").write_text("api", encoding="utf-8")
    (d / "run.sh").write_text("echo", encoding="utf-8")
    (skill,) = discover_skills(ws, TRUST, home=home)
    assert skill.files == ["references/api.md", "run.sh"]


def test_index_format_and_size_cap(env):
    ws, home = env
    root = ws / ".ai-rex" / "skills"
    make_skill(root, "short", "short", "tiny")
    make_skill(root, "long", "long", "x" * 1000)
    idx = skill_index(discover_skills(ws, TRUST, home=home))
    assert "- short: tiny (project)" in idx
    longline = next(l for l in idx.splitlines() if l.startswith("- long:"))
    assert len(longline) < 260 and longline.endswith("... (project)")
    many = [Skill(f"s{i:03d}", "d" * 150, Path("."), "", []) for i in range(100)]
    big = skill_index(many)
    assert len(big.encode("utf-8")) <= 2048
    assert "more skills not listed" in big
    assert skill_index([]) == ""


def test_load_skill_tool(env):
    ws, home = env
    d = make_skill(ws / ".ai-rex" / "skills", "s", "s", "d", body="Step 1\n")
    (d / "notes.txt").write_text("n", encoding="utf-8")
    tool = LoadSkillTool(skills=discover_skills(ws, TRUST, home=home))
    r = tool.execute(name="s")
    assert r["ok"] and r["instructions"] == "Step 1\n" and r["files"] == ["notes.txt"]
    bad = tool.execute(name="zzz")
    assert not bad["ok"] and "available: s" in bad["error"]


def test_read_skill_file_and_confinement(env, tmp_path):
    ws, home = env
    d = make_skill(ws / ".ai-rex" / "skills", "s", "s", "d")
    (d / "ref.md").write_text("hello", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    (d / "bin.dat").write_bytes(b"\x00\x01")
    (d / "big.txt").write_text("a" * (FILE_SIZE_CAP + 1), encoding="utf-8")
    skills = discover_skills(ws, TRUST, home=home)
    tool = ReadSkillFileTool(skills=skills)
    assert tool.execute(name="s", relpath="ref.md") == {"ok": True, "content": "hello"}
    for bad in ("../../../secret.txt", "..\\..\\secret.txt", str(tmp_path / "secret.txt"),
                "/etc/passwd", "C:/Windows/win.ini", "~/x", "sub/../../x"):
        assert not tool.execute(name="s", relpath=bad)["ok"], bad
    assert "binary" in tool.execute(name="s", relpath="bin.dat")["error"]
    assert "too large" in tool.execute(name="s", relpath="big.txt")["error"]
    assert not tool.execute(name="s", relpath="missing.md")["ok"]
    with pytest.raises(PermissionError):
        resolve_skill_file(skills[0], "../x")
    with pytest.raises(FileNotFoundError):
        read_skill_file(skills[0], "nope")


def test_symlink_escape_rejected(env, tmp_path):
    ws, home = env
    d = make_skill(ws / ".ai-rex" / "skills", "s", "s", "d")
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    try:
        (d / "link.txt").symlink_to(tmp_path / "secret.txt")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    (skill,) = discover_skills(ws, TRUST, home=home)
    with pytest.raises(PermissionError):
        read_skill_file(skill, "link.txt")


def test_cli_list_and_show(env, monkeypatch):
    ws, home = env
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    make_skill(ws / ".ai-rex" / "skills", "s", "alpha", "does alpha", body="ALPHA BODY\n")
    out, err = io.StringIO(), io.StringIO()
    assert run_skills([], TRUST, workspace=ws, out=out, err=err) == 0
    line = out.getvalue().strip()
    assert line.split("\t")[0] == "alpha" and "does alpha" in line and str(ws) in line
    out = io.StringIO()
    assert run_skills(["show", "alpha"], TRUST, workspace=ws, out=out, err=err) == 0
    assert "ALPHA BODY" in out.getvalue()
    assert run_skills(["show", "nope"], None, workspace=ws, out=out, err=err) == 1
    assert run_skills(["bogus"], None, workspace=ws, out=out, err=err) == 2


def test_agent_prompt_has_index_only_when_skills_exist(env, monkeypatch):
    from runtimelib.agent.loop import AgentLoop
    ws, home = env
    monkeypatch.chdir(ws)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    make_skill(ws / ".ai-rex" / "skills", "s", "alpha", "does alpha")
    cfg = TRUST
    loop = AgentLoop.__new__(AgentLoop)
    loop.config = cfg
    loop.tools = {}
    loop.read_tracker = None  # built without __init__; tools fall back to the default tracker
    loop.state = type("S", (), {"max_steps": 5})()
    loop._init_default_tools()
    assert "load_skill" in loop.tools and "read_skill_file" in loop.tools
    prompt = loop._build_system_prompt(include_tools=True)
    assert "- alpha: does alpha (project)" in prompt and "call load_skill" in prompt
    assert "untrusted" in prompt


def test_workspace_skills_untrusted_by_default(env, monkeypatch):
    ws, home = env
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    make_skill(ws / ".ai-rex" / "skills", "s", "proj", "project skill")
    make_skill(home / ".config" / "ai-rex" / "skills", "u", "user", "user skill")
    assert [s.name for s in discover_skills(ws, None, home=home)] == ["user"]
    both = discover_skills(ws, None, home=home, include_untrusted=True)
    assert {s.name: s.trusted for s in both} == {"user": True, "proj": False}
    out, err = io.StringIO(), io.StringIO()
    run_skills([], None, workspace=ws, out=out, err=err)
    assert "(project, not trusted - set workspace.trust)" in out.getvalue()
    out = io.StringIO()
    run_skills([], TRUST, workspace=ws, out=out, err=err)
    assert "project skill (project)" in out.getvalue()
    idx = skill_index(discover_skills(ws, TRUST, home=home))
    assert "- proj: project skill (project)" in idx and "- user: user skill" in idx
    assert "user skill (project)" not in idx


def test_folder_swapped_for_symlink_after_discovery_refused(env, tmp_path):
    ws, home = env
    d = make_skill(ws / ".ai-rex" / "skills", "s", "s", "d")
    (d / "ref.md").write_text("ok", encoding="utf-8")
    other = tmp_path / "other"
    other.mkdir()
    (other / "ref.md").write_text("evil", encoding="utf-8")
    (skill,) = discover_skills(ws, TRUST, home=home)
    assert read_skill_file(skill, "ref.md") == "ok"
    for f in d.iterdir():
        f.unlink()
    d.rmdir()
    try:
        d.symlink_to(other, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    with pytest.raises(PermissionError):
        read_skill_file(skill, "ref.md")


def test_project_skill_symlinked_outside_container_skipped(env, tmp_path):
    ws, home = env
    other = make_skill(tmp_path / "elsewhere", "s", "s", "d")
    root = ws / ".ai-rex" / "skills"
    root.mkdir(parents=True)
    try:
        (root / "s").symlink_to(other, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    warnings: list[str] = []
    assert discover_skills(ws, TRUST, home=home, warnings=warnings) == []
    assert any("outside" in w for w in warnings)


def test_load_skill_body_capped():
    body = "a" * (FILE_SIZE_CAP + 5000)
    tool = LoadSkillTool(skills=[Skill("big", "d", Path("."), body, [])])
    r = tool.execute(name="big")
    assert len(r["instructions"].encode("utf-8")) < FILE_SIZE_CAP + 50 and r["instructions"].endswith("[truncated]")
