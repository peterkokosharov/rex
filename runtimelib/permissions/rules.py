# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-sonnet-5-5, claude-opus-5-5
# Licensed under MIT License

"""Configurable permission rules (allow / ask / deny) for tool calls.

Rule grammar: ``Tool(spec)`` or a bare ``Tool``. ``Tool`` is a tool name or an
fnmatch-style glob. The spec depends on the tool:

* bash       - command glob (``bash(pytest *)``)
* fileops    - ``op:path-glob`` (``fileops(write:**/.env)``), or just a path glob
* edit       - path glob (``edit(src/**)``)
* git        - ``op *`` (``git(push*)``)
* webfetch   - ``domain:host`` (suffix match) or a URL glob
* MCP tools  - no spec (``mcp__github__delete_*``)

Precedence: deny > ask > allow > mode default. Compound bash commands are split
on ``&&``, ``||``, ``;``, ``|``, ``&`` and newlines (quote aware): any part that
hits deny denies, any part that hits ask asks, "allow" needs every part allowed.
Command substitution (``$(``, backticks, ``<(``, ``>(``) is never auto-allowed
(at least ask) and its content is also checked against deny rules.

Paths are normalised relative to the workspace before matching. Mode defaults
(see :func:`mode_default`): local mode asks for every write, exec, network and
MCP call unless an allow rule matches, whatever the config says.

Public entry point: :class:`PermissionRules` (``evaluate`` and
``add_allow_always``). Pure stdlib, no I/O except the allow-always store.
"""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import PermissionResult

ALLOW = PermissionResult.ALLOW
ASK = PermissionResult.ASK
DENY = PermissionResult.DENY

KIND_READ = "read"
KIND_WRITE = "write"
KIND_EXEC = "exec"
KIND_NETWORK = "network"
KIND_MCP = "mcp"

MODE_GUARD = "guard"
MODE_SANDBOX = "sandbox"
MODE_LOCAL = "local"

_READ_FILE_OPS = {"read", "list", "glob", "grep"}
_READ_GIT_OPS = {"status", "diff", "log", "show"}
_READ_TOOLS = {"load_skill", "read_skill_file", "render_diagram"}
_DECISIONS = {"allow": ALLOW, "ask": ASK, "deny": DENY}


class RuleParseError(ValueError):
    """Raised for a rule string that does not follow ``Tool(spec)``."""


# ---------------------------------------------------------------- globbing

def _glob_to_regex(glob: str, *, path: bool) -> re.Pattern[str]:
    """Translate a glob to a regex. ``path`` mode: ``*`` stops at ``/``, ``**`` crosses it."""
    out: list[str] = []
    i, n = 0, len(glob)
    while i < n:
        c = glob[i]
        if c == "*":
            if path and glob.startswith("**", i):
                if glob.startswith("**/", i):
                    out.append("(?:.*/)?")
                    i += 3
                else:
                    out.append(".*")
                    i += 2
                continue
            out.append("[^/]*" if path else ".*")
        elif c == "?":
            out.append("[^/]" if path else ".")
        elif c == "[":
            j = i + 1
            if j < n and glob[j] in "!^":
                j += 1
            if j < n and glob[j] == "]":
                j += 1
            while j < n and glob[j] != "]":
                j += 1
            if j >= n:
                out.append(re.escape(c))
            else:
                body = glob[i + 1:j]
                if body[:1] in "!^":
                    body = "^" + body[1:]
                body = body.replace("\\", "\\\\").replace("[", "\\[")
                out.append("[" + body + "]")
                i = j
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("".join(out), re.DOTALL)


def _glob_match(glob: str, text: str, *, path: bool) -> bool:
    if path and os.name == "nt":
        glob, text = glob.lower(), text.lower()
    return _glob_to_regex(glob, path=path).fullmatch(text) is not None


