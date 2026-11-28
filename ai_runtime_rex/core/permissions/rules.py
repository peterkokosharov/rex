# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-sonnet-5-5, claude-opus-5-5
# Licensed under MIT License

r"""Configurable permission rules (allow / ask / deny) for tool calls.

Rule grammar: ``Tool(spec)`` or a bare ``Tool``. ``Tool`` is a tool name or an
fnmatch-style glob. The spec depends on the tool:

* bash       - command glob (``bash(pytest *)``)
* fileops    - ``op:path-glob`` (``fileops(write:**/.env)``), or just a path glob
* edit       - path glob (``edit(src/**)``)
* git        - ``op *`` (``git(push*)``)
* webfetch   - ``domain:host`` (suffix match) or a URL glob
* MCP tools  - no spec (``mcp__github__delete_*``)

Precedence: deny > ask > allow > allow buffer (paths, see allow_buffer.py) > mode default. ALLOW is a whitelist (local
and sandbox mode; in guard mode the container is the boundary and commands run unless a
deny/ask rule matches): a bash
command is auto-allowed by an allow rule only if it is a single simple command
made only of ASCII letters, digits, space and ``- _ . / , : = + @`` (plain
quotes only around whole words; see :func:`allow_blocker`); then the rule's
prefix match applies. Anything else (``; & | < > $ ` \ * ? ( ) { }``, newline,
non-ASCII, a command over 16 KB ...) asks at least; no shell syntax has to be
understood for ALLOW.

Bash DENY is best effort and unchanged: compound commands are split on ``&&``,
``||``, ``;``, ``|``, ``&`` and newlines (quote aware, plus a quote-blind
split); command substitutions, ``sh -c``, ``eval``, ``xargs``, ``find -exec``
and similar are checked too; deny and ask rules see every part normalised
(``NAME=value`` prefixes, wrappers such as ``sudo``/``env``/``timeout``
stripped, ``/bin/rm`` -> ``rm``, simple expansions; see :func:`deny_subjects`).
A command over 16 KB, with more than 256 parts, more than 32 substitutions or
substitutions nested deeper than 8 is "too complex to check": it is decided
before any full scan, never allowed, and denied only when a deny rule hits its
first 4 KB; otherwise it asks. It does not stop code run by an interpreter
(``python -c``, ``perl -e``, ``node -e``) or obfuscated shell. The Docker guard
is the real boundary; use it or allow-lists for anything that matters.

fileops ``list`` / ``glob`` / ``grep`` are also checked as a read of their
search root and of any entry below it, so a root inside a path whose reading is
denied is denied. Known limit: a search whose root is ABOVE a denied path
(``grep`` in ``.`` with ``fileops(read:secrets/**)`` denied) is not refused by
the rules; deny the search ops too, or rely on the guard.

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
from dataclasses import dataclass, field
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
_READ_TOOLS = {"load_skill", "read_skill_file"}  # render_diagram writes files: a write
_SEARCH_FILE_OPS = {"list", "glob", "grep"}
# Stand-in for "any entry below the search root" when a search is checked
# against deny rules for reading: matches ``*`` / ``**`` but no real name.
_ANY_CHILD = "\x00"
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

@dataclass
class BashScan:
    """Result of :func:`scan_bash`.

    ``parts``: the command split into simple parts (comments removed).
    ``seps``: for each part, the separator before it (``""`` for the first,
    else ``;``, ``&&``, ``||``, ``|``, ``&`` or ``\\n``).
    ``substitution``: ``$(``, backticks, ``<(``, ``>(`` seen outside single quotes.
    ``unparseable``: a quote is left open, a heredoc is not terminated, or a
    part cannot be split into words; the command is never auto-allowed.
    ``heredocs``: heredoc bodies (kept out of ``parts``). ``$(`` or a backtick
    anywhere in a body counts as substitution, whatever the quoting. ``$[``
    (old arithmetic) counts as substitution too.
    ``commented``: an unquoted ``#`` comment was dropped; bash and the scanner
    may disagree on where it starts (``$[ 1 #]``), so never auto-allowed.
    """

    parts: list[str]
    seps: list[str]
    substitution: bool = False
    unparseable: bool = False
    heredocs: list[str] = field(default_factory=list)
    commented: bool = False


def join_continuations(command: str) -> str:
    """Remove backslash-newline line continuations (``r\\<newline>m`` is ``rm``)."""
    return re.sub(r"\\\r?\n", "", command)


def scan_bash(command: str) -> BashScan:
    """Split a command line into parts, failing closed on anything unclear.

    Splits on ``&&``, ``||``, ``;``, ``|``, ``&`` and newlines outside quotes;
    an ``&`` or ``|`` that belongs to a redirect (``&>``, ``>&``, ``2>&1``,
    ``<&``, ``>|``) does not split. Understands ``'...'``, ``"..."``,
    ``$'...'`` (backslash escapes inside), backslash escapes, line
    continuations and unquoted ``#`` comments (to the end of the line).
    """
    import shlex

    # No global pre-join of backslash-newline: it is only a continuation when
    # the backslash is unquoted (or in double quotes), unescaped and not in a
    # comment, which only the scanner below knows.
    parts: list[str] = []
    seps: list[str] = []
    buf: list[str] = []
    sep = ""
    subst = False
    quote = ""
    i, n = 0, len(command)
    pending: list[tuple[str, bool]] = []  # heredocs waiting for the end of the line
    heredocs: list[str] = []
    broken = False
    commented = False

    def flush(next_sep: str) -> None:
        nonlocal sep
        s = "".join(buf).strip()
        if s:
            parts.append(s)
            seps.append(sep)
        sep = next_sep if (s or not parts) else _stronger(sep, next_sep)
        buf.clear()

    while i < n:
        c = command[i]
        if quote == "'":
            buf.append(c)
            if c == "'":
                quote = ""
            i += 1
            continue
        if quote == "$'":
            if c == "\\" and i + 1 < n:
                buf.append(command[i:i + 2])
                i += 2
                continue
            buf.append(c)
            if c == "'":
                quote = ""
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            # Unquoted or in double quotes: backslash-newline is a line
            # continuation and disappears; any other escaped char is kept.
            if command.startswith("\\\n", i):
                i += 2
            elif command.startswith("\\\r\n", i):
                i += 3
            else:
                buf.append(command[i:i + 2])
                i += 2
            continue
        if c == "`" or command.startswith(("$(", "$["), i):
            subst = True
        if quote == '"':
            buf.append(c)
            if c == '"':
                quote = ""
            i += 1
            continue
        if c == "#" and (not buf or buf[-1] in (" ", "\t")):
            commented = True
            j = command.find("\n", i)
            i = n if j < 0 else j  # comment: skip to the newline (which still splits)
            continue
        if command.startswith("$'", i):
            quote = "$'"
            buf.append("$'")
            i += 2
            continue
        if command.startswith("<<<", i):
            buf.append("<<<")
            i += 3
            continue
        if command.startswith("<<", i):
            j = i + 2
            strip_tabs = j < n and command[j] == "-"
            j += 1 if strip_tabs else 0
            while j < n and command[j] in " \t":
                j += 1
            k = j
            while k < n and command[k] not in " \t\r\n;|&<>()":
                k += 1
            delim = command[j:k].replace("'", "").replace('"', "").replace("\\", "")
            if delim:
                pending.append((delim, strip_tabs))
            else:
                broken = True
            buf.append(command[i:k])
            i = k
            continue
        if c in "\r\n" and pending:
            flush("\n")
            pos = i + (2 if command.startswith("\r\n", i) else 1)
            for delim, strip_tabs in pending:
                body: list[str] = []
                found = False
                while pos < n:
                    end = command.find("\n", pos)
                    line = command[pos:n if end < 0 else end]
                    pos = n if end < 0 else end + 1
                    check = line.rstrip("\r")
                    if (check.lstrip("\t") if strip_tabs else check) == delim:
                        found = True
                        break
                    body.append(line)
                broken = broken or not found
                heredocs.append("\n".join(body))
            pending.clear()
            i = pos
            continue
        if c in "'\"":
            quote = c
            buf.append(c)
        elif command.startswith(("<(", ">("), i):
            subst = True
            buf.append(c)
        elif command.startswith(("&&", "||"), i):
            flush(command[i:i + 2])
            i += 1
        elif c == "&" and (command.startswith("&>", i) or (buf and buf[-1] in ("<", ">"))):
            buf.append(c)  # redirect: &> file, >&2, 2>&1, <&3 - not a separator
        elif c == "|" and buf and buf[-1] == ">":
            buf.append(c)  # >| file (clobber) - not a pipe
        elif c in ";|&\n\r":
            flush("\n" if c in "\r\n" else c)
        else:
            buf.append(c)
        i += 1
    unparseable = bool(quote) or broken or bool(pending)
    flush("")
    if any("$(" in body or "`" in body for body in heredocs):
        subst = True
    for part in parts:
        try:
            shlex.split(part, comments=False, posix=True)
        except ValueError:
            unparseable = True
    return BashScan(parts, seps, subst, unparseable, heredocs, commented)


def _stronger(a: str, b: str) -> str:
    """Separator kept when empty parts sit between two separators (``a |& b``)."""
    return a if a == "|" else b


def split_bash(command: str) -> tuple[list[str], bool]:
    """Split a command line into parts. Returns (parts, has_substitution).

    See :func:`scan_bash` for the rules; ``has_substitution`` is true for
    ``$(``, backticks, ``<(``, ``>(`` outside single quotes.
    """
    scan = scan_bash(command)
    return scan.parts, scan.substitution


def _quote_blind_parts(command: str) -> list[str]:
    """Deny pass only: split on separators ignoring every quote and comment.

    Both the raw text and the text with backslash-newlines joined are split,
    so neither reading of an ambiguous continuation hides a part.
    """
    out: list[str] = []
    for text in (command, join_continuations(command)):
        for p in re.split(r"&&|\|\||[;|&\n\r]", text):
            p = p.strip()
            if p and p not in out:
                out.append(p)
    return out


def quote_mask(part: str) -> str:
    """``part`` with every quoted or escaped character replaced by ``\\x01``.

    Same length as ``part``; what is left visible is unquoted shell syntax.
    """
    out: list[str] = []
    quote = ""
    i, n = 0, len(part)
    while i < n:
        c = part[i]
        if quote:
            if c == "\\" and quote in ('"', "$'") and i + 1 < n:
                out.append("\x01\x01")
                i += 2
                continue
            if c == "'" and quote in ("'", "$'") or c == '"' and quote == '"':
                quote = ""
            out.append("\x01")
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            out.append("\x01\x01")
            i += 2
            continue
        if part.startswith("$'", i):
            quote = "$'"
            out.append("\x01\x01")
            i += 2
            continue
        if c in "'\"":
            quote = c
            out.append("\x01")
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


MAX_NEST_DEPTH = 8
_SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "ash", "mksh", "fish", "csh", "tcsh"}
# Programs that run code they read from stdin or an argument.
_INTERPRETERS = _SHELLS | {
    "busybox", "python", "python2", "python3", "pypy", "pypy3", "perl", "ruby", "node", "nodejs", "deno", "bun",
    "php", "lua", "tclsh", "wish", "Rscript", "pwsh", "powershell", "osascript", "cmd", "cmd.exe",
}
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# Shell reserved words and grouping tokens (only meaningful as the first word).
_RESERVED = {"{", "}", "(", ")", "!", "if", "then", "elif", "else", "fi", "while", "until", "do",
             "done", "case", "esac", "function", "time", "coproc", "select", "[[", "]]"}
_FUNC_DEF = re.compile(r"^[\w.:-]+\(\)\{?$")
# Wrappers that run the rest of the line as a command, with the options of
# each that take a separate argument (so the argument is skipped too).
_WRAPPERS: dict[str, set[str]] = {
    "env": {"-u", "--unset", "-C", "--chdir"},
    "command": set(),
    "builtin": set(),
    "exec": {"-a"},
    "nohup": set(),
    "time": {"-f", "--format", "-o", "--output"},
    "sudo": {"-u", "--user", "-g", "--group", "-C", "--close-from", "-D", "--chdir",
             "-h", "--host", "-p", "--prompt", "-r", "--role", "-t", "--type", "-U", "--other-user"},
    "doas": {"-u", "-C"},
    "nice": {"-n", "--adjustment"},
    "ionice": {"-c", "--class", "-n", "--classdata", "-p", "--pid", "-P", "--pgid", "-u", "--uid"},
    "timeout": {"-s", "--signal", "-k", "--kill-after"},
    "stdbuf": {"-i", "--input", "-o", "--output", "-e", "--error"},
    "chrt": {"-T", "--sched-runtime", "-P", "--sched-period", "-D", "--sched-deadline"},
    "taskset": set(),
    "setsid": set(),
    "busybox": set(),
    "watch": {"-n", "--interval", "-q", "--equexit"},
    "flock": {"-w", "--wait", "--timeout", "-E", "--conflict-exit-code", "-c", "--command"},
    "chroot": {"--userspec", "--groups"},
    "unshare": {"-S", "--setuid", "-G", "--setgid", "-R", "--root", "-w", "--wd"},
    "nsenter": {"-t", "--target", "-S", "--setuid", "-G", "--setgid"},
    "runuser": {"-u", "--user", "-g", "--group", "-G", "--supp-group", "-c", "--command"},
    "systemd-run": {"-p", "--property", "-u", "--unit", "-E", "--setenv", "--uid", "--gid", "-M",
                    "--machine", "--description", "--slice", "-H", "--host", "--working-directory",
                    "--nice"},
}
# Wrappers with positional arguments before the command (timeout DURATION,
# chrt PRIORITY, taskset MASK, flock LOCKFILE, chroot NEWROOT).
_WRAPPER_POSITIONALS = {"timeout": 1, "chrt": 1, "taskset": 1, "flock": 1, "chroot": 1}
# Programs whose -c / --command option value is a shell command.
_DASH_C = {"su", "script", "runuser", "flock"}
_XARGS_ARG_OPTS = {"-I", "-n", "-P", "-d", "-L", "-s", "-E", "-a", "--max-args", "--max-procs",
                   "--delimiter", "--max-lines", "--max-chars", "--eof", "--arg-file", "--replace"}
_SHELL_ARG_OPTS = {"-o", "+o", "-O", "+O", "--rcfile", "--init-file"}


def _basename(word: str) -> str:
    word = word.replace("\\", "/")
    return word.rsplit("/", 1)[-1] if "/" in word.rstrip("/") else word


def _words(part: str) -> list[str]:
    """Shell words of one command part (quotes removed); whitespace split if shlex fails."""
    import shlex
    try:
        return shlex.split(part, comments=False, posix=True)
    except ValueError:
        return part.split()


def _env_split(opt: str, words: list[str], i: int) -> tuple[list[str], int] | None:
    """``env -S 'cmd args'``: the value is itself a command line. Returns (words, i) or None."""
    if opt in ("-S", "--split-string"):
        if i < len(words):
            return _words(words[i]) + words[i + 1:], 0
        return [], 0
    if opt.startswith("--split-string="):
        return _words(opt.split("=", 1)[1]) + words[i:], 0
    if opt.startswith("-S") and len(opt) > 2:
        return _words(opt[2:]) + words[i:], 0
    return None


def _strip_wrappers(words: list[str]) -> list[str]:
    """Drop leading ``NAME=value`` words, reserved words / grouping tokens and
    wrapper commands (``sudo``, ``env``, ``timeout`` ...)."""
    i, n = 0, len(words)
    while i < n:
        w = words[i]
        if _ASSIGN.match(w) or w in _RESERVED or _FUNC_DEF.match(w):
            i += 1
            if w == "function":
                i += 1  # the function name
            continue
        name = _basename(w)
        if name not in _WRAPPERS:
            break
        arg_opts = _WRAPPERS[name]
        i += 1
        restart = None
        while i < n and words[i].startswith("-") and words[i] != "-":
            opt = words[i]
            i += 1
            if opt == "--":
                break
            if name == "env":
                restart = _env_split(opt, words, i)
                if restart is not None:
                    break
            if opt in arg_opts and "=" not in opt:
                i += 1  # option argument
        if restart is not None:
            words, i = restart
            n = len(words)
            continue
        i += _WRAPPER_POSITIONALS.get(name, 0)
    out = words[i:]
    if out and any(ch in out[0] for ch in " \t\n"):
        out = _words(" ".join(out))  # watch 'rm -rf /': one word holding a command line
    if out:
        out = [_basename(out[0])] + out[1:]
    return out


_DYNAMIC_WORDS = ("eval", "xargs")
_REDIRECT_WORD = re.compile(r"^\d*[<>]|^&>")


def dynamic_command_word(command: str, subjects: list[str] | None = None) -> bool:
    """True if some command in ``command`` runs a word that is not known before run time.

    Heuristic over :func:`deny_subjects` (wrappers, reserved words, grouping, ``sh -c``
    bodies and substitution bodies already unpacked): the command word of a subject
    contains ``$`` or a backtick, a glob character (``* ? [``), starts with a redirection
    (``>f cmd`` hides ``cmd``), or is ``eval`` / ``xargs``; ``case`` heads and patterns are skipped. ``echo $(pwd)`` is not dynamic:
    only the command word matters, not the arguments. Too deep nesting counts as dynamic."""
    too_deep = False
    if subjects is None:
        subjects, too_deep = deny_subjects(command)
    if too_deep:
        return True
    for subject in subjects:
        raw = _words(subject)
        if raw and _REDIRECT_WORD.match(raw[0]):
            return True
        if raw[:1] == ["case"]:
            raw = raw[3:]  # `case WORD in`: WORD is data, not a command
        words = _strip_wrappers(raw)
        while len(words) > 1 and words[0].endswith(")") and not any(c in words[0] for c in "$`"):
            words = _strip_wrappers(words[1:])  # case pattern `x)` / `*)` before the command
        word = words[0] if words else ""
        if word and (any(c in word for c in "$`*?[") or _REDIRECT_WORD.match(word)
                     or _basename(word) in _DYNAMIC_WORDS):
            return True
    return False


MAX_BODIES = 32
MAX_SUBST_DEPTH = 8
MAX_COMMAND_CHARS = 16 * 1024
MAX_PARTS = 256
# A too-complex command is checked against deny rules on this prefix only.
PREFIX_SCAN_CHARS = 4096


def _substitutions(command: str, limit: int = MAX_BODIES) -> tuple[list[str], int, int]:
    """Substitution bodies, ignoring quoting: (first ``limit`` bodies, count, max depth).

    ``$(...)``, ``<(...)``, ``>(...)`` and backticks, at any nesting level, in
    order of their start; one linear pass. Quotes are deliberately not
    honoured: a body inside single quotes is harmless but checking it too
    only makes deny stricter.
    """
    spans: list[tuple[int, int]] = []
    stack: list[tuple[int, bool]] = []  # (body start, is a substitution)
    depth = max_depth = 0
    tick: int | None = None
    i, n = 0, len(command)
    while i < n:
        if command.startswith(("$(", "<(", ">("), i):
            stack.append((i + 2, True))
            depth += 1
            max_depth = max(max_depth, depth)
            i += 2
            continue
        c = command[i]
        if c == "(":
            stack.append((i + 1, False))
        elif c == ")" and stack:
            start, sub = stack.pop()
            if sub:
                spans.append((start, i))
                depth -= 1
        elif c == "`":
            if tick is None:
                tick = i + 1
            else:
                spans.append((tick, i))
                tick = None
        i += 1
    spans.extend((start, n) for start, sub in stack if sub)  # unclosed: to the end
    if tick is not None:
        spans.append((tick, n))
    spans.sort()
    bodies: list[str] = []
    for start, end in spans:
        if len(bodies) >= limit:
            break
        if command[start:end].strip():
            bodies.append(command[start:end])
    return bodies, len(spans), max_depth


def _substitution_bodies(command: str) -> list[str]:
    """The first :data:`MAX_BODIES` substitution bodies (see :func:`_substitutions`)."""
    return _substitutions(command)[0]


def too_complex(command: str) -> str:
    """Why ``command`` is too complex to check (then it is never auto-allowed), or ``""``.

    Cheap, linear checks only; :meth:`PermissionRules.evaluate` runs them
    before any full scan. Separators are counted quote-blind (over-counting
    only makes the limit stricter).
    """
    if len(command) > MAX_COMMAND_CHARS:
        return f"longer than {MAX_COMMAND_CHARS // 1024} KB"
    if len(re.findall(r"&&|\|\||[;|&\n\r]", command)) + 1 > MAX_PARTS:
        return f"more than {MAX_PARTS} parts"
    _, count, depth = _substitutions(command, limit=0)
    if count > MAX_BODIES:
        return f"more than {MAX_BODIES} substitutions"
    if depth > MAX_SUBST_DEPTH:
        return f"substitutions nested deeper than {MAX_SUBST_DEPTH}"
    return ""


def _shell_c_arg(words: list[str]) -> str | None:
    """The command string of ``bash [options] -c [--] 'cmd'``, or None."""
    k, has_c = 1, False
    while k < len(words):
        w = words[k]
        if w == "--":
            k += 1
            break
        if w in _SHELL_ARG_OPTS:
            k += 2
            continue
        if w.startswith("--"):
            k += 1
            continue
        if len(w) > 1 and w[0] in "-+":
            if w[0] == "-" and "c" in w[1:]:
                has_c = True
            k += 1
            continue
        break
    if has_c and k < len(words):
        return words[k]
    return None


def _dash_c_value(words: list[str]) -> str | None:
    """Value of ``-c`` / ``--command`` (also ``-qc``, ``--command=``) anywhere in ``words``."""
    for k, w in enumerate(words[1:], 1):
        if w.startswith("--command="):
            return w.split("=", 1)[1]
        if w == "--command" or (w.startswith("-") and not w.startswith("--") and w.endswith("c")):
            return words[k + 1] if k + 1 < len(words) else None
    return None


def _inner_commands(words: list[str]) -> list[str]:
    """Command texts a part runs indirectly: ``sh -c``, ``su -c``, ``eval``,
    ``xargs``, ``find -exec``, ``cmd /c``."""
    if not words:
        return []
    head = _basename(words[0])
    inner: list[str] = []
    if head in _SHELLS:
        text = _shell_c_arg(words)
        if text is not None:
            inner.append(text)
    elif head in _DASH_C:
        text = _dash_c_value(words)
        if text is not None:
            inner.append(text)
    elif head.lower() in ("cmd", "cmd.exe"):
        for k, w in enumerate(words[1:], 1):
            if w.lower() in ("/c", "//c", "/k", "//k"):
                inner.append(" ".join(words[k + 1:]))
                break
    elif head == "eval":
        inner.append(" ".join(words[1:]))
    elif head == "xargs":
        k = 1
        while k < len(words) and words[k].startswith("-"):
            opt = words[k]
            k += 1
            if opt == "--":
                break
            if opt in _XARGS_ARG_OPTS:
                k += 1
        if k < len(words):
            inner.append(" ".join(words[k:]))
    if head == "find" or "find" in words:
        k = 0
        while k < len(words):
            if words[k] in ("-exec", "-execdir", "-ok", "-okdir"):
                end = k + 1
                while end < len(words) and words[end] not in (";", "+", "\\;"):
                    end += 1
                if end > k + 1:
                    inner.append(" ".join(words[k + 1:end]))
                k = end
            k += 1
    return [t for t in inner if t.strip()]


def _herestring(part: str) -> str | None:
    """The word after an unquoted ``<<<``, quotes removed, or None."""
    mask = quote_mask(part)
    k = mask.find("<<<")
    if k < 0:
        return None
    rest = part[k + 3:].lstrip()
    words = _words(rest)
    return words[0] if words else None


def _reads_stdin_code(words: list[str]) -> bool:
    """An interpreter with no script/command argument (``sh``, ``python -``) or ``source /dev/stdin``."""
    if not words:
        return False
    head = _basename(words[0])
    if head in ("source", "."):
        return any(w in ("/dev/stdin", "-", "/proc/self/fd/0", "/dev/fd/0") for w in words[1:])
    if head not in _INTERPRETERS:
        return False
    args = [w for w in words[1:] if not (w.startswith("-") and w != "-")]
    return not args or args[0] in ("-", "/dev/stdin")


def _expand_variants(part: str, assigns: dict[str, str]) -> list[str]:
    """Deny pass: forms of ``part`` with simple shell expansions applied.

    ``$IFS`` -> space, ``$NAME`` of earlier ``NAME=value`` parts, brace lists
    ``{a,b}`` -> ``a b``, function headers and grouping characters removed.
    """
    text = re.sub(r"\$\{IFS\}|\$IFS\b", " ", part)
    for name, value in assigns.items():
        text = re.sub(r"\$\{" + re.escape(name) + r"\}|\$" + re.escape(name) + r"\b",
                      lambda _m, v=value: v, text)
    text = re.sub(r"\{([^{}\s]*,[^{}\s]*)\}", lambda m: m.group(1).replace(",", " "), text)
    text = re.sub(r"[\w.:-]+\s*\(\s*\)", " ", text)
    text = re.sub(r"(?<!\$)[(){}]", " ", text)
    return [text] if text != part else []


def deny_subjects(command: str) -> tuple[list[str], bool]:
    """Subjects used for deny (and ask) matching; returns (subjects, too_deep).

    Best effort, never a boundary. For every part (quote-aware split plus a
    quote-blind split): the part with whitespace squeezed, and the part with
    leading ``NAME=value`` words, reserved words / grouping tokens and
    wrappers stripped and the command word reduced to its basename
    (``sudo /bin/rm -rf /`` -> ``rm -rf /``), also after simple expansions
    (``$IFS``, earlier ``x=...`` assignments, ``{a,b}``). The bodies of
    command substitutions (any nesting level) and the commands run by
    ``sh -c``, ``su -c``, ``env -S``, ``eval``, ``xargs``, ``find -exec``,
    ``cmd /c``, a herestring or ``echo ... |`` fed to a shell are checked the
    same way; that nesting is followed up to :data:`MAX_NEST_DEPTH` levels,
    deeper nesting sets ``too_deep`` (the caller then never auto-allows).
    """
    seen: dict[str, None] = {}  # ordered set: membership must stay O(1)
    too_deep = False

    def add(text: str) -> None:
        text = text.strip()
        if text:
            seen.setdefault(text, None)

    def walk(command: str, depth: int) -> None:
        nonlocal too_deep
        nested: list[str] = []
        for text in [command] + _substitution_bodies(command):
            scan = scan_bash(text)
            assigns: dict[str, str] = {}
            prev_words: list[str] = []
            for idx, part in enumerate(scan.parts):
                words = _words(part)
                if words and all(_ASSIGN.match(w) for w in words):
                    for w in words:
                        name, _, value = w.partition("=")
                        assigns[name] = value
                stripped = _strip_wrappers(words)
                here = _herestring(part)
                if here and stripped and _basename(stripped[0]) in _INTERPRETERS:
                    nested.append(here)
                if (scan.seps[idx] == "|" and _reads_stdin_code(stripped) and prev_words
                        and prev_words[0] in ("echo", "printf")):
                    nested.append(" ".join(w for w in prev_words[1:] if not w.startswith("-")))
                prev_words = stripped
                for variant in _expand_variants(part, assigns):
                    words_v = _strip_wrappers(_words(variant))
                    add(" ".join(words_v))
                    nested.extend(_inner_commands(words_v))
            nested.extend(scan.heredocs)  # deny only: a body may be fed to a shell
            candidates = scan.parts + _quote_blind_parts(text)
            for part in candidates:
                add(" ".join(part.split()))
                for variant in (part, part.replace("'", "").replace('"', "")):
                    raw = _words(variant)
                    words = _strip_wrappers(raw)
                    add(" ".join(words))
                    nested.extend(_inner_commands(raw) + _inner_commands(words))
        if nested and depth >= MAX_NEST_DEPTH:
            too_deep = True
            return
        for text in dict.fromkeys(nested):
            walk(text, depth + 1)

    walk(command, 0)
    return list(seen), too_deep


_SAFE_CHARS = re.compile(r"[A-Za-z0-9 _.,:=+@/'\"-]*")


def allow_blocker(command: str) -> str:
    """Why ``command`` can never be auto-allowed, or ``""`` (whitelist, not a parser).

    Only a single simple command made of safe characters is eligible: ASCII
    letters, digits, space and ``- _ . / , : = + @``, plus plain ``'`` / ``"``
    quotes around whole words. Everything else (dollar, backtick, backslash,
    ``! # % ^ * ? [ ] { } ( ) < > ; & | ~``, tab, newline, control and non-ASCII
    characters, an unbalanced quote) is never auto-allowed, so no shell syntax
    has to be understood.
    """
    if not command.strip():
        return "empty command"
    if not _SAFE_CHARS.fullmatch(command):
        return "it has characters outside the safe set"
    first = command.split()[0]
    if "=" in first or first.startswith("-"):
        # `ls=1 echo x` is an assignment followed by the next word, not `ls`.
        return "the first word is an assignment or an option"
    quote = ""
    for i, c in enumerate(command):
        if quote:
            if c == quote:
                quote = ""
                if i + 1 < len(command) and command[i + 1] != " ":
                    return "quotes must surround whole words"
        elif c in "'\"":
            if i and command[i - 1] not in " =":
                return "quotes must surround whole words"
            quote = c
    if quote:
        return "unbalanced quote"
    return ""


def _deny_parts(command: str) -> list[str]:
    return deny_subjects(command)[0]


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
        # glob searches below ``root``; every other op works on ``path`` (same target as the allow buffer).
        raw = args.get("root") if _fileops_op(args) == "glob" and args.get("root") else args.get("path")
        return [f"{_fileops_op(args)}:{normalize_path(str(raw or '.'), workspace)}"]
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

    Read-only tools are allowed everywhere (unless ``defaults.read`` is ``ask``). Network and MCP ask everywhere.
    Writes/exec: guard -> ``defaults.guard`` (default allow), sandbox ->
    ``defaults.sandbox`` (default ask), local (and unknown modes) -> ask, fixed.
    """
    defaults = defaults or {}
    if kind == KIND_READ:
        # ``permissions.defaults.read: ask`` (env AI_REX_PERMISSIONS__DEFAULTS__READ=ask) makes reads ask too.
        return ASK if str(defaults.get("read", "allow")).lower() == "ask" else ALLOW
    if kind in (KIND_NETWORK, KIND_MCP):
        return ASK
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


def buffer_target(tool: str, args: dict[str, Any]) -> str | None:
    """The path an allow-buffer entry is about for this call, or None (not a path call).

    fileops: ``path`` (``root`` for glob, ``.`` when missing); edit: ``path``; bash: the working
    directory ``cwd`` (``.`` when missing).
    """
    if tool == "fileops":
        op = _fileops_op(args)
        raw = args.get("root") if op == "glob" else args.get("path")
        return str(raw) if isinstance(raw, str) and raw.strip() else "."
    if tool == "edit":
        raw = args.get("path")
        return str(raw) if isinstance(raw, str) and raw.strip() else None
    if tool == "bash":
        raw = args.get("cwd")
        return str(raw) if isinstance(raw, str) and raw.strip() else "."
    return None


# ------------------------------------------------------------------- store

def store_path(workspace: str | os.PathLike[str], base_dir: str | os.PathLike[str] | None = None) -> Path:
    """``<base>/<sha1-of-workspace>.yaml`` (base default ``~/.config/ai-rex/permissions``)."""
    base = Path(base_dir) if base_dir else Path.home() / ".config" / "ai-rex" / "permissions"
    digest = hashlib.sha1(_norm(str(workspace)).encode("utf-8")).hexdigest()
    return base / f"{digest}.yaml"


def load_store(path: Path) -> list[str]:
    """Read allow rules from a store file. Missing or unreadable file = empty."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rules: list[str] = []
    section = ""
    for line in lines:
        head = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(?:#.*)?$", line)
        if head:  # a top-level key starts a section; only "allow:" items count
            section = head.group(1)
            continue
        m = re.match(r"^\s+-\s+(.*?)\s*$", line) or re.match(r"^-\s+(.*?)\s*$", line)
        if not m or section != "allow":
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


def save_store(path: Path, rules: list[str]) -> list[str]:
    """Merge ``rules`` into the store file atomically; returns the merged list.

    The current file content is read first, so a second rex process working in
    the same workspace does not lose its rules; the new file is written to a
    temporary file next to it and moved into place with ``os.replace``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = load_store(path)
    for rule in rules:
        if rule not in merged:
            merged.append(rule)
    body = "allow:\n" + "".join(f"  - {json.dumps(r)}\n" for r in merged)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    return merged


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
        # Persistent allow buffer (see allow_buffer.py); attached by the gate when configured.
        self.buffer: Any = None
        self._store = store_path(self.workspace, store_dir)
        self._stored: list[str] = load_store(self._store) if persist else []
        for text in self._stored:
            try:
                self.allow.append(parse_rule(text, ALLOW))
            except RuleParseError as exc:
                self.errors.append(str(exc))

    def use_store_file(self, path: str | os.PathLike[str]) -> None:
        """Keep allow-always rules in ``path`` (one file for every workspace) instead of the per-workspace store."""
        self._store = Path(path).expanduser()
        self._persist = True
        for text in load_store(self._store):
            if text in self._stored:
                continue
            self._stored.append(text)
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
        """Decide one tool call. Precedence: deny > ask > allow > allow buffer > mode default."""
        args = args or {}
        kind = classify(tool, args)
        if tool == "bash":
            # Size first: no full scan of a command that is too big or too
            # nested to check in bounded time. Never ALLOW; DENY only when a
            # cheap scan of the beginning already hits a deny rule.
            command = str(args.get("command", ""))
            why = too_complex(command)
            if why:
                prefix_subjects = deny_subjects(command[:PREFIX_SCAN_CHARS])[0]
                for rule in self.deny:
                    if any(rule.matches_subject(tool, s) for s in prefix_subjects):
                        return RuleVerdict(DENY, kind, "deny", rule.text, f"Permission denied (rule {rule.text})")
                return RuleVerdict(ASK, kind, "unsafe", None, f"Command too complex to check ({why})")
        subjects = _subjects(tool, args, self.workspace)
        unsafe = ""
        strict_subjects = subjects
        if tool == "bash":
            command = str(args.get("command", ""))
            strict_subjects, too_deep = deny_subjects(command)
            why = allow_blocker(command)
            if why and mode != MODE_GUARD:
                # Guard mode: the container is the boundary, no whitelist there.
                unsafe = f"Auto-allow only for a single simple command of safe characters ({why})"
            elif too_deep and mode != MODE_GUARD:
                unsafe = "Nested shell commands too deep to check"
            elif args.get("env"):
                # Extra variables (PATH, LD_PRELOAD, GIT_SSH_COMMAND ...) change what runs.
                unsafe = "A command with extra environment variables is never auto-allowed"

        elif tool == "fileops" and _fileops_op(args) in _SEARCH_FILE_OPS:
            # list/glob/grep read below their root: a root inside a path whose
            # reading is denied is denied too (fileops(read:secrets/**) covers
            # grep in secrets/sub). A root ABOVE a denied path is not caught.
            root = subjects[0].partition(":")[2]
            child = _ANY_CHILD if root == "." else root.rstrip("/") + "/" + _ANY_CHILD
            strict_subjects = subjects + [f"read:{root}", f"read:{child}"]

        buffer_path = buffer_target(tool, args)
        if (self.buffer is not None and buffer_path is not None and kind == KIND_WRITE
                and self.buffer.protects(buffer_path)):
            return RuleVerdict(DENY, kind, "deny", None,
                               "Permission denied (the allow buffer folder is protected)")

        # Deny and ask see the normalised parts too (wrappers stripped, nested
        # commands); allow only ever sees the parts as written.
        for rule in self.deny:
            if any(rule.matches_subject(tool, s) for s in strict_subjects or [""]):
                return RuleVerdict(DENY, kind, "deny", rule.text, f"Permission denied (rule {rule.text})")
        for rule in self.ask:
            if any(rule.matches_subject(tool, s) for s in strict_subjects or [""]):
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
        if (decision == ASK and self.buffer is not None and buffer_path is not None
                and kind in (KIND_READ, KIND_WRITE)):
            # After deny, ask and allow rules, before the mode default: a path the user answered
            # "allow always" for (or a folder above it), inside the workspace only.
            hit = self.buffer.covers(kind, buffer_path)
            if hit is not None:
                return RuleVerdict(ALLOW, kind, "buffer", None, f"allow buffer: {kind} {hit}")
        if (tool == "bash" and decision == ALLOW and mode == MODE_GUARD
                and any(r.tool_matches(tool) for r in self.deny + self.ask)
                and (too_deep or dynamic_command_word(str(args.get("command", "")), strict_subjects))):
            # Guard mode has no whitelist, so deny/ask rules are the only brake. A command whose
            # word is built at run time (`$(echo rm) -rf /`, backticks, globs, xargs, also behind
            # sudo/env/time/`(`/`if`...) cannot be matched against them without a real shell
            # parser: ask instead of allowing. Heuristic; the container is the real boundary.
            return RuleVerdict(ASK, kind, "unsafe", None,
                               "Command word built dynamically; cannot be checked against deny rules")
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
        if tool == "bash" and too_complex(str(args.get("command", ""))):
            return []  # never store rules for a command too complex to check
        subjects = _subjects(tool, args, self.workspace)
        if tool == "webfetch":
            host = _host(subjects[0])
            # Escaped: a host such as "*" must not become an allow-everything rule.
            return [f"webfetch(domain:{escape_glob(host)})"] if host else []
        if tool == "bash":
            # Never store junk such as bash(1) or bash(>&2): a part made only of
            # digits and redirect characters is not a command.
            return [f"bash({escape_glob(s)})" for s in subjects
                    if s and not re.fullmatch(r"[\d\s<>&|-]*", s)]
        if tool in ("git", "edit"):
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
                self._stored = save_store(self._store, self._stored)
            except OSError as exc:
                self.errors.append(f"cannot save {self._store}: {exc}")
        return added
