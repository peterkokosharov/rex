# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-opus-5-5
# Licensed under MIT License

"""Layered agent instructions ("profiles"): a tree of small Markdown files, general to specific.

Every layer is a folder with ``LAYER.md`` under an ``agents`` root; its path names it::

    agents/LAYER.md                         ""                   base agent behaviour
    agents/programming/LAYER.md             programming          code, builds, tests, reviews
    agents/programming/java/LAYER.md        programming/java     JDK, Maven, Gradle, surefire

The effective instruction is the concatenation of the active layers, root first, leaf last. A layer is active
when it is asked for (``--profile``, ``agent.profile``, ``AI_REX_PROFILE``, an ``ai-rex-profile:`` line in a
loaded AGENTS.md) or, by default (``auto``), when its ``detect:`` patterns match a name in the workspace root.
Every ancestor of an active layer is active too.

Roots, highest precedence first: ``~/.config/ai-rex/agents``, ``<install folder>/agents``, the trusted
workspace's ``.ai-rex/agents`` (and legacy ``.rex/agents``), the built-in tree. A layer of the same path in a
higher root replaces the lower one, or with ``extend: true`` in its frontmatter is appended to it.
"""

from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from ..skills import parse_frontmatter
from ..trust import trust_workspace

LAYER_FILE = "LAYER.md"
MAX_BYTES = 16 * 1024
BUILTIN_DIR = Path(__file__).resolve().parent / "agents"
INSTALL_MARKER = ".ai-rex-install"
_PROFILE_LINE = re.compile(r"^[ \t>]*(?:<!--)?[ \t]*ai-rex-profile[ \t]*:[ \t]*([^\n]*?)[ \t]*(?:-->)?[ \t]*$",
                           re.IGNORECASE | re.MULTILINE)


_FENCED = re.compile(r"^[ \t]*```.*?^[ \t]*```[^\n]*$", re.MULTILINE | re.DOTALL)


@dataclass
class Layer:
    path: str  # "" for the root, else "programming/java" (POSIX, lowercase as on disk)
    text: str
    sources: list[str] = field(default_factory=list)
    detect: list[str] = field(default_factory=list)
    description: str = ""

    @property
    def depth(self) -> int:
        return 0 if not self.path else self.path.count("/") + 1


@dataclass
class Profile:
    layers: list[Layer]
    requested: str  # the selection text as given ("auto" by default)
    origin: str  # flag/config, env, AGENTS.md, auto
    warnings: list[str] = field(default_factory=list)

    @property
    def paths(self) -> list[str]:
        return [layer.path for layer in self.layers]

    def label(self) -> str:
        names = [p for p in self.paths if p] or ["base"]
        return ", ".join(names)

    def text(self) -> str:
        return "\n\n".join(layer.text for layer in self.layers if layer.text).strip()


def install_dir() -> Optional[Path]:
    """The portable install folder (``DEST`` holding ``.ai-rex-install``), or None when run from a checkout."""
    # DEST/lib/ai_runtime_rex/core/profiles/__init__.py -> DEST
    try:
        dest = Path(__file__).resolve().parents[4]
    except IndexError:
        return None
    return dest if (dest / INSTALL_MARKER).is_file() else None


def layer_roots(workspace: Path | str, config: Any = None, home: Optional[Path] = None,
                include_builtin: bool = True) -> list[tuple[Path, bool]]:
    """(root, is_project) in precedence order, highest first; project roots only for a trusted workspace."""
    home = home if home is not None else Path.home()
    roots: list[tuple[Path, bool]] = [(home / ".config" / "ai-rex" / "agents", False)]
    inst = install_dir()
    if inst is not None:
        roots.append((inst / "agents", False))
    extra = config.get("agent.layer_paths") if config is not None else None
    if isinstance(extra, str):
        extra = [p for p in extra.split(os.pathsep) if p]
    if isinstance(extra, (list, tuple)):
        roots.extend((Path(str(p)).expanduser(), False) for p in extra if p)
    if trust_workspace(config):
        ws = Path(workspace)
        roots.append((ws / ".ai-rex" / "agents", True))
        roots.append((ws / ".rex" / "agents", True))
    if include_builtin:
        roots.append((BUILTIN_DIR, False))
    return roots


def _read_capped(path: Path) -> str:
    with open(path, "rb") as fh:
        data = fh.read(MAX_BYTES + 1)
    text = data[:MAX_BYTES].decode("utf-8", errors="replace").lstrip("﻿")
    return text + ("\n[... truncated: layer exceeds 16 KB ...]" if len(data) > MAX_BYTES else "")


def _split(value: str) -> list[str]:
    return [t for t in re.split(r"[,\s]+", value or "") if t]


def _norm_path(p: str) -> str:
    return "/".join(seg for seg in p.replace("\\", "/").strip("/").split("/") if seg and seg != ".")


def _scan_root(root: Path, is_project: bool) -> dict[str, Path]:
    """layer path -> LAYER.md file for one root (symlinks leading outside a project root are skipped)."""
    found: dict[str, Path] = {}
    try:
        if not root.is_dir():
            return found
        real_root = root.resolve()
        files = sorted(root.rglob(LAYER_FILE))
    except OSError:
        return found
    for f in files:
        try:
            if not f.is_file():
                continue
            if is_project and not f.resolve().is_relative_to(real_root):
                continue
            rel = f.parent.relative_to(root).as_posix()
        except (OSError, ValueError):
            continue
        found[_norm_path("" if rel == "." else rel)] = f
    return found


