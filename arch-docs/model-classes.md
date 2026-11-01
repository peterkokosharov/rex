# Model Classes — Comprehensive Classification (30 Classes)

## Design Principles

1. **Orthogonal dimensions**: Class = (Capability Tier × Specialization × Modality)
2. **Keyword-based detection**: First substring match on model name (like kiko)
3. **Role-driven**: Each class has a clear role in the agent orchestration
4. **Extensible**: New classes added via DB, no code changes

---

## 30 Model Classes

| # | Class Name | Keyword | Role | Sort | Modality | Specialization |
|---|------------|---------|------|------|----------|----------------|
| 1 | **OPUS** | opus | Manager & evaluator (architect, reviewer) | 10 | Text | General reasoning |
| 2 | **SONNET** | sonnet | Craftsman (main implementation) | 20 | Text | General coding |
| 3 | **HAIKU** | haiku | Laborer (bulk simple work) | 30 | Text | Fast/cheap |
| 4 | **FABLE** | fable | External auditor (security, correctness) | 40 | Text | Adversarial review |
| 5 | **OPUS_CODE** | opus.code | Senior architect (complex design, refactor) | 11 | Text | Code reasoning |
| 6 | **SONNET_CODE** | sonnet.code | Staff engineer (production code) | 21 | Text | Code quality |
| 7 | **HAIKU_CODE** | haiku.code | Junior dev (boilerplate, tests, docs) | 31 | Text | Code speed |
| 8 | **OPUS_REASON** | opus.reason | Deep reasoning (planning, debugging) | 12 | Text | Chain-of-thought |
| 9 | **SONNET_REASON** | sonnet.reason | Structured reasoning (step-by-step) | 22 | Text | Logic |
| 10 | **HAIKU_REASON** | haiku.reason | Quick reasoning (simple logic) | 32 | Text | Fast logic |
| 11 | **VISION_OPUS** | vision.opus | Visual architect (UI, diagrams, screenshots) | 13 | Vision | Multimodal reasoning |
| 12 | **VISION_SONNET** | vision.sonnet | Visual craftsman (code from design, OCR) | 23 | Vision | Code from image |
| 13 | **VISION_HAIKU** | vision.haiku | Visual laborer (caption, classify) | 33 | Vision | Fast vision |
| 14 | **VOICE_TTS** | voice.tts | Speech synthesis (read code, alerts) | 50 | Audio | TTS |
| 15 | **VOICE_STT** | voice.stt | Speech recognition (voice commands) | 51 | Audio | STT |
| 16 | **VOICE_CONV** | voice.conv | Conversational voice (pair programming) | 52 | Audio | Voice chat |
| 17 | **EMBED_SMALL** | embed.small | Fast embeddings (search, clustering) | 60 | Embedding | Speed |
| 18 | **EMBED_LARGE** | embed.large | Quality embeddings (RAG, semantic search) | 61 | Embedding | Quality |
| 19 | **EMBED_CODE** | embed.code | Code embeddings (semantic code search) | 62 | Embedding | Code-specific |
| 20 | **RERANK** | rerank | Reranker (search result refinement) | 70 | Rerank | Precision |
| 21 | **TOOL_CALLER** | tool.caller | Function calling specialist (high reliability) | 80 | Text | Tool use |
| 22 | **LONG_CTX** | long.ctx | Large context (100K+ tokens, whole repo) | 90 | Text | Context window |
| 23 | **LOCAL_OPUS** | local.opus | Local reasoning (privacy, offline) | 14 | Text | Local |
| 24 | **LOCAL_SONNET** | local.sonnet | Local coding (privacy, offline) | 24 | Text | Local code |
| 25 | **LOCAL_HAIKU** | local.haiku | Local fast (edge, embedded) | 34 | Text | Local fast |
| 26 | **SPECULATIVE** | speculative | Speculative decoding (draft model) | 95 | Text | Speedup |
| 27 | **CLASSIFIER** | classifier | Intent classification, routing | 100 | Text | Routing |
| 28 | **GUARDRAIL** | guardrail | Safety, PII, prompt injection detection | 105 | Text | Safety |
| 29 | **TRANSLATOR** | translator | Code translation (lang↔lang, legacy→modern) | 110 | Text | Translation |
| 30 | **SYNTHESIZER** | synthesizer | Doc generation, spec→code, test generation | 115 | Text | Generation |

---

## Detection Logic (Keyword Priority)

```
# Detection order = sort ascending
# First keyword match wins (substring, case-insensitive)

opus.code      → OPUS_CODE      (before opus)
sonnet.code    → SONNET_CODE    (before sonnet)
haiku.code     → HAIKU_CODE     (before haiku)
opus.reason    → OPUS_REASON    (before opus)
vision.opus    → VISION_OPUS    (before opus)
voice.tts      → VOICE_TTS      (before voice)
embed.code     → EMBED_CODE     (before embed)
local.opus     → LOCAL_OPUS     (before opus)
```

---

## Agent Orchestration Mapping

