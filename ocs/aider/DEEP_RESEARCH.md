# Aider Deep Research Analysis

**Дата:** 2024-11-01  
**Версия анализирана:** master (v0.86.3.dev)  
**Методология:** Статичен анализ на кодовата база, git history, архитектурни патърни

---

## 1. Архитектура - Общ преглед

```
aider/
├── __main__.py          # Entry point (62 байта - делегира на main.py)
├── main.py              # Main orchestration (46KB)
├── args.py              # Argument parsing (30KB)
├── commands.py          # Slash commands (62KB)
├── io.py                # Input/Output handling (43KB)
├── llm.py               # LLM abstraction (1KB - ТЪЙНО минимално!)
├── models.py            # Model configurations (46KB)
├── repo.py              # Git repo operations (27KB)
├── repomap.py           # Repository mapping (27KB)
├── coders/              # 20+ coder implementations
├── queries/             # Tree-sitter queries
├── resources/           # Prompts, templates
└── tests/               # Test suite
```

### Ключови компоненти

| Файл | Размер | Отговорност |
|------|--------|-------------|
| `main.py` | 46KB | Главен оркестратор, управление на сесия, контекст |
| `commands.py` | 62KB | Всички `/` команди, REPL логика |
| `io.py` | 43KB | Потребителски интерфейс, цвят, форматиране |
| `repo.py` | 27KB | Git операции, файлово дърво |
| `repomap.py` | 27KB | Семантично индексиране (ctags + tree-sitter) |
| `coders/` | 20+ файлове | Различни стратегии за код генерация |

---

## 2. Силони страни

### 2.1 Multi-Model Support (Отличен)
- **models.py** - 46KB конфигурация за 100+ модели
- Поддържа: OpenAI, Anthropic, Gemini, DeepSeek, Ollama, OpenRouter, Vertex, Bedrock, Azure, Groq, Together, Fireworks, Perplexity, Mistral, Cohere
- Автоматично mapping на aliases (`sonnet` → `claude-3-5-sonnet-20241022`)
- Per-model settings: temperature, thinking tokens, context window, pricing

### 2.2 Repository Intelligence (Repomap)
- **repomap.py** - 27KB, най-силен feature
- Използва `ctags` + `tree-sitter` за семантично индексиране
- Извлича: класове, функции, импорти, дефиниции
- Rank-ва по релевантност за задачата
- Кeshира резултати (`.aider.caches/repomap/`)

### 2.3 Git Integration (Пълна)
- **repo.py** - Git wrapper с porcelain команди
- Автоматични commit-и след всяка промяна
- Diff preview преди прилагане
- Undo/redo чрез git history
- Поддържа submodules, worktrees

### 2.4 Coder Abstraction (Флексибилен)
- **coders/base_coder.py** - Базов клас с общ протокол
- 20+ специализирани кодъра:
  - `ArchitectCoder` - дизайн/планиране
  - `EditBlockCoder` - surgical edits
  - `UDiffCoder` - unified diff
  - `WholeFileCoder` - пълна замяна
  - `PatchCoder` - patch формат
  - `SearchReplaceCoder` - search/replace блокове

### 2.5 Voice Integration
- **voice.py** - Whisper интеграция за voice-to-code
- **watch.py** - File watching за auto-reload

