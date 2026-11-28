# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# AI models: claude-opus-5-5
# Licensed under MIT License
"""Default shell per OS (``--shell auto``), the login shell first on POSIX, the install-time record on Windows,
run-time precedence, and that the model is told the shell and its syntax family."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest

import runtime.__main__ as cli
from ai_runtime_rex.core.tools import bash as bash_mod
from ai_runtime_rex.core.tools.bash import Shell, describe, login_shell, posix_default, resolve_shell, shell_family
from ai_runtime_rex.runtime import shellsetup as ss
from runtimelib.config import Config


def _files(*paths):
    present = {p.replace("\\", "/") for p in paths}
    return lambda p: str(p).replace("\\", "/") in present


# --- syntax family -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("path, family", [
    ("/bin/sh", "sh"), ("/usr/bin/dash", "sh"), ("/bin/ash", "sh"), ("/bin/bash", "bash"),
    ("/usr/local/bin/bash", "bash"), ("/bin/zsh", "zsh"), ("/bin/ksh", "ksh"), ("/usr/bin/mksh", "ksh"),
    ("/usr/bin/fish", "fish"), ("/bin/tcsh", "csh"), ("/bin/csh", "csh"), ("/opt/weird", "sh"),
])
def test_family_from_basename(path, family):
    assert shell_family(path) == family


@pytest.mark.parametrize("family, words", [
    ("sh", "POSIX sh"), ("bash", "bash syntax"), ("zsh", "zsh syntax"), ("ksh", "Korn shell"),
    ("fish", "not a POSIX shell"), ("csh", "C shell"),
])
def test_note_names_shell_and_family(family, words, monkeypatch):
    monkeypatch.setattr(bash_mod.os, "name", "posix")
    shell = Shell(family, f"/x/{family}", family)
    assert words in shell.note() and f"/x/{family}" in shell.note()
    assert shell.note() in describe(shell)


def test_fish_and_csh_warn():
    assert "WARNING" in Shell("fish", "/usr/bin/fish", "fish").note()
    assert "WARNING" in Shell("csh", "/bin/tcsh", "tcsh").note()


# --- POSIX: login shell first, then the per-OS table --------------------------------------------------------

def test_login_shell_must_be_absolute_and_executable():
    ok = _files("/usr/local/bin/zsh")
    assert login_shell({"SHELL": "/usr/local/bin/zsh"}, isfile=ok, executable=ok) == "/usr/local/bin/zsh"
    assert login_shell({"SHELL": "zsh"}, isfile=ok, executable=ok) is None
    assert login_shell({"SHELL": "/nope/zsh"}, isfile=ok, executable=ok) is None
    assert login_shell({"SHELL": "/usr/local/bin/zsh"}, isfile=ok, executable=lambda p: False) is None
    assert login_shell({}, isfile=ok, executable=ok) is None


@pytest.mark.parametrize("platform, present, expected", [
    ("freebsd14", ["/bin/sh", "/usr/local/bin/bash"], "/bin/sh"),
    ("openbsd7", ["/bin/sh", "/bin/ksh"], "/bin/sh"),
    ("netbsd10", ["/bin/sh"], "/bin/sh"),
    ("linux", ["/usr/bin/bash", "/bin/sh"], "/usr/bin/bash"),
    ("linux", ["/bin/sh"], "/bin/sh"),
    ("darwin", ["/bin/zsh", "/bin/bash", "/bin/sh"], "/bin/zsh"),
    ("darwin", ["/bin/bash", "/bin/sh"], "/bin/bash"),
    ("darwin", ["/bin/sh"], "/bin/sh"),
])
def test_posix_table(platform, present, expected):
    which = lambda name: next((p for p in present if p.rsplit("/", 1)[-1] == name), None)  # noqa: E731
    assert posix_default(platform, which=which, isfile=_files(*present)) == expected


@pytest.mark.parametrize("platform", ["linux", "darwin", "freebsd14"])
def test_auto_on_posix_prefers_login_shell(platform):
    ok = _files("/usr/local/bin/fish", "/bin/sh")
    shell = resolve_shell("auto", nt=False, environ={"SHELL": "/usr/local/bin/fish"}, platform=platform,
                          isfile=ok, executable=ok, which=lambda n: None)
    assert shell.kind == "fish" and shell.path == "/usr/local/bin/fish" and "WARNING" in shell.note()


def test_auto_on_posix_falls_back_to_table_when_shell_invalid():
    ok = _files("/bin/sh")
    shell = resolve_shell("auto", nt=False, environ={"SHELL": "/gone/zsh"}, platform="freebsd14",
                          isfile=ok, executable=ok, which=lambda n: None)
    assert shell.kind == "sh" and shell.path == "/bin/sh" and "POSIX sh" in shell.note()


def test_posix_command_line_uses_dash_c(monkeypatch):
    monkeypatch.setattr(bash_mod.os, "name", "posix")
    for kind in ("sh", "bash", "zsh", "ksh", "fish", "csh"):
        assert Shell(kind, "/bin/x", "x").command_line("echo hi", {}) == ["/bin/x", "-c", "echo hi"]


# --- Windows: SHELL only when it is an existing bash.exe, then Git Bash, PowerShell, cmd ------------------

def test_windows_honours_shell_only_for_existing_bash_exe():
    ok = _files("D:/Git/bin/bash.exe", "D:/tools/zsh.exe")
    ps = lambda: ("C:/ps/powershell.exe", "Windows PowerShell 5.1")  # noqa: E731
    got = resolve_shell("auto", nt=True, environ={"SHELL": "D:/Git/bin/bash.exe"}, isfile=ok,
                        bash=lambda: None, powershell=ps, cmd=lambda: None)
    assert got.kind == "bash" and got.path == "D:/Git/bin/bash.exe"
    for value in ("/usr/bin/bash", "D:/tools/zsh.exe", "C:/Windows/System32/bash.exe"):
        got = resolve_shell("auto", nt=True, environ={"SHELL": value}, isfile=_files(value, "D:/tools/zsh.exe"),
                            bash=lambda: None, powershell=ps, cmd=lambda: None)
        assert got.kind == "powershell", value


def test_recorded_path_is_used_when_it_exists():
    ok = _files("E:/PortableGit/bin/bash.exe")
    got = resolve_shell("bash", nt=True, path="E:/PortableGit/bin/bash.exe", isfile=ok, bash=lambda: None)
    assert got.kind == "bash" and got.path == "E:/PortableGit/bin/bash.exe"


# --- installer: detection, question, record -----------------------------------------------------------------

ENV = {"LOCALAPPDATA": "C:/Users/u/AppData/Local", "ProgramFiles": "C:/Program Files", "SystemRoot": "C:/Windows"}
PS51 = "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
CMD = "C:/Windows/System32/cmd.exe"


@pytest.mark.parametrize("which_bash, files, expected", [
    ("C:/Git/bin/bash.exe", [], "C:/Git/bin/bash.exe"),
    ("C:/Windows/System32/bash.exe", ["C:/Program Files/Git/bin/bash.exe"], "C:/Program Files/Git/bin/bash.exe"),
    (None, ["C:/Users/u/AppData/Local/Programs/Git/bin/bash.exe"],
     "C:/Users/u/AppData/Local/Programs/Git/bin/bash.exe"),
    (None, [], None),
    ("D:/Git/usr/bin/bash.exe", ["D:/Git/bin/bash.exe"], "D:/Git/bin/bash.exe"),
    ("D:/Git/usr/bin/bash.exe", [], "D:/Git/usr/bin/bash.exe"),
])
def test_find_git_bash(which_bash, files, expected):
    got = ss.find_git_bash(ENV, which=lambda n: which_bash if n == "bash" else None, isfile=_files(*files))
    assert (got.replace("\\", "/") if got else None) == expected


def test_git_bash_found_is_recorded_without_asking():
    asked = []
    tools = ss.choose_shell(None, interactive=True, ask=lambda p: asked.append(p) or "2", environ=ENV,
                            which=lambda n: "C:/Git/bin/bash.exe" if n == "bash" else None, isfile=_files())
    assert tools["shell"] == "bash" and tools["shell_path"] == "C:/Git/bin/bash.exe" and not asked


@pytest.mark.parametrize("answer, kind, path", [("1", "powershell", "C:/pw/pwsh.exe"), ("", "powershell", "C:/pw/pwsh.exe"),
                                                ("2", "cmd", CMD)])
def test_no_git_bash_asks_once(answer, kind, path):
    asked = []
    out = io.StringIO()
    tools = ss.choose_shell(None, interactive=True, ask=lambda p: asked.append(p) or answer, out=out, environ=ENV,
                            which=lambda n: "C:/pw/pwsh.exe" if n == "pwsh" else None, isfile=_files(CMD))
    assert len(asked) == 1 and "1) PowerShell" in out.getvalue() and "2) cmd" in out.getvalue()
    assert tools["shell"] == kind and tools["shell_path"].replace("\\", "/") == path


def test_non_interactive_defaults_to_powershell_51_and_flag_wins():
    tools = ss.choose_shell(None, interactive=False, ask=lambda p: pytest.fail("asked"), environ=ENV,
                            which=lambda n: None, isfile=_files(PS51, CMD))
    assert tools["shell"] == "powershell" and tools["shell_label"] == "Windows PowerShell 5.1"
    tools = ss.choose_shell("cmd", interactive=False, environ=ENV,
                            which=lambda n: "C:/Git/bin/bash.exe" if n == "bash" else None, isfile=_files(CMD))
    assert tools["shell"] == "cmd"
    with pytest.raises(SystemExit):
        ss.choose_shell("git-bash", environ=ENV, which=lambda n: None, isfile=_files())


def test_record_merges_into_settings_file(tmp_path):
    (tmp_path / "ai-rex.json").write_text(json.dumps({"version": 1, "other": 5, "tools": {"x": 1}}), encoding="utf-8")
    ss.record(tmp_path, {"shell": "bash", "shell_path": "C:/Git/bin/bash.exe"})
    data = json.loads((tmp_path / "ai-rex.json").read_text(encoding="utf-8"))
    assert data["other"] == 5 and data["tools"] == {"x": 1, "shell": "bash", "shell_path": "C:/Git/bin/bash.exe"}


def test_setup_records(tmp_path):
    out = io.StringIO()
    tools = ss.setup(tmp_path, None, interactive=False, out=out, force=True, environ=ENV,
                     which=lambda n: None, isfile=_files(PS51))
    assert tools["shell"] == "powershell" and "recorded in" in out.getvalue()
    assert ss.read_settings(tmp_path)["tools"]["shell_path"].replace("\\", "/") == PS51


# --- run time: precedence and a missing recorded shell ------------------------------------------------------

def _install(tmp_path, shell, path):
    (tmp_path / "ai-rex.json").write_text(json.dumps({"version": 1, "tools": {"shell": shell, "shell_path": path}}),
                                          encoding="utf-8")
    return tmp_path


def test_recorded_shell_applied(tmp_path):
    cfg = Config({})
    kind = ss.apply_recorded_shell(cfg, _install(tmp_path, "bash", "E:/G/bin/bash.exe"),
                                   isfile=_files("E:/G/bin/bash.exe"))
    assert kind == "bash" and cfg.get("tools.shell") == "bash" and cfg.get("tools.shell_path") == "E:/G/bin/bash.exe"


def test_missing_recorded_shell_says_so_and_uses_powershell(tmp_path):
    cfg, err = Config({}), io.StringIO()
    kind = ss.apply_recorded_shell(cfg, _install(tmp_path, "bash", "E:/gone/bash.exe"), err=err,
                                   isfile=_files(), ps_finder=lambda: ("C:/pw/pwsh.exe", "PowerShell 7"))
    assert kind == "powershell" and cfg.get("tools.shell") == "powershell"
    assert "recorded shell E:/gone/bash.exe not found; run install again or use --shell" in err.getvalue()


def test_no_record_means_auto(tmp_path):
    cfg = Config({})
    assert ss.apply_recorded_shell(cfg, tmp_path) is None and ss.apply_recorded_shell(cfg, None) is None
    assert cfg.get("tools.shell") is None


@pytest.mark.parametrize("flag, env, config_shell, expected", [
    ("cmd", "bash", "bash", "cmd"),
    (None, "cmd", "bash", "cmd"),
    (None, None, "cmd", "cmd"),
    (None, None, None, "bash"),  # the recorded one
])
def test_run_time_precedence(tmp_path, flag, env, config_shell, expected):
    cfg = Config({"tools": {"shell": config_shell}} if config_shell else {})
    install = _install(tmp_path, "bash", "E:/G/bin/bash.exe")
    cli.apply_shell_choice(cfg, flag, {"AI_REX_SHELL": env} if env else {}, install=install, nt=True,
                           isfile=_files("E:/G/bin/bash.exe"))
    assert cfg.get("tools.shell") == expected
    assert (cfg.get("tools.shell_path") is not None) == (expected == "bash" and not (flag or env or config_shell))


def test_recorded_shell_ignored_off_windows(tmp_path):
    cfg = Config({})
    cli.apply_shell_choice(cfg, None, {}, install=_install(tmp_path, "cmd", "C:/x/cmd.exe"), nt=False,
                           isfile=_files("C:/x/cmd.exe"))
    assert cfg.get("tools.shell") is None
