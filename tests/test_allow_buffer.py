# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# AI models: claude-opus-5-5
# Licensed under MIT License
"""Persistent allow buffer: precedence, workspace confinement, LRU, persistence, corrupt files, REPL commands."""

from __future__ import annotations

import io
import json
import os
import sys
import time
from pathlib import Path

import pytest

import runtime.__main__ as cli
from ai_runtime_rex.core.agent.gate import ALLOW_ALWAYS, ALLOW_ALWAYS_FOLDER, PermissionGate
from ai_runtime_rex.core.permissions import allow_buffer as ab
from ai_runtime_rex.core.permissions.allow_buffer import AllowBuffer, allow_command, parse_size
from ai_runtime_rex.core.permissions.base import PermissionResult
from ai_runtime_rex.core.permissions.rules import PermissionRules
from runtimelib.config import Config

READ_ASK = {"defaults": {"read": "ask"}}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(ab, "_REGISTRY", {})
    monkeypatch.setenv(ab.ENV_STATE_DIR, str(tmp_path / "state"))
    monkeypatch.delenv(ab.ENV_SIZE, raising=False)


@pytest.fixture
def ws(tmp_path):
    w = tmp_path / "ws"
    (w / "src" / "pkg").mkdir(parents=True)
    (w / "src" / "pkg" / "a.py").write_text("x", encoding="utf-8")
    (w / "docs").mkdir()
    (w / "top.txt").write_text("t", encoding="utf-8")
    return w


def _buf(ws, tmp_path, size=1024, name="b.json"):
    return AllowBuffer(ws, tmp_path / "store" / name, size)


def _rules(ws, buf, config=READ_ASK):
    r = PermissionRules(config, str(ws), persist=False)
    r.buffer = buf
    return r


def _read(path):
    return ("fileops", {"operation": "read", "path": path})


# --- basic coverage ---------------------------------------------------------------------------------------

def test_folder_entry_covers_everything_below(ws, tmp_path):
    buf = _buf(ws, tmp_path)
    assert buf.add("read", "src") is not None
    assert buf.covers("read", "src/pkg/a.py")
    assert buf.covers("read", "src")
    assert not buf.covers("read", "docs/x.md")
    assert not buf.covers("write", "src/pkg/a.py")  # kinds are separate
    assert not buf.covers("read", "srcx/a.py")  # a prefix of a name is not a parent


def test_file_entry_covers_only_that_file(ws, tmp_path):
    buf = _buf(ws, tmp_path)
    buf.add("write", "src/pkg/a.py")
    assert buf.covers("write", "src/pkg/a.py")
    assert not buf.covers("write", "src/pkg/b.py")


def test_rules_use_buffer_only_instead_of_the_default_ask(ws, tmp_path):
    buf = _buf(ws, tmp_path)
    rules = _rules(ws, buf)
    assert rules.evaluate(*_read("src/pkg/a.py"), mode="local").decision == PermissionResult.ASK
    buf.add("read", "src")
    v = rules.evaluate(*_read("src/pkg/a.py"), mode="local")
    assert v.decision == PermissionResult.ALLOW and v.source == "buffer"
    # writes, edit
    edit = ("edit", {"path": "src/pkg/a.py", "old_string": "x", "new_string": "y"})
    assert rules.evaluate(*edit, mode="local").decision == PermissionResult.ASK
    buf.add("write", "src/pkg")
    assert rules.evaluate(*edit, mode="local").decision == PermissionResult.ALLOW


def test_default_read_stays_allowed_without_read_ask(ws, tmp_path):
    rules = _rules(ws, _buf(ws, tmp_path), config={})
    assert rules.evaluate(*_read("top.txt"), mode="local").decision == PermissionResult.ALLOW


# --- precedence --------------------------------------------------------------------------------------------

