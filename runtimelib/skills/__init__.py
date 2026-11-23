# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-sonnet-5-5
# Licensed under MIT License

"""Claude-style skills: a folder with SKILL.md (frontmatter + Markdown body) and optional extra files.

Only a compact index (name + description) goes into the system prompt; the model loads a skill's
body on demand with ``load_skill`` and reads its extra files with ``read_skill_file``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

SKILL_FILE = "SKILL.md"
INDEX_CAP_BYTES = 2048
DESC_MAX_CHARS = 200
FILE_SIZE_CAP = 200 * 1024
MAX_LISTED_FILES = 200
_TAIL_RESERVE = 48  # room for the "(+N more skills not listed)" line

log = logging.getLogger("rex.skills")


@dataclass
class Skill:
    name: str
    description: str
    path: Path  # skill folder
    body: str = ""
    files: list[str] = field(default_factory=list)  # relative POSIX paths, SKILL.md excluded


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split ``---`` frontmatter (simple ``key: value`` lines) from the body. Unknown keys are kept.

    Quoted values lose their quotes; ``>`` / ``|`` block values are joined from the indented lines.
    Without frontmatter the whole text is the body.
    """
    text = text.lstrip("﻿").replace("\r\n", "\n")
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}, text
    meta: dict[str, str] = {}
    i = 1
    while i < end:
        raw = lines[i]
        i += 1
        if not raw.strip() or raw.lstrip().startswith("#") or raw[0] in " \t" or ":" not in raw:
            continue
        key, _, value = raw.partition(":")
        key, value = key.strip(), value.strip()
        if value in (">", "|", ">-", "|-", ">+", "|+"):
            parts = []
            while i < end and (not lines[i].strip() or lines[i][0] in " \t"):
                parts.append(lines[i].strip())
                i += 1
            value = (" " if value[0] == ">" else "\n").join(p for p in parts if p)
        elif len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        meta[key] = value
    body = "\n".join(lines[end + 1:]).lstrip("\n")
    return meta, body


def _list_files(folder: Path) -> list[str]:
    out: list[str] = []
    for p in sorted(folder.rglob("*")):
        if p.is_file() and p != folder / SKILL_FILE:
            out.append(p.relative_to(folder).as_posix())
            if len(out) >= MAX_LISTED_FILES:
                break
    return out


def _warn(msg: str, warnings: list[str] | None) -> None:
    log.warning(msg)
    if warnings is not None:
        warnings.append(msg)


def load_skill_folder(folder: Path, warnings: list[str] | None = None) -> Skill | None:
    """Parse ``folder/SKILL.md``; None (plus a warning) if unreadable or without description."""
    try:
        text = (folder / SKILL_FILE).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        _warn(f"skill {folder}: cannot read {SKILL_FILE}: {exc}", warnings)
        return None
    meta, body = parse_frontmatter(text)
    description = " ".join(meta.get("description", "").split())
    if not description:
        _warn(f"skill {folder}: missing description, skipped", warnings)
        return None
    name = meta.get("name", "").strip() or folder.name
    return Skill(name=name, description=description, path=folder, body=body, files=_list_files(folder))


def search_roots(workspace: Path | str, config: Any = None, home: Path | None = None) -> list[Path]:
    """Skill roots in precedence order: workspace, user config dir, then config ``skills.paths``."""
    home = home if home is not None else Path.home()
    roots = [Path(workspace) / ".rex" / "skills", home / ".config" / "runtime" / "skills"]
    extra = config.get("skills.paths") if config is not None else None
    if isinstance(extra, str):
        extra = [extra]
    if isinstance(extra, (list, tuple)):
        roots.extend(Path(str(p)).expanduser() for p in extra if p)
    return roots


def discover_skills(
    workspace: Path | str,
    config: Any = None,
    home: Path | None = None,
    roots: Iterable[Path] | None = None,
    warnings: list[str] | None = None,
) -> list[Skill]:
    """Find skills in the search roots. A later duplicate name is ignored with a warning."""
    found: dict[str, Skill] = {}
    for root in (list(roots) if roots is not None else search_roots(workspace, config, home)):
        try:
            if not root.is_dir():
                continue
            children = sorted(p for p in root.iterdir() if p.is_dir() and (p / SKILL_FILE).is_file())
        except OSError:
            continue
        for child in children:
            skill = load_skill_folder(child, warnings)
            if skill is None:
                continue
            if skill.name in found:
                _warn(f"skill {skill.name!r} at {child} ignored: already defined at {found[skill.name].path}", warnings)
                continue
            found[skill.name] = skill
    return list(found.values())


def skill_index(skills: list[Skill], cap: int = INDEX_CAP_BYTES) -> str:
    """``- name: description`` lines, descriptions truncated, total size kept within ``cap`` bytes."""
    lines: list[str] = []
    used = 0
    for n, s in enumerate(skills):
        desc = s.description
        if len(desc) > DESC_MAX_CHARS:
            desc = desc[: DESC_MAX_CHARS - 3].rstrip() + "..."
        line = f"- {s.name}: {desc}"
        size = len(line.encode("utf-8")) + 1
        if used + size > cap - _TAIL_RESERVE:
            lines.append(f"- (+{len(skills) - n} more skills not listed)")
            break
        lines.append(line)
        used += size
    return "\n".join(lines)


def resolve_skill_file(skill: Skill, relpath: Any) -> Path:
    """Resolve ``relpath`` inside the skill folder or raise PermissionError / ValueError."""
    if not isinstance(relpath, str) or not relpath.strip():
        raise ValueError("relpath must be a non-empty string")
    if "\x00" in relpath:
        raise PermissionError("path contains a NUL byte")
    norm = relpath.replace("\\", "/")
    if norm.startswith(("/", "~")) or (len(norm) > 1 and norm[1] == ":") or Path(relpath).is_absolute():
        raise PermissionError(f"absolute paths are not allowed: {relpath}")
    if any(seg == ".." for seg in norm.split("/")):
        raise PermissionError(f"'..' is not allowed: {relpath}")
    root = skill.path.resolve()
    resolved = (root / norm).resolve()
    if not resolved.is_relative_to(root):
        raise PermissionError(f"path is outside the skill folder: {relpath}")
    return resolved


def read_skill_file(skill: Skill, relpath: Any) -> str:
    """Text content of one file inside the skill folder (size capped, text only)."""
    path = resolve_skill_file(skill, relpath)
    if not path.is_file():
        raise FileNotFoundError(f"no such file in skill {skill.name!r}: {relpath}")
    size = path.stat().st_size
    if size > FILE_SIZE_CAP:
        raise ValueError(f"file too large ({size} bytes, limit {FILE_SIZE_CAP})")
    data = path.read_bytes()
    if b"\x00" in data:
        raise ValueError("binary file: only text files can be read")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("not valid UTF-8 text") from None


__all__ = [
    "Skill",
    "parse_frontmatter",
    "load_skill_folder",
    "search_roots",
    "discover_skills",
    "skill_index",
    "resolve_skill_file",
    "read_skill_file",
]