def _cmd_match(glob: str, text: str) -> bool:
    """Command/op match: a trailing ``" *"`` also matches the bare command."""
    if _glob_match(glob, text, path=False):
        return True
    return glob.endswith(" *") and _glob_match(glob[:-2], text, path=False)


def escape_glob(text: str) -> str:
    """Make ``text`` match itself literally in a rule spec."""
    return re.sub(r"([*?\[])", r"[\1]", text)


# ------------------------------------------------------------ bash splitting

def split_bash(command: str) -> tuple[list[str], bool]:
    """Split a command line into parts. Returns (parts, has_substitution).

    Splits on ``&&``, ``||``, ``;``, ``|``, ``&`` and newlines outside quotes.
    ``has_substitution`` is true for ``$(``, backticks, ``<(``, ``>(`` outside
    single quotes.
    """
    parts: list[str] = []
    buf: list[str] = []
    unsafe = False
    quote = ""
    i, n = 0, len(command)

    def flush() -> None:
        s = "".join(buf).strip()
        if s:
            parts.append(s)
        buf.clear()

    while i < n:
        c = command[i]
        if quote == "'":
            buf.append(c)
            if c == "'":
                quote = ""
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            buf.append(command[i:i + 2])
            i += 2
            continue
        if c == "`" or command.startswith("$(", i):
            unsafe = True
        if quote == '"':
            buf.append(c)
            if c == '"':
                quote = ""
            i += 1
            continue
        if c in "'\"":
            quote = c
            buf.append(c)
        elif command.startswith(("<(", ">("), i):
            unsafe = True
            buf.append(c)
        elif command.startswith(("&&", "||"), i):
            flush()
            i += 1
        elif c in ";|&\n\r":
            flush()
        else:
            buf.append(c)
        i += 1
    flush()
    return parts, unsafe


def _deny_parts(command: str) -> list[str]:
    """Parts used for deny matching: normal split plus substitution contents."""
    parts, _ = split_bash(command)
    loose = re.sub(r"\$\(|<\(|>\(|`|\)", ";", command)
    extra, _ = split_bash(loose)
    seen = list(parts)
    for p in extra:
        if p not in seen:
            seen.append(p)
    return seen


# ------------------------------------------------------------- path handling

_DRIVE = re.compile(r"^[A-Za-z]:/")


def _norm(path: str) -> str:
    p = path.replace("\\", "/")
    drive = ""
    if _DRIVE.match(p):
        drive, p = p[:2], p[2:]
    p = posixpath.normpath(p) if p else "."
    if p.startswith("//"):
        p = "/" + p.lstrip("/")
    return drive + p


def _is_abs(p: str) -> bool:
    return p.startswith("/") or bool(_DRIVE.match(p))


def normalize_path(path: str, workspace: str) -> str:
    """Normalise ``path`` for matching: workspace-relative posix, else absolute posix."""
    ws = _norm(workspace or ".")
    p = path.replace("\\", "/") if path else "."
    p = _norm(p if _is_abs(p) else ws.rstrip("/") + "/" + p)
    cmp_p, cmp_ws = (p.lower(), ws.lower()) if os.name == "nt" else (p, ws)
    if cmp_p == cmp_ws:
        return "."
    prefix = cmp_ws.rstrip("/") + "/"
    if cmp_p.startswith(prefix):
        return p[len(prefix):]
    return p


# --------------------------------------------------------- call description

def _fileops_op(args: dict[str, Any]) -> str:
    return str(args.get("operation") or args.get("op") or "read")


def classify(tool: str, args: dict[str, Any]) -> str:
    """Kind of a tool call: read / write / exec / network / mcp."""
    if tool.startswith("mcp__"):
        return KIND_MCP
    if tool == "bash":
        return KIND_EXEC
    if tool == "fileops":
        return KIND_READ if _fileops_op(args) in _READ_FILE_OPS else KIND_WRITE
    if tool == "git":
        return KIND_READ if str(args.get("operation", "")) in _READ_GIT_OPS else KIND_WRITE
    if tool in ("webfetch", "websearch"):
        return KIND_NETWORK
    if tool in _READ_TOOLS:
        return KIND_READ
    return KIND_WRITE  # edit and anything unknown: be conservative