def test_deny_rule_beats_buffer(ws, tmp_path):
    buf = _buf(ws, tmp_path)
    buf.add("read", ".")
    buf.add("write", ".")
    rules = _rules(ws, buf, {"defaults": {"read": "ask"}, "deny": ["fileops(read:src/**)", "edit(docs/**)"]})
    assert rules.evaluate(*_read("src/pkg/a.py"), mode="local").decision == PermissionResult.DENY
    assert rules.evaluate("edit", {"path": "docs/x.md"}, mode="local").decision == PermissionResult.DENY
    assert rules.evaluate(*_read("top.txt"), mode="local").decision == PermissionResult.ALLOW


def test_ask_rule_beats_buffer(ws, tmp_path):
    buf = _buf(ws, tmp_path)
    buf.add("read", ".")
    rules = _rules(ws, buf, {"defaults": {"read": "ask"}, "ask": ["fileops(read:docs/**)"]})
    v = rules.evaluate(*_read("docs/x.md"), mode="local")
    assert v.decision == PermissionResult.ASK and v.source == "ask"


def test_glob_root_is_checked_by_deny_rules(ws, tmp_path):
    buf = _buf(ws, tmp_path)
    buf.add("read", ".")
    rules = _rules(ws, buf, {"defaults": {"read": "ask"}, "deny": ["fileops(glob:src)"]})
    call = {"operation": "glob", "pattern": "*.py", "root": "src"}
    assert rules.evaluate("fileops", call, mode="local").decision == PermissionResult.DENY


def test_buffer_never_allows_a_command(ws, tmp_path):
    buf = _buf(ws, tmp_path)
    buf.add("exec", ".")
    rules = _rules(ws, buf)
    assert rules.evaluate("bash", {"command": "ls"}, mode="local").decision == PermissionResult.ASK


def test_buffer_folder_inside_workspace_is_protected(ws, tmp_path):
    buf = AllowBuffer(ws, ws / ".ai-rex" / "allow-buffer" / "x.json", 10)
    buf.add("write", ".")
    rules = _rules(ws, buf)
    for path in (".ai-rex/allow-buffer/x.json", ".ai-rex/.gitignore", "./.ai-rex"):
        v = rules.evaluate("fileops", {"operation": "write", "path": path, "content": "{}"}, mode="local")
        assert v.decision == PermissionResult.DENY, path
    assert rules.evaluate("fileops", {"operation": "write", "path": "src/n.py"}, mode="local").decision \
        == PermissionResult.ALLOW


# --- workspace confinement ---------------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["..", "../other", "src/../../other", "~/x", "\x00x"])
def test_outside_paths_are_never_stored_or_matched(ws, tmp_path, bad):
    buf = _buf(ws, tmp_path)
    assert buf.add("read", bad) is None
    buf.add("read", ".")
    assert not buf.covers("read", bad)


def test_absolute_path_outside_and_inside(ws, tmp_path):
    buf = _buf(ws, tmp_path)
    buf.add("read", ".")
    assert buf.covers("read", str(ws / "src"))
    assert not buf.covers("read", str(tmp_path / "elsewhere"))
    assert buf.add("read", str(tmp_path)) is None


def test_symlink_out_of_the_workspace_is_not_covered(ws, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "s.txt").write_text("s", encoding="utf-8")
    link = ws / "link"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        if os.name != "nt":
            pytest.skip("symlinks not available")
        import subprocess  # a junction needs no privilege on Windows
        r = subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True)
        if r.returncode != 0:
            pytest.skip("neither symlinks nor junctions available")
    buf = _buf(ws, tmp_path)
    buf.add("read", ".")
    assert not buf.covers("read", "link/s.txt")
    assert buf.add("read", "link") is None


@pytest.mark.skipif(os.name != "nt", reason="Windows drives and UNC paths")
@pytest.mark.parametrize("bad", ["Z:\\data", "Z:data", "\\\\server\\share\\x", "//server/share/x", "\\\\?\\Z:\\x"])
def test_other_drive_and_unc_are_outside(ws, tmp_path, bad):
    buf = _buf(ws, tmp_path)
    buf.add("read", ".")
    assert not buf.covers("read", bad)
    assert buf.add("read", bad) is None