### 2.6 Benchmarking Infrastructure
- **benchmark/** - SWE-bench, HumanEval, custom tasks
- Автоматизирани тестове за качество на код генерация

---

## 3. Слаби места / Проблеми

### 3.1 LLM Abstraction - Почти липсва
```python
# llm.py - ЦЯЛОСТЕН ФАЙЛ (1103 байта)
from . import models
from .sendchat import send_chat_completion

__all__ = ["send_chat_completion", "models"]
```
**Проблем:** Няма retry logic, streaming handling, token counting, error classification. Всичко е в `sendchat.py` (1.8KB) което е monolithic.

### 3.2 Monolithic Commands & Main
- **commands.py** - 62KB, 1500+ реда, gigantesque `switch` statement
- **main.py** - 46KB, смесял оркестрация, UI, конфигурация
- Трудно за тестване, трудно за разширение

### 3.3 Няма Hooks System
- Няма pre/post tool execution hooks
- Няма event system за plugins
- Skills/Plugins = НЕСЪЩЕСТВУВА

### 3.4 Няма Permission System
- Всички tools се изпълняват директно
- Няма granular allow/deny
- Няма sandboxing (osвен docker опция)

### 3.5 Няма Subagents / Orchestration
- Единствен агент, никаква иерархия
- Няма task delegation
- Няма background tasks

### 3.6 Context Management - Базичен
- Sliding window само (последни N съобщения)
- Няма summarization
- Няма priority-based retention
- Няма cross-session memory

### 3.7 Testing - Integration only
- Тестовете са integration (пълна сесия)
- Няма unit тестове за отделни компоненти
- Трудно за TDD

### 3.8 Error Handling - Inconsistent
- Различни exception типове навсякъде
- Няма централизирана грешка обработка
- Няма structured error responses за LLM

---

## 4. Потенциални Bugs / Рискове

### 4.1 Repomap Race Conditions
```python
# repomap.py - кеш файлове без locking
cache_path = Path(".aider.caches/repomap/")
cache_path.write_bytes(pickle.dumps(data))  # Не е atomic
```

### 4.2 Git Operations - No Locking
```python
# repo.py - git команди без защита от конкурентност
subprocess.run(["git", "commit", ...])  # Може да се счупи при паралелни сесии
```

### 4.3 SendChat - Monolithic Error Handling
```python
# sendchat.py - един голям try/except
try:
    response = client.chat.completions.create(...)
except Exception as e:
    # Всички грешки са едно и също
```

### 4.4 Token Counting - Approximate
- Използва `tiktoken` за OpenAI модели
- За Anthropic/Google - приблизителни формули
- Няма точно counting за multi-modal

### 4.5 Config - No Validation
```python
# args.py - argparse без schema validation
# env vars се смесват с CLI args непредвидимо
```

---

## 5. Пропуски (What's Missing)

### 5.1 Enterprise Features
| Feature | Status |
|---------|--------|
| SSO/SAML | ❌ |
| Audit logging | ❌ |
| Policy enforcement | ❌ |
| Data residency | ❌ |
| Admin console | ❌ |

### 5.2 Developer Experience
| Feature | Status |
|---------|--------|
| IDE Integration (VS Code, JetBrains) | ❌ (само community plugins) |
| LSP Server | ❌ |
| Debug adapter | ❌ |
| Rich terminal UI (syntax highlight, diffs) | ❌ (само basic ANSI) |

### 5.3 Platform
| Feature | Status |
|---------|--------|
| Web UI | ❌ |
| Multi-user server | ❌ |
| Shared sessions | ❌ |
| API для интеграция | ❌ |

### 5.4 Advanced AI
| Feature | Status |
|---------|--------|
| Fine-tuning pipeline | ❌ |
| Custom model hosting | ❌ |
| RAG / Vector search | ❌ (само repomap) |
| Multi-modal (images, diagrams) | ❌ |
| Autonomous workflows | ❌ |

### 5.5 Extensibility
| Feature | Status |
|---------|--------|
| Plugin system | ❌ |
| Custom tools | ❌ |
| Custom slash commands | ❌ (трябва да се модифицира commands.py) |
| Hooks | ❌ |
| Skills marketplace | ❌ |

---

## 6. Модулна структура и връзки

```
┌─────────────┐
│  main.py    │ ← Entry point, lifecycle management
└──────┬──────┘
       │
       ▼
┌─────────────┐     ┌─────────────┐
│  args.py    │────▶│  config     │ (implicit, global state)
└─────────────┘     └─────────────┘
       │
       ▼
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  io.py      │◀───▶│  commands.py│◀───▶│  coders/    │
│  (UI/UX)    │     │  (REPL)     │     │  (LLM ops)  │
└─────────────┘     └──────┬──────┘     └──────┬──────┘
                           │                   │
                           ▼                   ▼
                    ┌─────────────┐     ┌─────────────┐
                    │  repo.py    │     │  sendchat.py│
                    │  (Git)      │     │  (LLM API)  │
                    └──────┬──────┘     └─────────────┘
                           │
                           ▼
                    ┌─────────────┐
                    │ repomap.py  │
                    │ (Code Intel)│
                    └─────────────┘
```

### Ключови зависимости (твърди)
- `main.py` → `args.py`, `io.py`, `commands.py`, `repo.py`, `repomap.py`
- `commands.py` → `io.py`, `repo.py`, `coders/`, `sendchat.py`
- `coders/*` → `sendchat.py`, `prompts.py`, `models.py`
- `sendchat.py` → `models.py`, `llm.py` (minimal)

### Проблем: Global State
- Много модули четат `os.environ` директно
- Конфигурацията е разпръсната
- Няма dependency injection

---

## 7. Защо са пропуснали (Root Cause Analysis)

### 7.1 Philosophy: "Single Developer Tool"
Aider е изграден за **един разработчик** в терминал. Не е платформа.

### 7.2 Organic Growth
- започнал като прост скрипт → нараснал без рефакторинг
- Фокус върху *features* не върху *architecture*

### 7.3 Resource Constraints
- Малък екип (1-2 core maintainers)
- Приоритет: нови модели, нови features
- Техническият дълг се нараства

### 7.4 Target Audience
- Индивидуални разработчици, не екипи/enterprise
- "Good enough" за лична употреба

---

## 8. Сравнение с нашия Runtime Plan

| Aspect | Aider | Our Runtime |
|--------|-------|-------------|
| Architecture | Monolithic | Modular (kikolib-based) |
| LLM Abstraction | Minimal | Full (streaming, retry, tools) |
| Tools | Hardcoded | Pluggable (guard sandbox) |
| Permissions | None | Granular |
| Hooks | None | Full system |
| Skills | None | Standard format + registry |
| Subagents | None | Hierarchical |
| Context Mgmt | Sliding window | Priority + summarization + embeddings |
| Git | Direct | Via guard (auditable) |
| Sandbox | Optional Docker | Mandatory guard container |
| Multi-user | No | Single-user but API-ready |
| Extensibility | Code modification | Plugins + skills + hooks |
| IDE Integration | Community only | LSP bridge planned |
| Enterprise | No | Policy engine + audit log |

---

## 9. Уроци за нашия Runtime

### Вземаме:
1. **Repomap идея** → но с AST (stdlib) вместо ctags
2. **Multi-model config** → но като YAML schema в kikolib
3. **Coder abstraction** → но като pluggable tools
4. **Git integration** → но през guard (audit trail)
5. **Benchmark suite** → за regression testing

### Не правим:
1. **Monolithic commands/main** → модулен CLI + command registry
2. **Global state** → dependency injection + config object
3. **No hooks/permissions** → first-class citizens
4. **Single agent** → hierarchical orchestration
5. **No sandbox** → mandatory guard for ALL tools

---

## 10. Конкретни файлове за референция

### За изучаване (Good patterns):
- `repomap.py` - code intelligence
- `models.py` - model configuration
- `coders/base_coder.py` - abstraction pattern
- `repo.py` - git wrapper

### За избягване (Anti-patterns):
- `commands.py` - god object
- `main.py` - mixed concerns
- `sendchat.py` - monolithic LLM handling
- `llm.py` - empty abstraction
- `args.py` - config без validation

---

## 11. Conclusions

**Aider = Excellent single-developer coding assistant**  
**Not a platform, not extensible, not enterprise-ready**

Our Runtime трябва да бъде **платформа** с:
- Clean architecture (kikolib foundation)
- Sandbox по подразбиране (guard)
- Extensibility (skills, hooks, tools)
- Orchestration (subagents, workflows)
- Bulgarian-first UI (уникално)

---

*Генериран за внутренска употреба. Не за публично разпространение.*