def discover_layers(roots: Iterable[tuple[Path, bool]], warnings: Optional[list[str]] = None) -> dict[str, Layer]:
    """Merge every root into one tree: higher roots replace lower ones per path, or extend them."""
    stacks: dict[str, list[Path]] = {}  # path -> files, highest precedence first
    for root, is_project in roots:
        for path, f in _scan_root(Path(root), is_project).items():
            stacks.setdefault(path, []).append(f)
    layers: dict[str, Layer] = {}
    for path, files in stacks.items():
        texts: list[str] = []
        sources: list[str] = []
        detect: Optional[list[str]] = None
        description = ""
        text_done = False
        for f in files:  # highest first
            try:
                meta, body = parse_frontmatter(_read_capped(f))
            except OSError as exc:
                if warnings is not None:
                    warnings.append(f"cannot read layer {f}: {exc}")
                continue
            # detect/description: the highest layer that sets them wins (an override inherits them otherwise)
            if detect is None and "detect" in meta:
                detect = _split(meta["detect"])
            description = description or " ".join(meta.get("description", "").split())
            if text_done:
                continue
            # text: the highest layer replaces lower ones, unless it says extend: true (then it is appended)
            texts.insert(0, body.strip())
            sources.insert(0, str(f))
            text_done = str(meta.get("extend", "")).strip().lower() not in ("1", "true", "yes", "on")
        detect = detect or []
        layers[path] = Layer(path, "\n\n".join(t for t in texts if t), sources, detect, description)
    return layers


def detect(workspace: Path | str, layers: dict[str, Layer]) -> list[str]:
    """Layer paths whose ``detect:`` patterns match a file or folder name in the workspace root."""
    try:
        names = [p.name for p in Path(workspace).iterdir()]
    except OSError:
        return []
    hits = []
    for path, layer in sorted(layers.items()):
        if any(fnmatch.fnmatchcase(n.lower(), pat.lower()) for pat in layer.detect for n in names):
            hits.append(path)
    return hits


def profile_from_agents_md(*texts: str) -> str:
    """The last ``ai-rex-profile: ...`` line in the given AGENTS.md texts ('' if none)."""
    value = ""
    for text in texts:
        text = _FENCED.sub("", text or "")  # examples inside ``` blocks do not count
        for m in _PROFILE_LINE.finditer(text):
            value = m.group(1).strip()
    return value


def requested_profile(config: Any = None, agents_md: str = "", environ: Optional[dict] = None) -> tuple[str, str]:
    """(selection, origin): ``agent.profile`` (flag or AI_REX_AGENT__PROFILE) > AI_REX_PROFILE > AGENTS.md > auto."""
    env = os.environ if environ is None else environ
    value = config.get("agent.profile") if config is not None else None
    if isinstance(value, (list, tuple)):
        value = ",".join(str(v) for v in value)
    if value and str(value).strip():
        return str(value).strip(), "flag/config"
    if env.get("AI_REX_PROFILE", "").strip():
        return env["AI_REX_PROFILE"].strip(), "env AI_REX_PROFILE"
    if agents_md:
        return agents_md, "AGENTS.md"
    return "auto", "auto"


def select(layers: dict[str, Layer], selection: str, workspace: Path | str,
           warnings: Optional[list[str]] = None) -> list[Layer]:
    """Active layers, root first. ``auto`` = detected, ``base``/``none`` = root only, else layer paths."""
    wanted: list[str] = []
    for token in _split(selection) or ["auto"]:
        low = token.lower()
        if low == "auto":
            wanted.extend(detect(workspace, layers))
        elif low in ("base", "none"):
            continue
        else:
            path = _norm_path(token)
            if path not in layers and warnings is not None:
                warnings.append(f"profile layer not found: {token}")
            wanted.append(path)
    active: set[str] = {""}
    for path in wanted:
        parts = path.split("/") if path else []
        for i in range(len(parts) + 1):
            active.add("/".join(parts[:i]))
    chosen = [layers[p] for p in active if p in layers]
    return sorted(chosen, key=lambda layer: (layer.depth, layer.path))


def build_profile(workspace: Path | str, config: Any = None, agents_md: str = "", home: Optional[Path] = None,
                  environ: Optional[dict] = None, include_builtin: bool = True) -> Profile:
    """Discover the layer tree and pick the active layers for ``workspace``."""
    warnings: list[str] = []
    layers = discover_layers(layer_roots(workspace, config, home, include_builtin), warnings)
    selection, origin = requested_profile(config, profile_from_agents_md(agents_md), environ)
    chosen = select(layers, selection, workspace, warnings)
    return Profile(chosen, selection, origin, warnings)


def all_layers(workspace: Path | str, config: Any = None, home: Optional[Path] = None) -> dict[str, Layer]:
    return discover_layers(layer_roots(workspace, config, home))


__all__ = [
    "Layer", "Profile", "BUILTIN_DIR", "LAYER_FILE", "layer_roots", "discover_layers", "detect",
    "profile_from_agents_md", "requested_profile", "select", "build_profile", "all_layers", "install_dir",
]