@pytest.mark.skipif(os.name != "nt", reason="case-insensitive file system")
def test_case_insensitive_on_windows(ws, tmp_path):
    buf = _buf(ws, tmp_path)
    buf.add("read", "SRC")
    assert buf.covers("read", "src/PKG/a.py")
    assert buf.covers("read", str(ws).upper() + "\\src\\pkg")
    assert buf.remove("Src") == 1


def test_file_entries_outside_the_workspace_are_dropped_on_load(ws, tmp_path):
    f = tmp_path / "store" / "b.json"
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps({"version": 1, "entries": [["read", str(tmp_path), 1.0],
                                                       ["read", str(ws / "src"), 2.0]]}), encoding="utf-8")
    buf = AllowBuffer(ws, f, 10)
    assert [e[1] for e in buf.entries()] == [os.path.realpath(ws / "src")]
    assert not buf.covers("read", str(tmp_path / "x"))


# --- LRU, size ---------------------------------------------------------------------------------------------

def test_lru_evicts_least_recently_used(ws, tmp_path):
    buf = _buf(ws, tmp_path, size=3)
    for name in ("a", "b", "c"):
        buf.add("read", name)
    assert buf.covers("read", "a/x")  # use refreshes a
    buf.add("read", "d")
    kept = {buf.relative(p) for _, p, _ in buf.entries()}
    assert kept == {"a", "c", "d"}
    assert len(buf) == 3


def test_resize_evicts_and_zero_disables(ws, tmp_path):
    buf = _buf(ws, tmp_path, size=10)
    for name in "abcde":
        buf.add("read", name)
    assert buf.resize(2) == 3
    assert {buf.relative(p) for _, p, _ in buf.entries()} == {"d", "e"}
    buf.resize(0)
    assert not buf.enabled and not buf.covers("read", "e") and buf.add("read", "f") is None


def test_zero_disables_in_the_gate(ws, tmp_path, monkeypatch):
    monkeypatch.chdir(ws)
    cfg = Config({"permissions": {"defaults": {"read": "ask"}, "allow_buffer_size": 0}})
    gate = PermissionGate.from_config(cfg, workspace=str(ws), persist=False)
    gate.set_asker(type("A", (), {"ask": lambda self, r: ALLOW_ALWAYS})())
    assert gate.check(*_read("src/pkg/a.py")).allowed
    assert not gate.rules.buffer.enabled and len(gate.rules.buffer) == 0
    assert not (tmp_path / "state").exists()


@pytest.mark.parametrize("value,ok", [("0", 0), ("1024", 1024), ("1_000_000", 1_000_000), ("-1", None),
                                      ("1000001", None), ("x", None), (True, None)])
def test_parse_size(value, ok):
    if ok is None:
        with pytest.raises(ValueError):
            parse_size(value)
    else:
        assert parse_size(value) == ok


# --- persistence, corrupt file -----------------------------------------------------------------------------

def test_persists_across_sessions(ws, tmp_path):
    buf = _buf(ws, tmp_path)
    buf.add("read", "src")
    buf.add("write", "docs")
    again = _buf(ws, tmp_path)
    assert again.covers("read", "src/pkg/a.py") and again.covers("write", "docs/n.md")
    data = json.loads((tmp_path / "store" / "b.json").read_text(encoding="utf-8"))
    assert data["version"] == 1 and len(data["entries"]) == 2


def test_two_processes_merge_instead_of_overwriting(ws, tmp_path):
    one, two = _buf(ws, tmp_path), _buf(ws, tmp_path)
    one.add("read", "src")
    two.add("read", "docs")
    assert {two.relative(p) for _, p, _ in _buf(ws, tmp_path).entries()} == {"src", "docs"}
    two.remove("src")
    assert {p for _, p, _ in _buf(ws, tmp_path).entries()} == {os.path.realpath(ws / "docs")}


