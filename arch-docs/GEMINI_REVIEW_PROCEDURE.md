# Gemini Review Procedure

## Overview
This document describes how to run an architecture review using Gemini models via the local LiteLLM proxy (port 4000).

---

## Prerequisites

### 1. LiteLLM Proxy Running on Port 4000
```bash
# Check if running
curl -s http://127.0.0.1:4000/health

# If not running, start it (from kiko installation):
cd /home/petark/kiko/kiko && python3 -m litellm --config config.yaml --port 4000
# OR whatever the actual start command is for your litellm setup
```

### 2. Models Available
```bash
# Verify models
curl -s http://127.0.0.1:4000/v1/models | jq '.data[].id'
# Expected: gemini/gemini-flash, gemini/gemini-flash-lite, nvidia/nemotron-ultra, etc.
```

### 3. API Keys Configured
Keys are in `~/.kiko/.env`:
- `GEMINI_GEMINI_FLASH_API_KEY`
- `GEMINI_GEMINI_FLASH_LITE_API_KEY`

---

## Review Script

**Location:** `/home/petark/runtime-ai/scripts/review_architecture.py`

**Usage:**
```bash
cd /home/petark/runtime-ai
.venv/bin/python scripts/review_architecture.py
```

---

## What the Script Does

### 1. Loads the Architecture Document
- Reads `/home/petark/runtime-ai/arch-docs/role-based-agent-architecture.md`

### 2. Builds Review Prompt
Structured prompt asking for:
- CRITICAL ISSUES (must fix)
- MAJOR CONCERNS (should address)
- MINOR SUGGESTIONS (nice to have)
- QUESTIONS FOR CLARIFICATION
- OVERALL VERDICT (APPROVED / NEEDS_REVISION / REJECTED)

### 3. Queries Models in Order
| Priority | Model ID | Display Name | Retries |
|----------|----------|--------------|---------|
| 1 | `gemini/gemini-flash` | Gemini 3.8 Flash | 3 |
| 2 | `gemini/gemini-flash-lite` | Gemini 3.5 Flash-Lite | 3 |

### 4. Retry Logic
- **429 (Rate Limited):** Exponential backoff (2s, 4s, 8s...)
- **Timeout/Connection Error:** Retry up to 3 times
- **400/404/500:** No retry (model/config issue)

### 5. Output
- Prints response to stdout
- Saves to `/home/petark/runtime-ai/arch-docs/gemini-review.md`
- Exit code: 0 = success, 1 = all models failed

---

## Model IDs to Use

**Use these exact IDs (from `/v1/models`):**
- `gemini/gemini-flash` — NOT `gemini-flash`
- `gemini/gemini-flash-lite` — NOT `gemini-flash-lite`

---

## Common Issues & Fixes

| Issue | Fix |
|-------|-----|
| `ConnectionRefusedError` on 4000 | Start LiteLLM proxy |
| `400: Invalid model name` | Use full ID with provider prefix (`gemini/...`) |
| `429 Too Many Requests` | Script handles with backoff; wait or check quota |
| `timed out` | Increase timeout in script (default 60s) |
| `Response object has no attribute 'text'` | Use `resp.read().decode('utf-8')` not `resp.text` |

---

## Manual Review (Alternative)

If script fails, you can manually review:

```bash
# 1. Get document
cat /home/petark/runtime-ai/arch-docs/role-based-agent-architecture.md

# 2. Copy to Gemini web UI or use curl directly:
curl -X POST http://127.0.0.1:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini/gemini-flash-lite",
    "messages": [{"role": "user", "content": "YOUR_PROMPT_HERE"}],
    "temperature": 0.1,
    "max_tokens": 4096
  }'
```

---

## Updating the Architecture Document

After review, update:
1. `/home/petark/runtime-ai/arch-docs/role-based-agent-architecture.md` — incorporate fixes
2. Increment version in document header
3. Re-run review to verify

---

## Files Involved

| File | Purpose |
|------|---------|
| `scripts/review_architecture.py` | Main review script |
| `arch-docs/role-based-agent-architecture.md` | Document to review |
| `arch-docs/gemini-review.md` | Review output (overwritten each run) |
| `arch-docs/GEMINI_REVIEW_PROCEDURE.md` | This file |

---

## Quick Reference

```bash
# One-liner to run review
cd /home/petark/runtime-ai && .venv/bin/python scripts/review_architecture.py

# Check last review
cat /home/petark/runtime-ai/arch-docs/gemini-review.md

# Verify proxy health
curl -s http://127.0.0.1:4000/health | jq .
```