| Task Type | Primary Class | Fallback Classes |
|-----------|--------------|------------------|
| Architecture design | OPUS_CODE | OPUS_REASON, OPUS |
| Code implementation | SONNET_CODE | SONNET, SONNET_REASON |
| Boilerplate/tests/docs | HAIKU_CODE | HAIKU |
| Security audit | FABLE | OPUS_REASON |
| Code review | FABLE | OPUS_CODE, SONNET_CODE |
| Debugging | OPUS_REASON | SONNET_REASON |
| Planning | OPUS_REASON | OPUS |
| UI from screenshot | VISION_OPUS | VISION_SONNET |
| OCR code from image | VISION_SONNET | VISION_OPUS |
| Voice commands | VOICE_STT | VOICE_CONV |
| Read aloud | VOICE_TTS | VOICE_CONV |
| Semantic search | EMBED_LARGE | EMBED_CODE |
| Code search | EMBED_CODE | EMBED_LARGE |
| RAG retrieval | RERANK + EMBED_LARGE | |
| Tool calling | TOOL_CALLER | SONNET_CODE |
| Whole repo context | LONG_CTX | OPUS_CODE |
| Offline/private | LOCAL_OPUS | LOCAL_SONNET |
| Edge/embedded | LOCAL_HAIKU | HAIKU |
| Intent routing | CLASSIFIER | |
| Safety check | GUARDRAIL | |
| Code translation | TRANSLATOR | OPUS_CODE |
| Test/spec generation | SYNTHESIZER | HAIKU_CODE |

---

## Pricing Tiers (for cost-aware routing)

| Tier | Classes | Est. Cost/1M tokens |
|------|---------|---------------------|
| **Premium** | OPUS, OPUS_CODE, OPUS_REASON, VISION_OPUS, LONG_CTX | $15-75 |
| **Standard** | SONNET, SONNET_CODE, SONNET_REASON, VISION_SONNET, TRANSLATOR | $3-15 |
| **Economy** | HAIKU, HAIKU_CODE, HAIKU_REASON, VISION_HAIKU, SYNTHESIZER | $0.25-3 |
| **Specialized** | FABLE, TOOL_CALLER, CLASSIFIER, GUARDRAIL, RERANK | $1-10 |
| **Embeddings** | EMBED_LARGE, EMBED_CODE, EMBED_SMALL | $0.02-0.13 |
| **Audio** | VOICE_TTS, VOICE_STT, VOICE_CONV | $0.5-15 |
| **Local/Free** | LOCAL_OPUS, LOCAL_SONNET, LOCAL_HAIKU, SPECULATIVE | $0 (hardware) |

---

## Database Schema (model_classes table)

```sql
-- Already exists in kiko.db v2
CREATE TABLE model_classes (
    name    TEXT PRIMARY KEY,           -- e.g. 'OPUS_CODE'
    keyword TEXT NOT NULL UNIQUE,       -- e.g. 'opus.code'
    role    TEXT NOT NULL DEFAULT '',   -- human-readable role
    sort    INTEGER NOT NULL DEFAULT 100 -- detection priority
);

-- Seed with 30 classes (INSERT OR IGNORE)
```

---

## Usage in Runtime

```python
# runtime/llm/model_registry.py
class ModelRegistry:
    def __init__(self, db_path: Path, litellm_url: str):
        self.db = Database(db_path)
        self.litellm = LiteLLMClient(litellm_url)
    
    def get_by_class(self, class_name: str) -> list[Model]:
        """All enabled models of a class, merged with live proxy info."""
    
    def resolve(self, task: TaskType, constraints: Constraints = None) -> Model:
        """Pick best model for task considering: class, cost, latency, availability."""
    
    def live_info(self) -> dict:
        """Merge DB classes + proxy /v1/models + health status."""
```

---

## Migration from Current (5 classes → 30)

```sql
-- Run once to add missing classes
INSERT OR IGNORE INTO model_classes(name, keyword, role, sort) VALUES
('OPUS_CODE', 'opus.code', 'Senior architect (complex design, refactor)', 11),
('SONNET_CODE', 'sonnet.code', 'Staff engineer (production code)', 21),
('HAIKU_CODE', 'haiku.code', 'Junior dev (boilerplate, tests, docs)', 31),
('OPUS_REASON', 'opus.reason', 'Deep reasoning (planning, debugging)', 12),
('SONNET_REASON', 'sonnet.reason', 'Structured reasoning (step-by-step)', 22),
('HAIKU_REASON', 'haiku.reason', 'Quick reasoning (simple logic)', 32),
('VISION_OPUS', 'vision.opus', 'Visual architect (UI, diagrams, screenshots)', 13),
('VISION_SONNET', 'vision.sonnet', 'Visual craftsman (code from design, OCR)', 23),
('VISION_HAIKU', 'vision.haiku', 'Visual laborer (caption, classify)', 33),
('VOICE_TTS', 'voice.tts', 'Speech synthesis (read code, alerts)', 50),
('VOICE_STT', 'voice.stt', 'Speech recognition (voice commands)', 51),
('VOICE_CONV', 'voice.conv', 'Conversational voice (pair programming)', 52),
('EMBED_SMALL', 'embed.small', 'Fast embeddings (search, clustering)', 60),
('EMBED_LARGE', 'embed.large', 'Quality embeddings (RAG, semantic search)', 61),
('EMBED_CODE', 'embed.code', 'Code embeddings (semantic code search)', 62),
('RERANK', 'rerank', 'Reranker (search result refinement)', 70),
('TOOL_CALLER', 'tool.caller', 'Function calling specialist (high reliability)', 80),
('LONG_CTX', 'long.ctx', 'Large context (100K+ tokens, whole repo)', 90),
('LOCAL_OPUS', 'local.opus', 'Local reasoning (privacy, offline)', 14),
('LOCAL_SONNET', 'local.sonnet', 'Local coding (privacy, offline)', 24),
('LOCAL_HAIKU', 'local.haiku', 'Local fast (edge, embedded)', 34),
('SPECULATIVE', 'speculative', 'Speculative decoding (draft model)', 95),
('CLASSIFIER', 'classifier', 'Intent classification, routing', 100),
('GUARDRAIL', 'guardrail', 'Safety, PII, prompt injection detection', 105),
('TRANSLATOR', 'translator', 'Code translation (lang↔lang, legacy→modern)', 110),
('SYNTHESIZER', 'synthesizer', 'Doc generation, spec→code, test generation', 115);
```