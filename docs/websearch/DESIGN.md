# WebSearch Module - Design Document

**Project:** rex  
**Module:** `runtimelib/tools/websearch.py`  
**Status:** Design phase  
**Target:** Embedded web search tool (stdlib + kikolib only, no Docker, no pip at runtime)

---

## 1. Requirements

| Requirement | Detail |
|-------------|--------|
| **Runtime** | Python 3.11+ stdlib + `kikolib` (vendored in `vendor/kikolib/`) |
| **Platform** | Linux, Windows (no Docker, no WSL required) |
| **Dependencies** | Zero pip packages at runtime |
| **Integration** | `runtimelib.tools.WebSearchTool` → `Tool` interface |
| **Protocol** | JSON-RPC over stdio (via ai-rex) or direct Python import |
| **Concurrency** | Async (kikolib.http is async-capable) |

---

## 2. Options Analysis

### Option A: SearXNG (self-hosted metasearch)

| Aspect | Detail |
|--------|--------|
| **Language** | Python (Flask/Quart + aiohttp + lxml + redis) |
| **Dependencies** | ~35 pip packages (Flask, Quart, aiohttp, lxml, redis, pyyaml, etc.) |
| **Runtime** | Requires Python 3.10+, Redis (optional) |
| **Deployment** | Docker (recommended), or venv + pip install |
| **API** | REST: `GET /search?q=...&format=json` |
| **Engines** | 70+ (Google, Bing, DDG, Brave, GitHub, Reddit, etc.) |
| **Maintenance** | Active (weekly commits) |

**Verdict for Windows/no-Docker:** ❌ **Not suitable** — requires pip install (35+ deps), Redis, background process management. Can't embed in stdlib-only codebase.

---

### Option B: Brave Search API

| Aspect | Detail |
|--------|--------|
| **Type** | Commercial API (free tier: 2000/day → 20/day after month 1) |
| **Protocol** | REST JSON: `GET https://api.search.brave.com/res/v1/web/search` |
| **Auth** | Bearer token (API key) |
| **Rate limits** | 2000/day free → 20/day after 1 month |
| **Dependencies** | None (pure HTTPS + JSON) |
| **Reliability** | Commercial SLA |

**Verdict:** ⚠️ **Fallback only** — rate limits too low for production, dependency on 3rd party.

---

### Option C: Custom Search Engine (stdlib + kikolib) — **CHOSEN**

| Engine | Protocol | Selector | Reliability |
|--------|----------|----------|-------------|
| **Brave Search (HTML)** | HTTPS + HTML scraping | CSS selectors / data-testid | Medium (202 redirects) |
| **DuckDuckGo HTML** | HTTPS + HTML scraping | data-testid / regex | Medium (202/302 redirects) |
| **Bing HTML** | HTTPS + HTML scraping | CSS selectors | Good |
| **Startpage HTML** | HTTPS + HTML scraping | CSS selectors | Good |
| **Google Custom Search API** | REST JSON | Native | Requires API key |

**Chosen approach:** Multi-engine HTML scraping with fallback chain, using `kikolib.http` (stdlib-compatible async HTTP client).

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    WebSearchTool (Tool)                     │
├─────────────────────────────────────────────────────────────┤
│  execute(query, max_results=10, engines=["brave","ddg"])   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    SearchEngine (abstract)                  │
│  search(query, max_results) -> list[SearchResult]          │
└─────────────────────────────────────────────────────────────┘
         │                    │                    │
         ▼                    ▼                    ▼
┌───────────────┐  ┌─────────────────┐  ┌─────────────────┐
│ BraveEngine   │  │ DuckDuckGoEngine│  │ BingEngine      │
│ (HTML scrape) │  │ (HTML scrape)   │  │ (HTML scrape)   │
└───────────────┘  └─────────────────┘  └─────────────────┘
         │                    │                    │
         └────────────────────┼────────────────────┘
                              ▼
                    ┌─────────────────────┐
                    │   ResultMerger      │
                    │  dedup, rank, trim  │
                    └─────────────────────┘
                              │
                              ▼
                    ┌─────────────────────┐
                    │   SearchResult[]    │
                    └─────────────────────┘
