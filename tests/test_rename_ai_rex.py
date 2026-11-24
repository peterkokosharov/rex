# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-sonnet-5-5
# Licensed under MIT License

"""The product is called ai-rex: no installed command, launcher or package file may be named plain `rex`."""

from __future__ import annotations

import os
import re
import shutil
import tomllib
from pathlib import Path

from runtimelib.config import paths

ROOT = Path(__file__).resolve().parents[1]


def test_pyproject_names_are_ai_rex():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["name"] == "ai-rex"
    scripts = data["project"]["scripts"]
    assert "ai-rex" in scripts
    assert "rex" not in scripts


def test_packaging_scripts_never_install_plain_rex():
    deb = (ROOT / "packaging" / "build-deb.sh").read_text(encoding="utf-8")
    win = (ROOT / "packaging" / "build-win-zip.sh").read_text(encoding="utf-8")
    ci = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
    assert "usr/bin/ai-rex" in deb and "Package: ai-rex" in deb
    # plain `rex` as a path component, package name, file name or archive name (not part of ai-rex)
    plain = re.compile(r"(?<![\w-])rex(?:\.cmd|_\$\{VERSION\}|-win\.zip|_\*\.deb)?(?=[\"'/\s]|$)")
    for name, text in (("build-deb.sh", deb), ("build-win-zip.sh", win), ("build.yml", ci)):
        for line in text.splitlines():
            if line.lstrip().startswith("#"):
                continue
            for m in plain.finditer(line):
                # allow only prose-free structural hits; any hit in a path/package context is a failure
                if name == "build-win-zip.sh" and "$STAGE/rex" in line:
                    continue  # the zip's inner folder stays `rex`: Layka and Sisi look for that name
                if re.search(r"(usr/bin/|doc/|Package: |dist/|\$STAGE/|\.cmd|\.deb|\.zip)", line):
                    raise AssertionError(f"{name}: plain `rex` in: {line.strip()}")


def test_no_tracked_file_is_named_plain_rex():
    for p in ROOT.rglob("*"):
        if "vendor" in p.parts or ".git" in p.parts or ".venv" in p.parts or "runtime.egg-info" in p.parts:
            continue
        if p.is_file() and p.name in {"rex", "rex.cmd", "rex.exe", "rex.sh"}:
            raise AssertionError(f"file named plain rex: {p}")


def test_config_dir_is_ai_rex(tmp_path):
    assert paths.config_dir(tmp_path) == tmp_path / ".config" / "ai-rex"


def test_migration_copies_old_dir_and_keeps_it(tmp_path):
    old = tmp_path / ".config" / "runtime"
    old.mkdir(parents=True)
    (old / "config.yaml").write_text("model: x\n", encoding="utf-8")
    assert paths.migrate_legacy_config(tmp_path) is True
    assert (tmp_path / ".config" / "ai-rex" / "config.yaml").read_text(encoding="utf-8") == "model: x\n"
    assert (old / "config.yaml").exists()  # never deleted
    assert paths.migrate_legacy_config(tmp_path) is False  # one-time


def test_migration_skipped_when_new_dir_exists(tmp_path):
    (tmp_path / ".config" / "runtime").mkdir(parents=True)
    (tmp_path / ".config" / "runtime" / "a").write_text("old", encoding="utf-8")
    (tmp_path / ".config" / "ai-rex").mkdir()
    (tmp_path / ".config" / "ai-rex" / "mine.yaml").write_text("new", encoding="utf-8")
    assert paths.migrate_legacy_config(tmp_path) is False
    assert not (tmp_path / ".config" / "ai-rex" / "a").exists()


def test_migration_treats_empty_new_dir_as_missing(tmp_path):
    (tmp_path / ".config" / "runtime").mkdir(parents=True)
    (tmp_path / ".config" / "runtime" / "a").write_text("old", encoding="utf-8")
    (tmp_path / ".config" / "ai-rex" / "sub").mkdir(parents=True)  # directories only, no files
    assert paths.migrate_legacy_config(tmp_path) is True
    assert (tmp_path / ".config" / "ai-rex" / "a").read_text(encoding="utf-8") == "old"
    assert (tmp_path / ".config" / "runtime" / "a").exists()


def test_migration_leftover_temp_dir_and_lost_race(tmp_path, monkeypatch):
    base = tmp_path / ".config"
    (base / "runtime").mkdir(parents=True)
    (base / "runtime" / "a").write_text("old", encoding="utf-8")
    tmp = base / f".ai-rex.tmp-{os.getpid()}"
    tmp.mkdir()
    (tmp / "junk").write_text("partial", encoding="utf-8")  # leftover of a crashed run
    assert paths.migrate_legacy_config(tmp_path) is True
    assert not (base / "ai-rex" / "junk").exists() and (base / "ai-rex" / "a").exists()
    assert not tmp.exists()
    # another process wins the race: replace fails, temp is removed, their dir is kept
    shutil.rmtree(base / "ai-rex")
    real_replace = os.replace

    def racing_replace(src, dst):
        (base / "ai-rex").mkdir()
        (base / "ai-rex" / "theirs").write_text("x", encoding="utf-8")
        raise OSError("exists")

    monkeypatch.setattr(os, "replace", racing_replace)
    assert paths.migrate_legacy_config(tmp_path) is False
    monkeypatch.setattr(os, "replace", real_replace)
    assert (base / "ai-rex" / "theirs").exists() and not tmp.exists()
    assert (base / "runtime" / "a").exists()


def test_deprecation_note_is_silent_in_protocol_mode(monkeypatch, capsys):
    monkeypatch.setenv("RUNTIME_FOO__BAR", "1")
    paths.set_quiet(True)
    paths.env_overrides()
    assert capsys.readouterr().err == ""


def test_env_new_prefix_wins_and_old_is_fallback(monkeypatch, capsys):
    monkeypatch.setenv("RUNTIME_LITELLM__MODEL", "old")
    assert dict(paths.env_overrides())["LITELLM__MODEL"] == "old"
    assert "deprecated" in capsys.readouterr().err
    monkeypatch.setenv("AI_REX_LITELLM__MODEL", "new")
    assert dict(paths.env_overrides())["LITELLM__MODEL"] == "new"