def _host(url: str) -> str:
    m = re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://(?:[^/@?#]*@)?(\[[^\]]*\]|[^/:?#]*)", url.strip())
    return (m.group(1) if m else "").lower().strip("[]")


def _subjects(tool: str, args: dict[str, Any], workspace: str) -> list[str]:
    """Strings a rule spec is matched against (several for compound bash)."""
    if tool == "bash":
        return split_bash(str(args.get("command", "")))[0]
    if tool == "fileops":
        return [f"{_fileops_op(args)}:{normalize_path(str(args.get('path') or '.'), workspace)}"]
    if tool == "edit":
        return [normalize_path(str(args.get("path") or ""), workspace)]
    if tool == "git":
        extra = args.get("message") or args.get("branch") or ""
        return [(str(args.get("operation", "")) + " " + str(extra)).strip()]
    if tool == "webfetch":
        return [str(args.get("url", ""))]
    return [""]


@dataclass(frozen=True)
class Rule:
    """One parsed rule. ``spec`` is None for a bare tool rule."""

    tool: str
    spec: str | None
    decision: PermissionResult
    text: str

    def tool_matches(self, tool: str) -> bool:
        return _glob_match(self.tool, tool, path=False)

    def matches_subject(self, tool: str, subject: str) -> bool:
        """Does this rule cover one subject string of a call to ``tool``?"""
        if not self.tool_matches(tool):
            return False
        spec = self.spec
        if spec is None:
            return True
        if tool == "bash":
            return _cmd_match(spec, subject)
        if tool == "git":
            return _cmd_match(spec, subject)
        if tool == "fileops":
            op_glob, path_glob = _split_op(spec)
            op, _, path = subject.partition(":")
            if op_glob is not None and not _glob_match(op_glob, op, path=False):
                return False
            return _path_match(path_glob, path)
        if tool == "edit":
            return _path_match(spec, subject)
        if tool == "webfetch":
            if spec.lower().startswith("domain:"):
                dom = spec[7:].strip().lower().lstrip(".")
                host = _host(subject)
                return bool(host) and (host == dom or host.endswith("." + dom)
                                       or _glob_match(dom, host, path=False))
            return _glob_match(spec, subject, path=False)
        return spec == "*"  # tools without a spec grammar


def _split_op(spec: str) -> tuple[str | None, str]:
    head, sep, tail = spec.partition(":")
    if sep and (head == "*" or re.fullmatch(r"[A-Za-z_*?|\[\]]{2,}", head)):
        return head, tail
    return None, spec


def _path_match(glob: str, path: str) -> bool:
    g = glob.replace("\\", "/")
    if g.startswith("./"):
        g = g[2:]
    return _glob_match(g, path, path=True)


_RULE_RE = re.compile(r"^\s*([^()\s][^()]*?)\s*(?:\((.*)\))?\s*$", re.DOTALL)


def parse_rule(text: str, decision: PermissionResult | str) -> Rule:
    """Parse ``Tool(spec)`` / ``Tool`` into a :class:`Rule`."""
    if isinstance(decision, str):
        if decision not in _DECISIONS:
            raise RuleParseError(f"unknown decision {decision!r}")
        decision = _DECISIONS[decision]
    if not isinstance(text, str) or not text.strip():
        raise RuleParseError("empty rule")
    m = _RULE_RE.match(text)
    if not m:
        raise RuleParseError(f"bad rule {text!r}")
    tool, spec = m.group(1).strip(), m.group(2)
    if spec is not None:
        spec = spec.strip()
        if spec == "":
            raise RuleParseError(f"empty spec in {text!r}")
    return Rule(tool=tool, spec=spec, decision=decision, text=text.strip())


# ------------------------------------------------------------- mode defaults

