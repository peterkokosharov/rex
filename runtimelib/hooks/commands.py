# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-sonnet-5-5
# Licensed under MIT License

"""User-configured hook commands (pre_tool / post_tool).

A hook is an external command (argument list, never a shell) that receives one
JSON object on stdin and runs with the workspace as its working directory.

Pre-tool contract:
  * exit 0, empty stdout      -> no opinion
  * exit 0, stdout JSON       -> {"decision": "allow|deny|ask", "reason": "..."}
  * exit 2                    -> deny, stderr is the reason
  * any other exit / timeout / spawn failure / malformed output
                              -> ``on_error`` (``deny`` by default, or ``ignore``)

Post-tool: stdout text (at most 2 KB) is returned as a note. A post hook can
never undo the call; failures are reported in the note, never raised.

Config shape (see docs/plans/2026-11-23-rex-gaps.md)::

    hooks:
      pre_tool:  [{match: "bash|edit", command: ["python", "check.py"], timeout: 10}]
      post_tool: [{match: "edit", command: ["ruff", "format"], timeout: 20}]
      on_error: deny
"""

from __future__ import annotations

import fnmatch
import json
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

DEFAULT_TIMEOUT = 10.0
MAX_NOTE_CHARS = 2048
MAX_REASON_CHARS = 2048
ON_ERROR_VALUES = ("deny", "ignore")
DECISIONS = ("allow", "deny", "ask")
# Strictness order for combining several pre hooks: deny > ask > allow.
_RANK = {"deny": 3, "ask": 2, "allow": 1}


@dataclass(frozen=True)
class HookCommand:
    """One configured hook command."""

    match: str
    command: tuple[str, ...]
    timeout: float = DEFAULT_TIMEOUT

    def matches(self, tool: str) -> bool:
        """Match a tool name against ``a|b`` alternatives, each an fnmatch glob.

        An empty match or ``*`` matches every tool. Matching is case-sensitive.
        """
        pattern = (self.match or "*").strip() or "*"
        return any(
            fnmatch.fnmatchcase(tool, part.strip())
            for part in pattern.split("|")
            if part.strip()
        )


@dataclass
class PreHookResult:
    """Combined outcome of all matching pre-tool hooks.

    ``decision`` is ``None`` (no opinion), ``allow``, ``deny`` or ``ask``.
    ``error`` is True when the decision came from a failed hook handled by
    ``on_error`` (so callers can tell a crash from a deliberate deny).
    """

    decision: Optional[str] = None
    reason: str = ""
    error: bool = False

    @property
    def denied(self) -> bool:
        return self.decision == "deny"


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _decode(data: Optional[bytes]) -> str:
    return (data or b"").decode("utf-8", errors="replace")


def parse_hooks(raw: Any) -> list[HookCommand]:
    """Parse a config list into HookCommand objects.

    Entries that are not mappings or have no usable command list are skipped.
    """
    hooks: list[HookCommand] = []
    if not isinstance(raw, (list, tuple)):
        return hooks
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        command = entry.get("command")
        if not isinstance(command, (list, tuple)) or not command:
            continue
        if not all(isinstance(c, str) for c in command) or not command[0]:
            continue
        try:
            timeout = float(entry.get("timeout", DEFAULT_TIMEOUT))
        except (TypeError, ValueError):
            timeout = DEFAULT_TIMEOUT
        if timeout <= 0:
            timeout = DEFAULT_TIMEOUT
        match = entry.get("match", "*")
        hooks.append(HookCommand(
            match=match if isinstance(match, str) else "*",
            command=tuple(command),
            timeout=timeout,
        ))
    return hooks


@dataclass
class _Outcome:
    returncode: Optional[int]
    stdout: str
    stderr: str
    failure: str = ""  # non-empty: spawn failure or timeout