def test_clear_survives_reload(ws, tmp_path):
    buf = _buf(ws, tmp_path)
    buf.add("read", "src")
    assert buf.clear() == 1
    assert len(_buf(ws, tmp_path)) == 0


@pytest.mark.parametrize("text", ["{not json", '{"version": 99, "entries": []}', '[1, 2]',
                                  '{"version": 1, "entries": [[1]]}'])
def test_corrupt_file_warns_keeps_bak_and_starts_empty(ws, tmp_path, text):
    f = tmp_path / "store" / "b.json"
    f.parent.mkdir(parents=True)
    f.write_text(text, encoding="utf-8")
    buf = AllowBuffer(ws, f, 10)
    assert len(buf) == 0 and buf.warnings and "corrupt" in buf.warnings[0]
    assert (tmp_path / "store" / "b.json.bak").read_text(encoding="utf-8") == text
    buf.add("read", "src")
    assert _buf(ws, tmp_path).covers("read", "src")


def test_state_dir_order(ws, tmp_path):
    assert ab.state_dir(ws, {"AI_REX_STATE_DIR": str(tmp_path / "s")}) == tmp_path / "s" / "allow-buffer"
    assert ab.state_dir(ws, {}, install=tmp_path / "inst") == tmp_path / "inst" / "state" / "allow-buffer"
    assert ab.state_dir(ws, {}, detect_install=False) == Path(os.path.realpath(ws)) / ".ai-rex" / "allow-buffer"
    a, b = ab.buffer_file(ws, tmp_path), ab.buffer_file(ws / "src" / "..", tmp_path)
    assert a == b and a.suffix == ".json"


def test_install_dir_detection(tmp_path):
    inst = tmp_path / "inst"
    code = inst / "lib" / "ai_runtime_rex" / "core" / "permissions" / "allow_buffer.py"
    code.parent.mkdir(parents=True)
    code.write_text("", encoding="utf-8")
    assert ab.install_dir(code) is None
    (inst / ".ai-rex-install").write_text("x", encoding="utf-8")
    assert ab.install_dir(code) == inst.resolve()


def test_workspace_fallback_gets_gitignore(ws, tmp_path):
    buf = AllowBuffer(ws, ws / ".ai-rex" / "allow-buffer" / "x.json", 10)
    buf.add("read", "src")
    assert (ws / ".ai-rex" / ".gitignore").read_text(encoding="utf-8") == "*\n"


def test_touch_is_flushed_later(ws, tmp_path, monkeypatch):
    buf = _buf(ws, tmp_path)
    buf.add("read", "src")
    before = json.loads((tmp_path / "store" / "b.json").read_text(encoding="utf-8"))["entries"][0][2]
    time.sleep(0.01)
    buf.covers("read", "src/x")
    buf.flush()
    after = json.loads((tmp_path / "store" / "b.json").read_text(encoding="utf-8"))["entries"][0][2]
    assert after > before


# --- gate: "always" goes to the buffer ---------------------------------------------------------------------

class _Asker:
    def __init__(self, answer):
        self.answer = answer
        self.requests = []

    def ask(self, request):
        self.requests.append(request)
        return self.answer


def _gate(ws, answer, perms=None):
    cfg = Config({"permissions": {**(perms or READ_ASK), "allow_buffer_size": 1024}, "cli": {"unsafe_local_tools": True}})
    gate = PermissionGate.from_config(cfg, workspace=str(ws), persist=False)
    asker = _Asker(answer)
    gate.set_asker(asker)
    return gate, asker


def test_always_is_remembered_in_the_buffer_and_survives_a_restart(ws, monkeypatch):
    monkeypatch.chdir(ws)
    gate, asker = _gate(ws, ALLOW_ALWAYS)
    assert gate.check("fileops", {"operation": "list", "path": "src"}).allowed
    assert asker.requests[0].remember == "read src"
    assert gate.rules.allow == []  # no exact rule: the buffer holds it
    monkeypatch.setattr(ab, "_REGISTRY", {})  # a new process
    gate2, asker2 = _gate(ws, "deny")
    assert gate2.check(*_read("src/pkg/a.py")).allowed and not asker2.requests
    assert not gate2.check(*_read("top.txt")).allowed  # outside the approved folder: asks (denied here)
    assert not gate2.check(*_read(str(ws.parent / "x.txt"))).allowed  # outside the workspace: asks