def mode_default(mode: str, kind: str, defaults: dict[str, Any] | None = None) -> PermissionResult:
    """Default decision of ``mode`` for a tool ``kind`` when no rule matched.

    Read-only tools are allowed everywhere. Network and MCP ask everywhere.
    Writes/exec: guard -> ``defaults.guard`` (default allow), sandbox ->
    ``defaults.sandbox`` (default ask), local (and unknown modes) -> ask, fixed.
    """
    if kind == KIND_READ:
        return ALLOW
    if kind in (KIND_NETWORK, KIND_MCP):
        return ASK
    defaults = defaults or {}
    if mode == MODE_GUARD:
        return _DECISIONS.get(str(defaults.get("guard", "allow")).lower(), ALLOW)
    if mode == MODE_SANDBOX:
        return _DECISIONS.get(str(defaults.get("sandbox", "ask")).lower(), ASK)
    return ASK


@dataclass(frozen=True)
class RuleVerdict:
    """Outcome of :meth:`PermissionRules.evaluate`.

    ``source``: ``deny`` / ``ask`` / ``allow`` (a rule matched), ``unsafe``
    (substitution forced an ask) or ``default`` (mode default). ``rule`` is the
    rule text that decided, if any.
    """

    decision: PermissionResult
    kind: str
    source: str
    rule: str | None = None
    reason: str = ""


# ------------------------------------------------------------------- store

def store_path(workspace: str | os.PathLike[str], base_dir: str | os.PathLike[str] | None = None) -> Path:
    """``<base>/<sha1-of-workspace>.yaml`` (base default ``~/.config/runtime/permissions``)."""
    base = Path(base_dir) if base_dir else Path.home() / ".config" / "runtime" / "permissions"
    digest = hashlib.sha1(_norm(str(workspace)).encode("utf-8")).hexdigest()
    return base / f"{digest}.yaml"