class CommandHookRunner:
    """Runs configured pre_tool / post_tool hook commands."""

    def __init__(
        self,
        pre_tool: Sequence[HookCommand] = (),
        post_tool: Sequence[HookCommand] = (),
        on_error: str = "deny",
        cwd: Optional[str] = None,
        session: Optional[str] = None,
        mode: Optional[str] = None,
    ) -> None:
        self.pre_tool = list(pre_tool)
        self.post_tool = list(post_tool)
        self.on_error = on_error if on_error in ON_ERROR_VALUES else "deny"
        self.cwd = cwd
        self.session = session
        self.mode = mode

    @classmethod
    def from_config(
        cls,
        hooks_config: Optional[Mapping[str, Any]],
        cwd: Optional[str] = None,
        session: Optional[str] = None,
        mode: Optional[str] = None,
    ) -> "CommandHookRunner":
        """Build from the ``hooks`` section of the user config (may be None)."""
        cfg: Mapping[str, Any] = hooks_config if isinstance(hooks_config, Mapping) else {}
        on_error = cfg.get("on_error", "deny")
        return cls(
            pre_tool=parse_hooks(cfg.get("pre_tool")),
            post_tool=parse_hooks(cfg.get("post_tool")),
            on_error=on_error if isinstance(on_error, str) else "deny",
            cwd=cwd,
            session=session,
            mode=mode,
        )

    @property
    def has_hooks(self) -> bool:
        return bool(self.pre_tool or self.post_tool)

    # -- public API ---------------------------------------------------------

    def run_pre(self, tool: str, args: Optional[Mapping[str, Any]] = None) -> PreHookResult:
        """Run all matching pre hooks; the strictest decision wins (deny > ask > allow)."""
        best = PreHookResult()
        payload = self._payload("pre_tool", tool, args)
        for hook in self.pre_tool:
            if not hook.matches(tool):
                continue
            result = self._run_one_pre(hook, payload)
            if result.decision is None:
                continue
            if _RANK[result.decision] > _RANK.get(best.decision or "", 0):
                best = result
            if best.decision == "deny":
                break  # nothing can be stricter; skip remaining hooks
        return best

    def run_post(
        self,
        tool: str,
        args: Optional[Mapping[str, Any]] = None,
        ok: bool = True,
        error: Optional[str] = None,
        result_preview: str = "",
    ) -> str:
        """Run all matching post hooks; returns the joined stdout notes (<= 2 KB)."""
        payload = self._payload("post_tool", tool, args)
        payload["ok"] = bool(ok)
        payload["error"] = error
        payload["result_preview"] = result_preview
        notes: list[str] = []
        for hook in self.post_tool:
            if not hook.matches(tool):
                continue
            out = self._execute(hook, payload)
            if out.failure:
                notes.append(f"[hook {hook.command[0]}: {out.failure}]")
            elif out.stdout.strip():
                notes.append(out.stdout.strip())
        return _clip("\n".join(notes), MAX_NOTE_CHARS)

    # -- internals ----------------------------------------------------------

    def _payload(self, event: str, tool: str, args: Optional[Mapping[str, Any]]) -> dict[str, Any]:
        return {
            "event": event,
            "tool": tool,
            "args": dict(args or {}),
            "cwd": self.cwd,
            "session": self.session,
            "mode": self.mode,
        }

    def _execute(self, hook: HookCommand, payload: Mapping[str, Any]) -> _Outcome:
        argv = list(hook.command)
        # Resolve through PATH ourselves so Windows launchers (npx.cmd) work.
        resolved = shutil.which(argv[0])
        if resolved:
            argv[0] = resolved
        try:
            data = json.dumps(payload, default=str).encode("utf-8")
        except (TypeError, ValueError) as exc:  # pragma: no cover - default=str covers it
            return _Outcome(None, "", "", f"cannot encode payload: {exc}")
        try:
            proc = subprocess.run(
                argv,
                input=data,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.cwd or None,
                timeout=hook.timeout,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            return _Outcome(None, "", "", f"timed out after {hook.timeout:g}s")
        except (OSError, ValueError) as exc:
            return _Outcome(None, "", "", f"cannot run: {exc}")
        return _Outcome(proc.returncode, _decode(proc.stdout), _decode(proc.stderr))

    def _on_error(self, hook: HookCommand, why: str) -> PreHookResult:
        if self.on_error == "ignore":
            return PreHookResult()
        return PreHookResult("deny", _clip(f"pre_tool hook {hook.command[0]} failed: {why}", MAX_REASON_CHARS), True)

    def _run_one_pre(self, hook: HookCommand, payload: Mapping[str, Any]) -> PreHookResult:
        out = self._execute(hook, payload)
        if out.failure:
            return self._on_error(hook, out.failure)
        if out.returncode == 2:
            reason = _clip(out.stderr, MAX_REASON_CHARS) or f"denied by hook {hook.command[0]}"
            return PreHookResult("deny", reason)
        if out.returncode != 0:
            detail = _clip(out.stderr, 200)
            why = f"exit {out.returncode}" + (f": {detail}" if detail else "")
            return self._on_error(hook, why)
        text = out.stdout.strip()
        if not text:
            return PreHookResult()
        try:
            obj = json.loads(text)
        except ValueError:
            return self._on_error(hook, "stdout is not valid JSON")
        if not isinstance(obj, dict):
            return self._on_error(hook, "stdout JSON is not an object")
        decision = obj.get("decision")
        if decision is None:
            return PreHookResult()
        if decision not in DECISIONS:
            return self._on_error(hook, f"unknown decision {decision!r}")
        reason = obj.get("reason")
        return PreHookResult(decision, _clip(reason, MAX_REASON_CHARS) if isinstance(reason, str) else "")
