# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-sonnet-5-5, claude-opus-5-5
# Licensed under MIT License

"""Permission gate for tool calls in the agent loop.

Order of decisions for one call (strictest first):

1. a deny rule denies, whatever hooks say;
2. a pre_tool hook that denies (or fails with ``on_error: deny``) denies;
3. an ask (from a rule, the mode default, or a hook) goes to the asker;
4. otherwise the call is allowed.

A hook "allow" turns an ask into an allow but never overrides a deny rule.
``_role`` / ``_agent_id`` are internal and are stripped before rules, hooks and
askers see the arguments.

Asking is pluggable: any object with ``ask(PermissionRequest) -> str`` works.
The answer is ``allow_once``, ``allow_always``, ``allow_always_folder`` or
``deny`` (anything else is a deny). With an allow buffer attached to the rules
(``permissions.allow_buffer_size`` > 0), "always" for a fileops / edit path
inside the workspace is remembered in the buffer (``allow_always_folder``: its
folder); other calls keep the exact allow-always rules. The default :class:`DenyAsker` denies, which is the right behaviour
for one-shot runs without a UI.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Protocol

from ..hooks.commands import CommandHookRunner
from ..permissions.base import PermissionResult
from ..permissions.rules import (
    MODE_GUARD,
    MODE_LOCAL,
    MODE_SANDBOX,
    PermissionRules,
    _host,
)
from ..permissions.rules import buffer_target
from ..tools.base import Tool, default_describe

ALLOW_ONCE = "allow_once"
ALLOW_ALWAYS = "allow_always"
ALLOW_ALWAYS_FOLDER = "allow_always_folder"
DENY = "deny"
INTERNAL_ARGS = ("_role", "_agent_id")
MAX_DETAIL_CHARS = 20000


@dataclass
class PermissionRequest:
    """What an asker is shown. ``args`` has the internal keys removed."""

    id: str
    tool: str
    kind: str
    summary: str
    detail: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    rule: Optional[str] = None
    reason: str = ""
    # Allow buffer: what "always" remembers ("read src/app.py"), and the folder offered for
    # "always for the folder" ("" = no folder choice).
    remember: str = ""
    remember_folder: str = ""


class Asker(Protocol):
    """Interface P4 implements for REPL / JSONL / ACP."""

    def ask(self, request: PermissionRequest) -> str:
        """Return ``allow_once``, ``allow_always`` or ``deny``. May block."""


class DenyAsker:
    """Default asker: denies every question (one-shot, no UI)."""

    def ask(self, request: PermissionRequest) -> str:
        return DENY


@dataclass
class GateDecision:
    allowed: bool
    reason: str = ""
    source: str = ""  # rule / hook / user / default


def strip_internal(args: Mapping[str, Any] | None) -> dict[str, Any]:
    return {k: v for k, v in (args or {}).items() if k not in INTERNAL_ARGS}


def resolve_mode(config: Any) -> str:
    """local when ``cli.unsafe_local_tools`` is set; else sandbox if configured; else guard."""
    if config is not None and config.get("cli.unsafe_local_tools"):
        return MODE_LOCAL
    configured = str(config.get("permissions.mode", "") if config is not None else "").lower()
    return MODE_SANDBOX if configured == MODE_SANDBOX else MODE_GUARD


def attach_allow_buffer(rules: PermissionRules, config: Any, workspace: str) -> None:
    """Attach the workspace's allow buffer when ``permissions.allow_buffer_size`` is set (> 0).

    The plain REPL / one-shot set it (default 1024, ``--allow-buffer`` / ``AI_REX_ALLOW_BUFFER``); a config file
    may set it too. A bad value or an unusable state folder leaves the buffer off (noted in ``rules.errors``).
    """
    raw = config.get("permissions.allow_buffer_size") if config is not None else None
    if raw is None:
        return
    from ..permissions.allow_buffer import get_buffer, parse_size
    try:
        size = parse_size(raw)
        rules.buffer = get_buffer(workspace, size)
    except (ValueError, OSError) as exc:
        rules.errors.append(f"allow buffer off: {exc}")
        print(f"ai-rex: allow buffer off: {exc}", file=sys.stderr)
        return
    for warning in rules.buffer.warnings:  # a corrupt file: said once, on stderr (never on a protocol stdout)
        print(f"ai-rex: {warning}", file=sys.stderr)
    rules.buffer.warnings.clear()


class PermissionGate:
    """Rules + command hooks + asker, evaluated before every tool call."""

    def __init__(
        self,
        rules: PermissionRules,
        hooks: Optional[CommandHookRunner] = None,
        asker: Optional[Asker] = None,
        mode: str = MODE_GUARD,
        config: Any = None,
        id_prefix: str = "",
    ) -> None:
        self.rules = rules
        self.hooks = hooks
        self.asker: Asker = asker or DenyAsker()
        self._mode = mode
        # With a config the mode is live: ``/local on`` in the REPL flips
        # ``cli.unsafe_local_tools`` and the very next check sees local mode.
        self._config = config
        self._counter = 0
        # Request ids are "<prefix>:perm-<n>" when a prefix is set (one gate per agent,
        # e.g. "t-3"), so questions from several agents never share an id.
        self.id_prefix = id_prefix

    @property
    def mode(self) -> str:
        """Current mode; resolved from the config on every read when one is attached."""
        if self._config is not None:
            return resolve_mode(self._config)
        return self._mode

    @mode.setter
    def mode(self, value: str) -> None:
        """Pin the mode; detaches the config."""
        self._mode = value
        self._config = None

    @classmethod
    def from_config(
        cls,
        config: Any,
        asker: Optional[Asker] = None,
        workspace: Optional[str] = None,
        session: Optional[str] = None,
        persist: bool = True,
        id_prefix: str = "",
    ) -> "PermissionGate":
        workspace = workspace or str(Path.cwd())
        mode = resolve_mode(config)
        perms = config.get("permissions", {}) if config is not None else {}
        rules = PermissionRules(perms if isinstance(perms, dict) else {}, workspace, persist=persist)
        attach_allow_buffer(rules, config, workspace)
        hooks_cfg = config.get("hooks", {}) if config is not None else {}
        hooks = CommandHookRunner.from_config(
            hooks_cfg if isinstance(hooks_cfg, dict) else {}, cwd=workspace, session=session, mode=mode
        )
        return cls(rules, hooks if hooks.has_hooks else None, asker, mode, config=config, id_prefix=id_prefix)

    def set_asker(self, asker: Optional[Asker]) -> None:
        self.asker = asker or DenyAsker()

    # -- before the call
    def check(self, name: str, args: Mapping[str, Any] | None, tool: Optional[Tool] = None) -> GateDecision:
        clean = strip_internal(args)
        mode = self.mode
        verdict = self.rules.evaluate(name, clean, mode)
        if verdict.decision == PermissionResult.DENY:
            return GateDecision(False, verdict.reason or "Permission denied", "rule")

        decision = verdict.decision
        hook_decision = None
        if self.hooks is not None:
            self.hooks.mode = mode
            pre = self.hooks.run_pre(name, clean)
            hook_decision = pre.decision
            if pre.denied:
                return GateDecision(False, f"Permission denied by hook: {pre.reason}".rstrip(": "), "hook")
            if hook_decision == "ask":
                decision = PermissionResult.ASK
            elif hook_decision == "allow" and decision == PermissionResult.ASK:
                return GateDecision(True, "", "hook")

        if decision != PermissionResult.ASK:
            return GateDecision(True, "", "rule" if verdict.source == "allow" else "default")
        return self._ask(name, clean, verdict, tool, hook_decision)

    def _ask(self, name: str, clean: dict[str, Any], verdict: Any, tool: Optional[Tool],
             hook_decision: Optional[str]) -> GateDecision:
        summary, detail = self._describe(name, clean, tool)
        remember, folder = self._buffer_offer(name, clean, verdict.kind)
        self._counter += 1
        request = PermissionRequest(
            id=f"{self.id_prefix}:perm-{self._counter}" if self.id_prefix else f"perm-{self._counter}",
            tool=name,
            kind=verdict.kind,
            summary=summary,
            detail=detail,
            args=clean,
            rule=verdict.rule,
            reason=verdict.reason if verdict.source != "default" else "",
            remember=remember,
            remember_folder=folder,
        )
        try:
            answer = self.asker.ask(request)
        except Exception as exc:  # a broken asker must never allow a call
            return GateDecision(False, f"Permission denied (asker failed: {exc})", "user")
        if answer in (ALLOW_ALWAYS, ALLOW_ALWAYS_FOLDER):
            self._remember(name, clean, verdict.kind, folder if answer == ALLOW_ALWAYS_FOLDER else "")
            return GateDecision(True, "", "user")
        if answer == ALLOW_ONCE:
            return GateDecision(True, "", "user")
        return GateDecision(False, "Permission denied by user", "user")

    def _buffer(self) -> Any:
        buf = getattr(self.rules, "buffer", None)
        return buf if buf is not None and getattr(buf, "enabled", False) else None

    def _buffer_offer(self, name: str, clean: dict[str, Any], kind: str) -> tuple[str, str]:
        """(what "always" stores in the buffer, folder offered) for the prompt; ("", "") when not buffered."""
        buf = self._buffer()
        target = buffer_target(name, clean)
        if buf is None or target is None or name == "bash" or kind not in ("read", "write"):
            return "", ""
        resolved = buf.resolve(target)
        if resolved is None:
            return "", ""
        rel = buf.relative(resolved[0])
        parent = os.path.dirname(resolved[0])
        folder = buf.relative(parent) if parent != resolved[0] and buf.inside(parent) and rel != "." else ""
        if folder and os.path.isdir(resolved[0]):
            folder = ""  # the path is a folder already: "always" covers everything below it
        return f"{kind} {rel}", folder

    def _remember(self, name: str, clean: dict[str, Any], kind: str, folder: str) -> None:
        """Store an "always" answer: in the allow buffer for paths inside the workspace, else as exact rules."""
        buf = self._buffer()
        target = buffer_target(name, clean)
        if buf is not None and target is not None and name != "bash" and kind in ("read", "write"):
            if buf.add(kind, folder or target) is not None:
                return
        self.rules.add_allow_always(name, clean)
        if buf is not None and name == "bash" and target is not None:
            buf.add("exec", target)  # record where commands were approved (never allows a command)

    @staticmethod
    def _describe(name: str, clean: dict[str, Any], tool: Optional[Tool]) -> tuple[str, str]:
        describe = getattr(tool, "describe", None)
        try:
            summary, detail = describe(dict(clean)) if callable(describe) else default_describe(name, clean)
        except Exception:
            summary, detail = default_describe(name, clean)
        detail = str(detail)
        if len(detail) > MAX_DETAIL_CHARS:
            detail = detail[:MAX_DETAIL_CHARS] + "\n... (truncated)"
        return str(summary), detail

    # -- during the call
    def check_redirect(self, from_url: str, to_url: str) -> Optional[str]:
        """webfetch redirect hook: None to follow, else the refusal reason.

        The target is always evaluated against the rules: deny refuses (also
        on the same host, e.g. a denied URL glob), allow follows, an ask from
        the mode default follows only on the same host, and an explicit ask
        rule always refuses (fetching the new URL directly asks the normal
        way). Hooks and the asker are not consulted mid-call.
        """
        new_host = _host(to_url)
        verdict = self.rules.evaluate("webfetch", {"url": to_url}, self.mode)
        if verdict.decision == PermissionResult.DENY:
            return f"redirect to {new_host or to_url} refused: {verdict.reason}"
        if verdict.decision == PermissionResult.ALLOW:
            return None
        if verdict.source == "default" and new_host and new_host == _host(from_url):
            return None  # only the mode default asks: same host, follow
        if verdict.source != "default":
            return (f"redirect to {new_host or to_url} refused: {verdict.reason}; "
                    f"fetch {to_url} directly")
        return (f"redirect to another host ({new_host or to_url}) refused: it needs its own "
                f"permission; fetch {to_url} directly")

    # -- after the call
    def post(self, name: str, args: Mapping[str, Any] | None, ok: bool,
             error: Optional[str] = None, result: Any = None) -> str:
        """Run post_tool hooks; returns the note to append to the result (may be empty)."""
        if self.hooks is None:
            return ""
        try:
            preview = result if isinstance(result, str) else json.dumps(result, default=str, ensure_ascii=False)
        except (TypeError, ValueError):
            preview = str(result)
        try:
            self.hooks.mode = self.mode
            return self.hooks.run_post(name, strip_internal(args), ok=ok, error=error,
                                       result_preview=preview[:2000])
        except Exception as exc:  # post hooks never break the call
            return f"[post hook failed: {exc}]"