```

---

## 4. Dependencies Analysis

### Current Stack (Zero Pip)

| Package | Source | Purpose |
|---------|--------|---------|
| `kikolib.http` | `vendor/kikolib/` | Async HTTP client (stdlib + ctypes) |
| `kikolib.json` | `vendor/kikolib/` | JSON parsing |
| `urllib.parse` | stdlib | URL encoding |
| `html` | stdlib | HTML entity decoding |
| `re` | stdlib | Regex parsing |
| `html.parser` | stdlib | HTML parsing (fallback) |
| `html.entities` | stdlib | Entity decoding |
| `asyncio` | stdlib | Async runtime |
| `dataclasses` | stdlib | Data structures |
| `typing` | stdlib | Type hints |

**Total pip packages at runtime: 0**

---

## 4.1 SearXNG Dependencies (for reference — NOT used)

| Package | Version | Purpose |
|---------|---------|---------|
| Flask | 2.3+ | Web framework |
| Quart | 0.18+ | Async Flask alternative |
| aiohttp | 3.8+ | Async HTTP client |
| lxml | 4.9+ | HTML/XML parsing |
| cssselect | 1.2+ | CSS selectors |
| redis | 4.5+ | Rate limiting cache |
| pyyaml | 6.0+ | Config parsing |
| requests | 2.31+ | Sync HTTP (fallback) |
| playwright | 1.40+ | JS rendering (optional) |
| redis | 4.5+ | Rate limiting |
| flask-cors | 3.0+ | CORS |
| flask-limiter | 3.5+ | Rate limiting |
| **Total** | **~35 packages** | |

**Verdict:** Cannot embed in zero-pip codebase.

---

## 5. Implementation Plan

### 5.1 Module Structure

```
runtimelib/tools/
├── websearch.py          # Main WebSearchTool (Tool interface)
├── engines/
│   ├── __init__.py       # Engine registry
│   ├── base.py           # SearchEngine abstract base
│   ├── brave.py          # Brave HTML scraper
│   ├── duckduckgo.py     # DuckDuckGo scraper
│   ├── bing.py           # Bing scraper
│   └── base_scraper.py   # BaseScraper with common logic
└── websearch.py          # Main Tool entry point
```

### 5.2 Data Structures

```python
@dataclass
class SearchResult:
    url: str
    title: str
    snippet: str
    engine: str
    score: float = 0.0
    rank: int = 0

@dataclass  
class SearchResponse:
    query: str
    results: list[SearchResult]
    engine: str
    total_results: int
    elapsed_ms: int
```

### 5.3 Engine Interface

```python
class SearchEngine(ABC):
    name: str
    base_url: str
    
    @abstractmethod
    async def search(self, query: str, max_results: int = 10) -> list[SearchResult]:
        pass
    
    async def fetch_html(self, url: str, params: dict) -> str:
        # Uses kikolib.http.client()
        pass
    
    def parse_results(self, html: str, max_results: int) -> list[SearchResult]:
        # Engine-specific parsing
        pass
```

---

## 6. Engine Implementations

### 6.1 Brave Search (Primary)

| Aspect | Detail |
|--------|--------|
| **URL** | `https://search.brave.com/search?q={query}` |
| **Method** | GET with proper headers |
| **Parsing** | `data-testid="result"` containers |
| **Rate limit** | None (HTML scraping) |
| **User-Agent** | Required (Chrome UA) |

```python
class BraveEngine(SearchEngine):
    name = "brave"
    base_url = "https://search.brave.com/search"
    
    async def search(self, query: str, max_results: int = 10) -> list[SearchResult]:
        params = {"q": query, "source": "web"}
        headers = {"User-Agent": CHROME_UA}
        html = await self.fetch_html(self.base_url, params=params, headers=headers)
        return self._parse_brave_results(html, max_results)
    
    def _parse_brave_results(self, html: str, max_results: int) -> list[SearchResult]:
        # Parse data-testid="result" containers
        # Extract: title, url, snippet
        pass
```

### 6.2 DuckDuckGo (Fallback)

| Aspect | Detail |
|--------|--------|
| **URL** | `https://html.duckduckgo.com/html/?q={query}` |
| **Parsing** | `data-testid="result"` + regex fallback |
| **Issues** | 202/302 redirects, dynamic HTML |

### 6.3 Bing (Fallback)

| Aspect | Detail |
|--------|--------|
| **URL** | `https://www.bing.com/search?q={query}` |
| **Parsing** | `li.b_algo` containers |
| **Reliability** | Good, stable HTML |

---

## 7. Result Merging & Ranking

```python
class ResultMerger:
    def merge(self, engine_results: dict[str, list[SearchResult]], max_results: int) -> list[SearchResult]:
        # 1. Deduplicate by URL (normalize)
        # 2. Score = engine_weight * engine_score + position_bonus
        # 3. Sort by score desc
        # 4. Trim to max_results
        pass
```

