# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-opus-5-5
# Licensed under MIT License

"""Layered agent instructions (profiles), the default instruction in requests, built-in task skills."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from runtimelib.config import Config
from runtimelib.llm.client import ChatCompletionChunk
from ai_runtime_rex.core.profiles import BUILTIN_DIR, build_profile, profile_from_agents_md
from ai_runtime_rex.core.profiles.cli import run_profile

BASE = {"liteLLM": {"base_url": "http://d:4000", "api_key": "k", "model": "m"}}
TRUST = {"workspace": {"trust": True}}


def cfg(**extra):
    data = dict(BASE)
    for k, v in extra.items():
        data[k] = v
    return Config(data)


def layer(root: Path, path: str, body: str, meta: str = "") -> None:
    d = root / path if path else root
    d.mkdir(parents=True, exist_ok=True)
    (d / "LAYER.md").write_text(f"---\ndescription: test\n{meta}---\n{body}\n", encoding="utf-8")


@pytest.fixture
def ws(tmp_path):
    d = tmp_path / "ws"
    d.mkdir()
    return d


def home(tmp_path) -> Path:
    return tmp_path / "_home"  # conftest points HOME here


def user_agents(tmp_path) -> Path:
    return home(tmp_path) / ".config" / "ai-rex" / "agents"


# --- composition and detection ---------------------------------------------------------------------------

def test_builtin_tree_has_expected_layers():
    for rel in ("LAYER.md", "programming/LAYER.md", "programming/java/LAYER.md", "programming/python/LAYER.md"):
        assert (BUILTIN_DIR / rel).is_file(), rel


def test_empty_workspace_gets_base_only(ws):
    p = build_profile(ws, cfg())
    assert p.paths == [""] and p.label() == "base" and p.origin == "auto"
    assert "Explore" in p.text() and "fileops" not in p.text()


def test_java_detected_and_composed_root_to_leaf(ws):
    (ws / "pom.xml").write_text("<project/>", encoding="utf-8")
    p = build_profile(ws, cfg())
    assert p.paths == ["", "programming", "programming/java"]
    text = p.text()
    assert text.index("You are ai-rex") < text.index("Working in a code project") < text.index("Java projects")
    assert "surefire-reports" in text and "Python projects" not in text


def test_mixed_repo_gets_both_leaves_once(ws):
    (ws / "pom.xml").write_text("<project/>", encoding="utf-8")
    (ws / "requirements-dev.txt").write_text("pytest\n", encoding="utf-8")
    p = build_profile(ws, cfg())
    assert p.paths == ["", "programming", "programming/java", "programming/python"]
    assert p.text().count("Working in a code project") == 1


def test_git_folder_means_programming(ws):
    (ws / ".git").mkdir()
    assert build_profile(ws, cfg()).paths == ["", "programming"]


# --- selection: flag/config, env, AGENTS.md ----------------------------------------------------------------

def test_flag_overrides_detection(ws):
    (ws / "pom.xml").write_text("<project/>", encoding="utf-8")
    p = build_profile(ws, cfg(agent={"profile": "programming/python"}))
    assert p.paths == ["", "programming", "programming/python"] and p.origin == "flag/config"
    assert build_profile(ws, cfg(agent={"profile": "base"})).paths == [""]
    both = build_profile(ws, cfg(agent={"profile": "auto,programming/python"}))
    assert both.paths == ["", "programming", "programming/java", "programming/python"]


def test_env_fallback_and_flag_wins(ws):
    env = {"AI_REX_PROFILE": "programming/java"}
    assert build_profile(ws, cfg(), environ=env).paths[-1] == "programming/java"
    p = build_profile(ws, cfg(agent={"profile": "programming/python"}), environ=env)
    assert p.paths[-1] == "programming/python"


def test_agents_md_profile_line(ws):
    assert profile_from_agents_md("# x\nai-rex-profile: programming/java\n") == "programming/java"
    assert profile_from_agents_md("<!-- ai-rex-profile: programming/python -->") == "programming/python"
    p = build_profile(ws, cfg(), agents_md="ai-rex-profile: programming/java", environ={})
    assert p.paths[-1] == "programming/java" and p.origin == "AGENTS.md"
    flag = build_profile(ws, cfg(agent={"profile": "base"}), agents_md="ai-rex-profile: programming/java")
    assert flag.paths == [""]


def test_unknown_layer_warns_but_keeps_ancestors(ws):
    p = build_profile(ws, cfg(agent={"profile": "programming/cobol"}))
    assert p.paths == ["", "programming"] and any("cobol" in w for w in p.warnings)


# --- user and project layers -------------------------------------------------------------------------------

def test_user_layer_replaces_extends_and_adds(ws, tmp_path):
    ua = user_agents(tmp_path)
    layer(ua, "programming/java", "MY-JAVA-RULES")
    layer(ua, "programming", "MORE-PROGRAMMING", meta="extend: true\n")
    layer(ua, "programming/java/spring", "SPRING-RULES", meta="detect: application.yml\n")
    (ws / "pom.xml").write_text("<project/>", encoding="utf-8")
    text = build_profile(ws, cfg()).text()
    assert "MY-JAVA-RULES" in text and "Java projects" not in text  # replaced
    assert text.index("Working in a code project") < text.index("MORE-PROGRAMMING")  # extended
    assert "SPRING-RULES" not in text
    (ws / "application.yml").write_text("x: 1\n", encoding="utf-8")
    p = build_profile(ws, cfg())
    assert p.paths[-1] == "programming/java/spring" and p.text().endswith("SPRING-RULES")


def test_user_branch_outside_programming(ws, tmp_path):
    layer(user_agents(tmp_path), "writing", "WRITING-RULES")
    p = build_profile(ws, cfg(agent={"profile": "writing"}))
    assert p.paths == ["", "writing"] and "Working in a code project" not in p.text()


def test_project_layers_only_when_trusted(ws):
    layer(ws / ".ai-rex" / "agents", "programming/java", "PROJECT-JAVA")
    (ws / "pom.xml").write_text("<project/>", encoding="utf-8")
    assert "PROJECT-JAVA" not in build_profile(ws, cfg()).text()
    assert "PROJECT-JAVA" in build_profile(ws, cfg(**TRUST)).text()


def test_user_layer_wins_over_project_layer(ws, tmp_path):
    layer(ws / ".ai-rex" / "agents", "programming/java", "PROJECT-JAVA")
    layer(user_agents(tmp_path), "programming/java", "USER-JAVA")
    (ws / "pom.xml").write_text("<project/>", encoding="utf-8")
    text = build_profile(ws, cfg(**TRUST)).text()
    assert "USER-JAVA" in text and "PROJECT-JAVA" not in text


def test_profile_cli_lists_and_shows(ws):
    (ws / "pom.xml").write_text("<project/>", encoding="utf-8")
    out = io.StringIO()
    assert run_profile([], cfg(), workspace=ws, out=out, err=io.StringIO()) == 0
    listing = out.getvalue()
    assert "Active: programming, programming/java" in listing and "* " in listing
    out = io.StringIO()
    assert run_profile(["show"], cfg(), workspace=ws, out=out, err=io.StringIO()) == 0
    assert "Java projects" in out.getvalue()


# --- the agent request -------------------------------------------------------------------------------------

class _Llm:
    default_model = "m"

    def __init__(self):
        self.calls = []

    def chat_completion(self, **kw):
        self.calls.append(kw)
        return iter([ChatCompletionChunk(choices=[{"delta": {"content": "ok"}, "finish_reason": "stop"}])])


def _request_system(config) -> str:
    from runtimelib.agent import create_agent_loop
    loop = create_agent_loop(config)
    loop.llm = _Llm()
    loop.run("look at the project")
    msgs = loop.llm.calls[0]["messages"]
    assert msgs[0].role == "system"
    return msgs[0].content


def test_default_instruction_is_sent_to_the_model(tmp_path):
    (tmp_path / "pom.xml").write_text("<project/>", encoding="utf-8")  # cwd is tmp_path (conftest)
    system = _request_system(cfg())
    assert "You are ai-rex" in system and "Keep going" in system
    assert "Java projects" in system and "Active profile: programming, programming/java" in system
    assert "Read a file before editing it" in system and "load_skill" in system
    assert "review-project" in system and "java-maven" in system


def test_trusted_agents_md_reaches_the_model_and_picks_profile(tmp_path):
    (tmp_path / "AGENTS.md").write_text("Build: make all\nai-rex-profile: programming/python\n", encoding="utf-8")
    system = _request_system(cfg(**TRUST))
    assert "Build: make all" in system and "Python projects" in system
    assert system.index("You are ai-rex") < system.index("Build: make all")


# --- built-in task skills ----------------------------------------------------------------------------------

def test_task_skills_listed_and_loadable(tmp_path):
    from runtimelib.skills import discover_skills, skill_index
    from runtimelib.tools.skills import LoadSkillTool
    skills = discover_skills(tmp_path, cfg())
    names = {s.name for s in skills}
    assert {"review-project", "java-maven", "python-project", "code-review"} <= names
    idx = skill_index(skills)
    assert "more skills not listed" not in idx  # every built-in fits in the index
    tool = LoadSkillTool(skills=skills)
    assert "surefire-reports" in tool.execute(name="java-maven")["instructions"]
    assert "Not checked" in tool.execute(name="review-project")["instructions"]
    assert "pytest" in tool.execute(name="python-project")["instructions"]
