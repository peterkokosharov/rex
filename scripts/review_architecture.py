#!/usr/bin/env python3
"""Review architecture document using kiko-lite proxy (Gemini Flash / Flash-Lite)."""

import json
import os
import sys
import time
from pathlib import Path

# Add runtimelib to path
sys.path.insert(0, "/home/petark/runtime-ai")

from runtimelib.llm import LiteLLMClient
from runtimelib.config import Config


def load_doc(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def build_review_prompt(doc: str) -> str:
    return f"""You are a senior software architect reviewing a design document for a role-based agent orchestration system.

Review the following design document and provide structured feedback in this exact format:

## CRITICAL ISSUES (must fix before implementation)
- [ ] Issue 1
- [ ] Issue 2

## MAJOR CONCERNS (should address)
- [ ] Concern 1
- [ ] Concern 2

## MINOR SUGGESTIONS (nice to have)
- [ ] Suggestion 1
- [ ] Suggestion 2

## QUESTIONS FOR CLARIFICATION
- Question 1?
- Question 2?

## OVERALL VERDICT
[APPROVED / NEEDS_REVISION / REJECTED] - One sentence summary

---

DESIGN DOCUMENT:
{doc}
"""


def query_model(client: LiteLLMClient, model: str, prompt: str, max_retries: int = 3) -> str | None:
    """Query model with retries."""
    from runtimelib.llm import ChatMessage

    for attempt in range(max_retries):
        try:
            resp = client.chat_completion(
                messages=[ChatMessage.user(prompt)],
                model=model,
                temperature=0.1,
                max_tokens=4096,
                stream=False,
            )
            if resp.status == 200:
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            elif resp.status == 429:
                wait = 2 ** attempt
                print(f"  Rate limited (429), waiting {wait}s...")
                time.sleep(wait)
                continue
            else:
                body = resp.read().decode("utf-8") if hasattr(resp, 'read') else str(resp)
                print(f"  HTTP {resp.status}: {body[:200]}")
                return None
        except Exception as e:
            print(f"  Error (attempt {attempt + 1}): {e}")
            if attempt < max_retries - 1:
                time.sleep(1)
    return None


def main():
    doc_path = Path("/home/petark/runtime-ai/arch-docs/role-based-agent-architecture.md")
    if not doc_path.exists():
        print(f"Document not found: {doc_path}")
        sys.exit(1)

    doc = load_doc(doc_path)
    print(f"Loaded document: {len(doc)} chars")

    # Load config from kiko-lite proxy
    config = Config.load()
    # Override with kiko-lite settings
    config._data["liteLLM"] = {
        "base_url": "http://127.0.0.1:4000",
        "api_key": "dummy",  # litellm handles auth
        "model": "gemini-flash",
        "timeout": 60,
    }

    client = LiteLLMClient.from_config(config)

    # Try models in order
    models = [
        ("gemini/gemini-flash", "Gemini 3.8 Flash"),
        ("gemini/gemini-flash-lite", "Gemini 3.5 Flash-Lite"),
    ]

    prompt = build_review_prompt(doc)
    print(f"Prompt: {len(prompt)} chars")

    for model_id, model_name in models:
        print(f"\n🔄 Trying {model_name} ({model_id})...")
        result = query_model(client, model_id, prompt)
        if result:
            print(f"✅ {model_name} responded ({len(result)} chars)")
            output_path = Path("/home/petark/runtime-ai/arch-docs/gemini-review.md")
            output_path.write_text(result, encoding="utf-8")
            print(f"📝 Review saved to: {output_path}")
            print("\n" + "=" * 60)
            print(result)
            return 0
        else:
            print(f"❌ {model_name} failed")

    print("\n❌ All models failed (3 attempts each)")
    return 1


if __name__ == "__main__":
    sys.exit(main())