**Engine weights:** Brave=1.0, Bing=0.9, DDG=0.8

---

## 8. Error Handling & Fallback Chain

```
try Brave → if fails/empty → try Bing → if fails → try DDG → if fails → return error
```

| Error Type | Handling |
|------------|----------|
| HTTP 429/403 | Retry with backoff, switch engine |
| HTTP 5xx | Retry once, switch engine |
| Parse error | Log, try next engine |
| Timeout (10s) | Switch engine |
| Empty results | Try next engine |

---

## 8. WebSearchTool Interface

```python
class WebSearchTool(Tool):
    name = "websearch"
    description = "Search the web using multiple engines"
    
    schema = ToolSchema(
        name="websearch",
        description="Search the web for information",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "max_results": {"type": "integer", "default": 10},
                "engines": {"type": "array", "items": {"type": "string"}, "default": ["brave", "bing"]},
            },
            "required": ["query"],
        },
    )
    
    async def execute(self, query: str, max_results: int = 10, engines: list[str] = None) -> ToolResult:
        # 1. Select engines
        # 2. Run search concurrently
        # 3. Merge & rank results
        # 4. Return ToolResult with SearchResponse
```

---

## 9. Testing Strategy

| Test | Method |
|------|--------|
| Unit: parsing | HTML fixtures in `tests/fixtures/` |
| Integration: live | `@pytest.mark.live` (optional, skipped by default) |
| Mock: HTTP | `kikolib.http` mock in tests |
| Engine fallback | Mock HTTP 429/500 → verify fallback |

---

## 9.1 Test Fixtures

```
tests/fixtures/
├── brave_results.html
├── bing_results.html
├── duckduckgo_results.html
└── empty_results.html
```

---

## 10. Configuration

```python
# No config file needed - all in code
# Optional: environment variables
SEARXNG_URL = ""  # Optional: if self-hosted SearXNG available
BRAVE_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
REQUEST_TIMEOUT = 10  # seconds
MAX_RETRIES = 2
ENGINE_WEIGHTS = {"brave": 1.0, "bing": 0.9, "duckduckgo": 0.8}
```

---

## 11. Integration with ai-rex

```python
# In runtimelib/tools/__init__.py
from .websearch import WebSearchTool, create_websearch_tool

__all__ = [..., "WebSearchTool", "create_websearch_tool"]
```

**Usage in Layka:**
```
User: "Search for Python asyncio best practices"
→ Layka calls websearch tool
→ Orchestrator runs WebSearchTool
→ Returns SearchResult[] to Layka
→ Layka displays results
```

---

## 12. Risks & Mitigations

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| HTML structure changes | Medium | High | Multiple engines, regex fallback |
| IP blocking | Low | High | Rotate User-Agent, respect robots.txt |
| Engine shutdown | Low | Medium | 3+ engine fallback chain |
| Rate limiting | Low | Medium | Engine rotation, exponential backoff |
| HTML parsing errors | Medium | Medium | Regex fallback, graceful degradation |

---

## 13. Milestones

| Milestone | Target |
|-----------|--------|
| M1: Base scraper + Brave engine | Week 1 |
| M2: Bing + DDG engines + merger | Week 2 |
| M3: WebSearchTool integration | Week 2 |
| M4: Tests + error handling | Week 3 |
| M4: Layka integration | Week 3 |

---

## 14. Appendix: Brave HTML Structure (Reference)

```html
<!-- Brave search result container -->
<div data-testid="result" class="snippet">
  <div class="title">
    <a href="https://example.com" data-testid="result-title-link">
      <span>Page Title</span>
    </a>
  </div>
  <div class="snippet" data-testid="result-snippet">
    Page description/snippet...
  </div>
  <div class="url" data-testid="result-url">
    https://example.com/path
  </div>
</div>
```

---

## 15. Conclusion

**WebSearchModule** will be:
- ✅ **Zero pip dependencies** (stdlib + kikolib only)
- ✅ **Windows compatible** (no Docker, no WSL)
- ✅ **Embeddable** (single `WebSearchTool` class)
- ✅ **Multi-engine** (Brave → Bing → DDG fallback)
- ✅ **Async** (kikolib.http async client)
- ✅ **Tool interface** (compatible with ai-rex Tool system)

**Next step:** Implement `BaseScraper` + `BraveEngine` + `WebSearchTool` in `runtimelib/tools/engines/`.