def load_store(path: Path) -> list[str]:
    """Read allow rules from a store file. Missing or unreadable file = empty."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rules: list[str] = []
    for line in lines:
        m = re.match(r"^\s*-\s+(.*?)\s*$", line)
        if not m:
            continue
        item = m.group(1)
        if item.startswith('"'):
            try:
                item = json.loads(item)
            except ValueError:
                continue
        if isinstance(item, str) and item:
            rules.append(item)
    return rules


def save_store(path: Path, rules: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "allow:\n" + "".join(f"  - {json.dumps(r)}\n" for r in rules)
    path.write_text(body, encoding="utf-8")


# ----------------------------------------------------------------- engine

class PermissionRules:
    """Rule set for one workspace.

    ``config`` is the ``permissions:`` section of the user config (a dict with
    optional ``defaults``, ``allow``, ``ask``, ``deny``). Bad rule strings are
    skipped and listed in ``errors``. Allow-always rules for the workspace are
    loaded from the per-workspace store (``store_dir`` overrides the location;
    ``persist=False`` keeps them in memory only).
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        workspace: str | os.PathLike[str] = ".",
        *,
        store_dir: str | os.PathLike[str] | None = None,
        persist: bool = True,
    ) -> None:
        config = config if isinstance(config, dict) else {}
        self.workspace = str(Path(workspace).resolve()) if str(workspace) in (".", "") else str(workspace)
        self.defaults: dict[str, Any] = dict(config.get("defaults") or {})
        self.errors: list[str] = []
        self.allow: list[Rule] = self._parse_list(config.get("allow"), ALLOW)
        self.ask: list[Rule] = self._parse_list(config.get("ask"), ASK)
        self.deny: list[Rule] = self._parse_list(config.get("deny"), DENY)
        self._persist = persist
        self._store = store_path(self.workspace, store_dir)
        self._stored: list[str] = load_store(self._store) if persist else []
        for text in self._stored:
            try:
                self.allow.append(parse_rule(text, ALLOW))
            except RuleParseError as exc:
                self.errors.append(str(exc))

    def _parse_list(self, items: Any, decision: PermissionResult) -> list[Rule]:
        out: list[Rule] = []
        if isinstance(items, str):
            items = [items]
        for text in items or []:
            try:
                out.append(parse_rule(text, decision))
            except RuleParseError as exc:
                self.errors.append(str(exc))
        return out

    # -- evaluation
    def evaluate(self, tool: str, args: dict[str, Any] | None, mode: str = MODE_LOCAL) -> RuleVerdict:
        """Decide one tool call. Precedence: deny > ask > allow > mode default."""
        args = args or {}
        kind = classify(tool, args)
        subjects = _subjects(tool, args, self.workspace)
        unsafe = ""
        deny_subjects = subjects
        if tool == "bash":
            command = str(args.get("command", ""))
            if split_bash(command)[1]:
                unsafe = "Command substitution is never auto-allowed"
            elif args.get("env"):
                # Extra variables (PATH, LD_PRELOAD, GIT_SSH_COMMAND ...) change what runs.
                unsafe = "A command with extra environment variables is never auto-allowed"
            deny_subjects = _deny_parts(command)

        for rule in self.deny:
            if any(rule.matches_subject(tool, s) for s in deny_subjects or [""]):
                return RuleVerdict(DENY, kind, "deny", rule.text, f"Permission denied (rule {rule.text})")
        for rule in self.ask:
            if any(rule.matches_subject(tool, s) for s in subjects or [""]):
                return RuleVerdict(ASK, kind, "ask", rule.text, f"Ask required (rule {rule.text})")

        allowed_by: Rule | None = None
        if subjects and all(self._allowed(tool, s) for s in subjects):
            allowed_by = self._allowed(tool, subjects[0])
        elif not subjects:
            allowed_by = next((r for r in self.allow if r.tool_matches(tool) and r.spec is None), None)
        if allowed_by is not None:
            if unsafe:
                return RuleVerdict(ASK, kind, "unsafe", allowed_by.text, unsafe)
            return RuleVerdict(ALLOW, kind, "allow", allowed_by.text)

        decision = mode_default(mode, kind, self.defaults)
        if unsafe and decision == ALLOW:
            return RuleVerdict(ASK, kind, "unsafe", None, unsafe)
        return RuleVerdict(decision, kind, "default", None, f"{mode} default for {kind}")

    def _allowed(self, tool: str, subject: str) -> Rule | None:
        for rule in self.allow:
            if rule.matches_subject(tool, subject):
                return rule
        return None

    # -- allow always
    def exact_rules(self, tool: str, args: dict[str, Any] | None) -> list[str]:
        """Exact rule strings that would allow precisely this call (one per bash part)."""
        args = args or {}
        subjects = _subjects(tool, args, self.workspace)
        if tool == "webfetch":
            host = _host(subjects[0])
            return [f"webfetch(domain:{host})"] if host else []
        if tool in ("bash", "git", "edit"):
            return [f"{tool}({escape_glob(s)})" for s in subjects if s]
        if tool == "fileops":
            op, _, path = subjects[0].partition(":")
            return [f"fileops({op}:{escape_glob(path)})"]
        return [tool]

    def add_allow_always(self, tool: str, args: dict[str, Any] | None) -> list[str]:
        """Allow this exact call for the rest of the process and persist it for the workspace.

        Returns the rule strings added (empty if nothing was addable). A call
        with command substitution still asks afterwards (see module docs).
        """
        added: list[str] = []
        for text in self.exact_rules(tool, args):
            try:
                rule = parse_rule(text, ALLOW)
            except RuleParseError:
                continue
            if text not in self._stored:
                self._stored.append(text)
            if all(r.text != text for r in self.allow):
                self.allow.append(rule)
            added.append(text)
        if added and self._persist:
            try:
                save_store(self._store, self._stored)
            except OSError as exc:
                self.errors.append(f"cannot save {self._store}: {exc}")
        return added