def test_always_for_the_folder(ws, monkeypatch):
    monkeypatch.chdir(ws)
    gate, asker = _gate(ws, ALLOW_ALWAYS_FOLDER)
    assert gate.check(*_read("src/pkg/a.py")).allowed
    assert asker.requests[0].remember_folder == "src/pkg"
    asker.answer = "deny"
    assert gate.check(*_read("src/pkg/b.py")).allowed
    assert len(asker.requests) == 1


def test_always_outside_the_workspace_is_session_only(ws, monkeypatch):
    monkeypatch.chdir(ws)
    gate, asker = _gate(ws, ALLOW_ALWAYS)
    out = str(ws.parent / "o.txt")
    assert gate.check(*_read(out)).allowed
    assert asker.requests[0].remember == ""
    assert len(gate.rules.buffer) == 0 and gate.rules.allow  # exact rule for this process only


def test_always_for_a_command_records_its_folder_but_keeps_command_rules(ws, monkeypatch):
    monkeypatch.chdir(ws)
    gate, asker = _gate(ws, ALLOW_ALWAYS)
    assert gate.check("bash", {"command": "ls src", "cwd": "src"}).allowed
    assert [(k, gate.rules.buffer.relative(p)) for k, p, _ in gate.rules.buffer.entries()] == [("exec", "src")]
    assert any(r.text == "bash(ls src)" for r in gate.rules.allow)
    asker.answer = "deny"
    assert not gate.check("bash", {"command": "rm x", "cwd": "src"}).allowed


def test_buffer_is_shared_by_gates_of_one_process(ws, monkeypatch):
    monkeypatch.chdir(ws)
    g1, _ = _gate(ws, ALLOW_ALWAYS)
    g2, a2 = _gate(ws, "deny")
    g1.check("fileops", {"operation": "list", "path": "docs"})
    assert g2.check(*_read("docs/n.md")).allowed and not a2.requests


# --- CLI: settings, /allow, --allow-buffer-list, prompt ----------------------------------------------------

def test_size_precedence_flag_env_config_default():
    cfg = Config({})
    cli.apply_allow_buffer(cfg, None, env_only=True, environ={})
    assert cfg.get("permissions.allow_buffer_size") == 1024
    cfg = Config({"permissions": {"allow_buffer_size": 7}})
    cli.apply_allow_buffer(cfg, None, env_only=False, environ={})
    assert cfg.get("permissions.allow_buffer_size") == 7
    cli.apply_allow_buffer(cfg, None, env_only=False, environ={"AI_REX_ALLOW_BUFFER": "9"})
    assert cfg.get("permissions.allow_buffer_size") == 9
    cli.apply_allow_buffer(cfg, "0", env_only=False, environ={"AI_REX_ALLOW_BUFFER": "9"})
    assert cfg.get("permissions.allow_buffer_size") == 0
    cfg = Config({})
    cli.apply_allow_buffer(cfg, None, env_only=False, environ={})
    assert cfg.get("permissions.allow_buffer_size") is None  # config-file modes: off unless set
    with pytest.raises(ValueError):
        cli.apply_allow_buffer(Config({}), "2000000", environ={})
    with pytest.raises(ValueError, match="AI_REX_ALLOW_BUFFER"):
        cli.apply_allow_buffer(Config({}), None, environ={"AI_REX_ALLOW_BUFFER": "lots"})


