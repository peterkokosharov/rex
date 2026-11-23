<!--
Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
LLM: nvidia nemotron ultra
Licensed under MIT License
-->

## CRITICAL ISSUES (must fix before implementation)
- [ ] Section 5.4 specifies mounting `.worktrees/{agent_id}` as a writeable path, but Docker bind mounts require the host path to exist *before* container startup. The runtime must explicitly ensure `Path(source).mkdir(parents=True, exist_ok=True)` in Python *prior* to invoking the Docker API, or the container will fail to start with a generic filesystem error.
- [ ] Section 9.1's `StructuredPromptBuilder` includes a `TOOL_OUTPUT` zone that formats tool results into `tool` role messages, but it completely omits handling for OpenAI/LiteLLM `tool_call_id` correlation or assistant `tool_calls` history arrays. Without matching assistant tool call IDs to tool response messages, API calls to LiteLLM will throw 400 Bad Request errors.
- [ ] Section 10.2's `RedisBackend` Lua script stores state using `HSET` and sets an expiration, but the expiration is calculated as `math.ceil(capacity / refill_rate) + 10`. For large buckets (e.g., `tokens_per_minute = 100,000`), this expiration can exceed 1,000 seconds, causing stale token bucket states to accumulate indefinitely in Redis if the agent session crashes or exits unexpectedly.

## MAJOR CONCERNS (should address)
- [ ] Section 5.2's `FileMergeLock` and `RedisMergeLock` manage git worktree merging, but they do not handle stale locks left behind by abruptly terminated agent processes (e.g., OOM kills or SIGKILL), which will permanently block the orchestrator from merging code.
- [ ] Section 8.2 outlines a parallel sync mechanism where the `TESTER` reads directly from the `CODER`'s sibling worktree using a read-only mount, but Git worktrees share a single `.git` repository object database with isolated index files. Reading uncommitted changes across worktrees via filesystem bind mounts can result in partial writes or synchronization bugs unless explicit git commits or staging boundaries are enforced.
- [ ] Section 10.3 uses model identifiers like `gemini/gemini-flash` and `claude-opus-4-1` inside configuration files, but model naming conventions and provider prefixes differ significantly across LiteLLM providers, risking configuration breakage when provider strings change.

## MINOR SUGGESTIONS (nice to have)
- [ ] Add explicit metrics/telemetry logging for token bucket consumption and rate-limit wait times to help debug performance bottlenecks during high parallel agent execution.
- [ ] Provide an option in the `SandboxManager` to automatically compress and archive `.runtime/sessions/{session_id}` logs upon session completion to prevent inode exhaustion on long-running deployments.
- [ ] Document the expected behavior when an agent reaches its `max_steps` threshold (e.g., graceful shutdown vs. forced summary prompt generation).

## QUESTIONS FOR CLARIFICATION
- How are environment variables and LiteLLM API credentials securely passed to the isolated Docker guard containers without leaking via `docker inspect` or persistent shell history?
- What is the intended recovery path if the host machine running the Orchestrator crashes mid-pipeline after child worktrees have been created but before changes are merged to `main`?
- Are subagents expected to inherit the parent agent's active session context by default, or do they spin up with a blank context window restricted strictly to their specific task description?

## OVERALL VERDICT
[NEEDS_REVISION] - The design document is exceptionally thorough and well-structured, but requires concrete fixes for Docker pre-creation requirements, LLM tool-call message formatting, and stale distributed lock handling before implementation can begin.