# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# AI models: claude-opus-5-5
# Licensed under MIT License
"""Persistent allow buffer: paths the user answered "allow always" for, per kind of access.

The buffer lets ai-rex run for weeks without Docker: every "always" answer for a file or folder adds an entry,
and later calls below an entry run without asking. What it holds and how it decides:

* Entries are ``(kind, path)``: ``read``, ``write`` (fileops / edit) and ``exec`` (the working directory of a
  shell command). A folder entry covers everything below it. ``exec`` entries are a record of where commands were
  approved; they never allow a command by themselves (commands keep the normal rules and their own "allow always"
  command rules).
* Paths are absolute, symlinks resolved, case-folded on Windows, and must be INSIDE the workspace. A path outside
  the workspace (``..``, a symlink leading out, another drive, a UNC path) is never stored and never matched.
* Size is counted in entries (``permissions.allow_buffer_size``, env ``AI_REX_ALLOW_BUFFER``, flag
  ``--allow-buffer N``; 0 = disabled, max 1_000_000). When full, the least recently USED entry goes (LRU); every
  allow decided by the buffer refreshes the entry's time.
* Lookup walks the path's parents up to the workspace root: O(depth) dict lookups.
* Storage: one small JSON file per workspace (named by a hash of the normalised workspace path), written
  atomically. A corrupt file is kept as ``.bak``, a warning is recorded and the buffer starts empty. Location:
  ``AI_REX_STATE_DIR`` if set, else ``<install folder>/state`` for a portable install, else ``<workspace>/.ai-rex``
  (a repo checkout); the file is in ``allow-buffer/`` below it.

Precedence (in :meth:`PermissionRules.evaluate`): deny rules > ask rules > allow rules > buffer > mode default.
Pure stdlib.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

VERSION = 1
DEFAULT_SIZE = 1024
MAX_SIZE = 1_000_000
KINDS = ("read", "write", "exec")
ENV_SIZE = "AI_REX_ALLOW_BUFFER"
ENV_STATE_DIR = "AI_REX_STATE_DIR"
INSTALL_MARKER = ".ai-rex-install"
SUBDIR = "allow-buffer"
# Timestamp-only changes (an entry was used) are written at most this often, and at exit.
TOUCH_FLUSH_SECONDS = 60.0

_WINDOWS = os.name == "nt"


def parse_size(value: Any) -> int:
    """A buffer size from a flag, env or config value; ValueError outside 0..1_000_000."""
    if isinstance(value, bool):
        raise ValueError("allow buffer size must be a whole number 0..1000000")
    try:
        size = int(str(value).strip().replace("_", ""))
    except (TypeError, ValueError):
        raise ValueError(f"allow buffer size must be a whole number 0..{MAX_SIZE}, not {value!r}") from None
    if not 0 <= size <= MAX_SIZE:
        raise ValueError(f"allow buffer size must be 0..{MAX_SIZE}, not {size}")
    return size


def _fold(path: str) -> str:
    """Comparison key of an absolute path: case-folded and backslashes on Windows."""
    return path.replace("/", "\\").lower() if _WINDOWS else path


def real(path: str | os.PathLike[str]) -> str:
    """Absolute path with symlinks resolved (no error for a path that does not exist)."""
    return os.path.realpath(os.path.abspath(os.fspath(path)))


def install_dir(start: Optional[Path] = None) -> Optional[Path]:
    """The portable install folder this code runs from (it holds ``.ai-rex-install``), or None (a checkout)."""
    here = (start or Path(__file__)).resolve()
    for parent in list(here.parents)[:7]:
        if (parent / INSTALL_MARKER).is_file():
            return parent
    return None


def state_dir(workspace: str | os.PathLike[str], environ: Optional[Mapping[str, str]] = None,
              install: Optional[Path] = None, detect_install: bool = True) -> Path:
    """Folder that holds the buffer files: ``AI_REX_STATE_DIR``, else ``<install>/state``, else ``<workspace>/.ai-rex``."""
    env = os.environ if environ is None else environ
    explicit = env.get(ENV_STATE_DIR)
    if explicit:
        return Path(explicit).expanduser() / SUBDIR
    if install is None and detect_install:
        install = install_dir()
    if install is not None:
        return Path(install) / "state" / SUBDIR
    return Path(real(workspace)) / ".ai-rex" / SUBDIR


def buffer_file(workspace: str | os.PathLike[str], directory: Path) -> Path:
    digest = hashlib.sha256(_fold(real(workspace)).encode("utf-8")).hexdigest()[:24]
    return Path(directory) / f"{digest}.json"


class AllowBuffer:
    """LRU set of approved ``(kind, path)`` entries for one workspace, kept in one JSON file."""

    def __init__(self, workspace: str | os.PathLike[str], path: Optional[Path], size: int = DEFAULT_SIZE) -> None:
        self.workspace = real(workspace)
        self._ws_key = _fold(self.workspace)
        self.path = Path(path) if path is not None else None
        self.size = parse_size(size)
        self.warnings: list[str] = []
        # "kind|key" -> (last used, display path); order = least recently used first.
        self._entries: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self._removed: set[str] = set()
        self._cleared_at = 0.0
        self._dirty_touch = False
        self._last_flush = time.time()
        self._lock = threading.RLock()
        self._file_sig: Optional[tuple[int, int]] = None  # (mtime_ns, size) of the file as we last saw it
        self._load()

    # ---------------------------------------------------------------- paths
    def resolve(self, raw: Any) -> Optional[tuple[str, str]]:
        """(display path, key) of ``raw`` (relative to the workspace or absolute) if it is inside the workspace."""
        if not isinstance(raw, str):
            return None
        raw = raw.strip() or "."
        if "\x00" in raw or raw.startswith("~"):
            return None
        try:
            full = real(os.path.join(self.workspace, raw))
        except (OSError, ValueError):
            return None
        key = _fold(full)
        return (full, key) if self._inside_key(key) else None

    def _inside_key(self, key: str) -> bool:
        return key == self._ws_key or key.startswith(self._ws_key.rstrip(os.sep) + os.sep)

    def inside(self, raw: Any) -> bool:
        return self.resolve(raw) is not None

    def relative(self, display: str) -> str:
        """Workspace-relative POSIX form of a stored path, for listings."""
        try:
            rel = os.path.relpath(display, self.workspace)
        except ValueError:
            return display
        return "." if rel == "." else rel.replace(os.sep, "/")

    # --------------------------------------------------------------- lookup
    @property
    def enabled(self) -> bool:
        return self.size > 0

    def covers(self, kind: str, raw: Any, touch: bool = True) -> Optional[str]:
        """Display path of the entry that covers ``raw`` for ``kind`` (refreshing its time), else None."""
        if not self.enabled or kind not in KINDS:
            return None
        resolved = self.resolve(raw)
        if resolved is None:
            return None
        key = resolved[1]
        stop = self._ws_key
        with self._lock:
            while True:
                item = self._entries.get(f"{kind}|{key}")
                if item is not None:
                    if touch:
                        self._entries[f"{kind}|{key}"] = (time.time(), item[1])
                        self._entries.move_to_end(f"{kind}|{key}")
                        self._dirty_touch = True
                        self._maybe_flush_touch()
                    return item[1]
                if key == stop:
                    return None
                parent = os.path.dirname(key)
                if parent == key or len(parent) < len(stop):
                    return None
                key = parent

    # -------------------------------------------------------------- changes
    def add(self, kind: str, raw: Any) -> Optional[str]:
        """Remember ``raw`` for ``kind``; returns the stored display path, or None (disabled / outside)."""
        if not self.enabled or kind not in KINDS:
            return None
        resolved = self.resolve(raw)
        if resolved is None:
            return None
        display, key = resolved
        with self._lock:
            k = f"{kind}|{key}"
            self._entries[k] = (time.time(), display)
            self._entries.move_to_end(k)
            self._removed.discard(k)
            self._evict()
            self.save()
        return display

    def remove(self, raw: Any, kind: Optional[str] = None) -> int:
        """Forget the entries for exactly ``raw`` (every kind, or one); returns how many went."""
        resolved = self.resolve(raw)
        if resolved is None:
            return 0
        count = 0
        with self._lock:
            for k in KINDS if kind is None else (kind,):
                name = f"{k}|{resolved[1]}"
                if self._entries.pop(name, None) is not None:
                    count += 1
                self._removed.add(name)
            if count:
                self.save()
        return count

    def clear(self) -> int:
        with self._lock:
            count = len(self._entries)
            self._entries.clear()
            self._removed.clear()
            self._cleared_at = time.time()
            self.save()
        return count

    def resize(self, size: int) -> int:
        """Change the size (this process); returns how many entries were evicted."""
        size = parse_size(size)
        with self._lock:
            before = len(self._entries)
            self.size = size
            if size:
                self._evict()
                if len(self._entries) != before:
                    self.save()
            return before - len(self._entries)

    def entries(self) -> list[tuple[str, str, float]]:
        """``(kind, display path, last used)``, most recently used first."""
        with self._lock:
            return [(k.split("|", 1)[0], v[1], v[0]) for k, v in reversed(self._entries.items())]

    def __len__(self) -> int:
        return len(self._entries)

    def _evict(self) -> None:
        while self.size and len(self._entries) > self.size:
            self._entries.popitem(last=False)

    # -------------------------------------------------------------- storage
    def _read_file(self) -> list[tuple[str, str, float]]:
        """Entries of the file; [] when missing. A corrupt file is copied to .bak and reported."""
        if self.path is None:
            return []
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except OSError as exc:
            self.warnings.append(f"allow buffer: cannot read {self.path}: {exc}")
            return []
        try:
            data = json.loads(text)
            if not isinstance(data, dict) or data.get("version") != VERSION:
                raise ValueError("unknown format or version")
            out = []
            for item in data.get("entries") or []:
                kind, display, used = item
                if kind in KINDS and isinstance(display, str) and isinstance(used, (int, float)):
                    out.append((kind, display, float(used)))
            return out
        except (ValueError, TypeError) as exc:
            bak = self.path.with_name(self.path.name + ".bak")
            try:
                shutil.copy2(self.path, bak)
                where = f"; kept as {bak}"
            except OSError:
                where = ""
            self.warnings.append(f"allow buffer: {self.path} is corrupt ({exc}); starting empty{where}")
            try:
                self.path.unlink()
            except OSError:
                pass
            return []

    def _sig(self) -> Optional[tuple[int, int]]:
        try:
            st = self.path.stat() if self.path is not None else None
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size) if st is not None else None

    def _load(self) -> None:
        self._file_sig = self._sig()
        items = self._read_file()
        items.sort(key=lambda e: e[2])
        for kind, display, used in items:
            key = _fold(display)
            if not self._inside_key(key):
                continue  # never trust an entry outside the workspace, whatever the file says
            self._entries[f"{kind}|{key}"] = (used, display)
        self._evict()

    def save(self) -> None:
        """Merge with the file (another process may have added entries) and replace it atomically."""
        if self.path is None:
            return
        with self._lock:
            sig = self._sig()
            # Merge only when another process changed the file since we last read or wrote it.
            changed = sig is not None and sig != self._file_sig
            for kind, display, used in self._read_file() if changed else []:
                k = f"{kind}|{_fold(display)}"
                if k in self._removed or used <= self._cleared_at:
                    continue
                if not self._inside_key(_fold(display)):
                    continue
                mine = self._entries.get(k)
                if mine is None:
                    self._entries[k] = (used, display)
                elif used > mine[0]:
                    self._entries[k] = (used, mine[1])
            if changed:
                self._entries = OrderedDict(sorted(self._entries.items(), key=lambda kv: kv[1][0]))
            self._evict()
            body = {"version": VERSION, "workspace": self.workspace,
                    "entries": [[k.split("|", 1)[0], v[1], round(v[0], 3)] for k, v in self._entries.items()]}
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._ignore_in_git()
                tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
                try:
                    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                        json.dump(body, f, ensure_ascii=False, separators=(",", ":"))
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(tmp, self.path)
                    self._file_sig = self._sig()
                finally:
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
            except OSError as exc:
                self.warnings.append(f"allow buffer: cannot save {self.path}: {exc}")
            self._dirty_touch = False
            self._last_flush = time.time()

    def _ignore_in_git(self) -> None:
        """A buffer folder inside the workspace gets a ``.gitignore`` so it is never committed."""
        top = self.path.parent.parent if self.path is not None else None
        if top is None or top.name != ".ai-rex":
            return
        ignore = top / ".gitignore"
        if not ignore.exists():
            try:
                ignore.write_text("*\n", encoding="utf-8")
            except OSError:
                pass

    def _maybe_flush_touch(self) -> None:
        if self._dirty_touch and time.time() - self._last_flush >= TOUCH_FLUSH_SECONDS:
            self.save()

    def flush(self) -> None:
        """Write pending timestamp changes (called at exit)."""
        if self._dirty_touch:
            self.save()

    def protects(self, raw: Any) -> bool:
        """True when ``raw`` (workspace-relative or absolute) is the buffer folder or inside it."""
        if self.path is None or not isinstance(raw, str):
            return False
        try:
            target = _fold(real(os.path.join(self.workspace, raw.strip() or ".")))
            folder = _fold(real(self.path.parent.parent if self.path.parent.parent.name == ".ai-rex"
                                else self.path.parent))
        except (OSError, ValueError):
            return False
        return target == folder or target.startswith(folder.rstrip(os.sep) + os.sep)


# One buffer object per file in a process: sub-agents and the REPL commands share it.
_REGISTRY: dict[str, AllowBuffer] = {}
_REGISTRY_LOCK = threading.Lock()


def get_buffer(workspace: str | os.PathLike[str], size: int,
               environ: Optional[Mapping[str, str]] = None) -> AllowBuffer:
    """The shared buffer of ``workspace`` (created and loaded on first use)."""
    directory = state_dir(workspace, environ)
    path = buffer_file(workspace, directory)
    with _REGISTRY_LOCK:
        buf = _REGISTRY.get(str(path))
        if buf is None:
            buf = AllowBuffer(workspace, path, size)
            _REGISTRY[str(path)] = buf
            if not _REGISTRY_ATEXIT:
                import atexit
                atexit.register(_flush_all)
                _REGISTRY_ATEXIT.append(True)
        elif buf.size != size:
            buf.resize(size)
        return buf


_REGISTRY_ATEXIT: list[bool] = []


def _flush_all() -> None:
    for buf in list(_REGISTRY.values()):
        try:
            buf.flush()
        except Exception:
            pass


def format_entries(buf: AllowBuffer, limit: Optional[int] = None) -> list[str]:
    """Lines for ``/allow list`` and ``--allow-buffer-list``."""
    head = (f"allow buffer: {len(buf)} of {buf.size} entries" if buf.enabled else
            f"allow buffer: disabled (size 0), {len(buf)} stored entries kept")
    lines = [head, f"workspace: {buf.workspace}", f"file: {buf.path}"]
    items = buf.entries()
    for kind, display, used in items[:limit] if limit else items:
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(used))
        lines.append(f"  {kind:<5}  {when}  {buf.relative(display)}")
    if limit and len(items) > limit:
        lines.append(f"  ... {len(items) - limit} more")
    return lines


def allow_command(buf: Optional[AllowBuffer], text: str, set_size: Any = None) -> tuple[int, list[str]]:
    """Run ``/allow`` with its raw argument text; returns (exit code, output lines).

    ``list``, ``remove <path>``, ``clear``, ``size [N]``. ``set_size(n)`` is called after a size change so
    the caller can update its configuration.
    """
    usage = "Usage: /allow list | /allow remove <path> | /allow clear | /allow size [N]"
    words = text.strip().split(None, 1)
    verb = words[0].lower() if words else "list"
    rest = words[1].strip() if len(words) > 1 else ""
    if buf is None:
        return 1, ["allow buffer: not active here (no workspace buffer configured)"]
    if verb == "list":
        return 0, format_entries(buf)
    if verb == "remove":
        if not rest:
            return 1, [usage]
        if len(rest) >= 2 and rest[0] == rest[-1] and rest[0] in "'\"":
            rest = rest[1:-1]
        kind = None
        first, _, tail = rest.partition(" ")
        if first in KINDS and tail.strip():
            kind, rest = first, tail.strip()
        n = buf.remove(rest, kind)
        return (0, [f"removed {n} entr{'y' if n == 1 else 'ies'} for {rest}"]) if n else \
            (1, [f"no entry for {rest} (see /allow list)"])
    if verb == "clear":
        n = buf.clear()
        return 0, [f"allow buffer cleared ({n} entries removed)"]
    if verb == "size":
        if not rest:
            return 0, [f"allow buffer size: {buf.size} entries ({len(buf)} used)"
                       + ("" if buf.enabled else " - disabled")]
        try:
            evicted = buf.resize(parse_size(rest))
        except ValueError as exc:
            return 1, [str(exc)]
        if callable(set_size):
            set_size(buf.size)
        note = f", {evicted} least recently used entries evicted" if evicted else ""
        return 0, [f"allow buffer size: {buf.size} entries (this session){note}"
                   + ("" if buf.enabled else " - disabled")]
    return 1, [usage]


def print_lines(lines: Iterable[str], out: Any = None) -> None:
    out = out or sys.stdout
    for line in lines:
        print(line, file=out)