def test_allow_commands(ws, tmp_path):
    buf = _buf(ws, tmp_path, size=5)
    buf.add("read", "src")
    buf.add("write", "src")
    buf.add("read", "docs")
    code, lines = allow_command(buf, "list")
    assert code == 0 and "3 of 5" in lines[0] and any("read" in l and l.endswith("docs") for l in lines)
    code, lines = allow_command(buf, "remove write src")
    assert code == 0 and "1 entry" in lines[0]
    code, lines = allow_command(buf, f'remove "{ws / "src"}"')
    assert code == 0 and len(buf) == 1
    assert allow_command(buf, "remove nothing/here")[0] == 1
    seen = []
    code, lines = allow_command(buf, "size 0", set_size=seen.append)
    assert code == 0 and "disabled" in lines[0] and seen == [0]
    assert "disabled" in allow_command(buf, "size")[1][0]
    assert allow_command(buf, "size -3")[0] == 1
    code, lines = allow_command(buf, "clear")
    assert code == 0 and len(buf) == 0
    assert allow_command(buf, "bogus")[0] == 1
    assert allow_command(None, "list")[0] == 1


def test_repl_allow_command_keeps_windows_backslashes(ws, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(ws)
    monkeypatch.setattr(cli, "_REPL_LOOP", None)
    cfg = Config({"permissions": {"allow_buffer_size": 10}})
    buf = cli.current_allow_buffer(cfg)
    buf.add("read", "src")
    assert cli.run_allow_command(cfg, "remove " + str(ws / "src")) == 0
    assert "removed 1" in capsys.readouterr().out
    assert cli.run_allow_command(cfg, "size 3") == 0 and cfg.get("permissions.allow_buffer_size") == 3


def test_allow_buffer_list_flag(ws, monkeypatch, capsys):
    monkeypatch.chdir(ws)
    for k in list(os.environ):
        if k.startswith(("AI_REX_", "LITELLM_")) and k != ab.ENV_STATE_DIR:
            monkeypatch.delenv(k)
    ab.get_buffer(str(ws), 1024).add("read", "src")
    monkeypatch.setattr(sys, "argv", ["ai-rex", "--allow-buffer-list"])
    assert cli.main() == 0
    out = capsys.readouterr().out
    assert "1 of 1024" in out and "src" in out
    monkeypatch.setattr(sys, "argv", ["ai-rex", "--allow-buffer", "x", "--allow-buffer-list"])
    assert cli.main() == 2


def test_prompt_says_always_is_remembered_and_offers_the_folder():
    from ai_runtime_rex.core.agent.gate import PermissionRequest
    out = io.StringIO()
    req = PermissionRequest(id="p", tool="fileops", kind="read", summary="read src/a.py",
                            remember="read src/a.py", remember_folder="src")
    asker = cli.TerminalAsker(input_fn=lambda prompt: (out.write(prompt), "f")[1], out=out)
    assert asker.ask(req) == "allow_always_folder"
    text = out.getvalue()
    assert "remembered in the allow buffer: read src/a.py" in text and "[f] always for src/" in text
    req.remember_folder = ""
    asker = cli.TerminalAsker(input_fn=lambda prompt: "f", out=io.StringIO())
    assert asker.ask(req) == "deny"  # no folder offered: f is not an answer


# --- performance --------------------------------------------------------------------------------------------

def test_large_buffer_loads_and_looks_up_fast(ws, tmp_path):
    n = 100_000
    f = tmp_path / "store" / "big.json"
    f.parent.mkdir(parents=True)
    base = os.path.realpath(ws)
    entries = [["read", os.path.join(base, "d%03d" % (i % 997), "f%06d.txt" % i), float(i)] for i in range(n)]
    f.write_text(json.dumps({"version": 1, "entries": entries}), encoding="utf-8")
    t = time.perf_counter()
    buf = AllowBuffer(ws, f, ab.MAX_SIZE)
    load = time.perf_counter() - t
    assert len(buf) == n
    t = time.perf_counter()
    for i in range(1000):
        assert buf.covers("read", "d%03d/f%06d.txt" % (i % 997, i), touch=False)
    look = time.perf_counter() - t
    assert load < 10 and look < 5
