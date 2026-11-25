# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: Nemotron 3 Ultra, claude-sonnet-5-5
# Licensed under MIT License

"""Base search engine classes."""

from __future__ import annotations

import html
import re
import urllib.parse
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

# kikolib is installed as a top-level package (see pyproject: where = [".", "vendor"]).
# Import it directly; never mutate the global sys.path on import.
from kikolib import http

# Honest, non-impersonating default User-Agent. A caller may override it with a
# configured value (see WebSearchTool). We do not pretend to be Chrome.
DEFAULT_USER_AGENT = "rex-websearch/1.0 (+https://github.com/; stdlib http)"

# Base request headers. No Accept-Encoding: vendor/kikolib/http.py does not
# decompress, and it already forces "Accept-Encoding: identity", so asking for
# gzip would only yield undecodable bytes.
DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# kikolib timeouts are in SECONDS, not milliseconds.
REQUEST_TIMEOUT = 10.0  # seconds


@dataclass
class SearchResult:
    """A single search result."""
    url: str
    title: str
    snippet: str
    engine: str
    score: float = 0.0
    rank: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "snippet": self.snippet,
            "engine": self.engine,
            "score": self.score,
            "rank": self.rank,
        }


@dataclass
class SearchResponse:
    """Complete search response."""
    query: str
    results: list[SearchResult] = field(default_factory=list)
    engine: str = ""
    total_results: int = 0
    elapsed_ms: int = 0


class SearchEngine(ABC):
    """Abstract base class for search engines."""

    name: str = ""
    base_url: str = ""

    def __init__(self, user_agent: str | None = None) -> None:
        self.user_agent = user_agent or DEFAULT_USER_AGENT
        self.client = http.client()

    @abstractmethod
    async def search(self, query: str, max_results: int = 10) -> list[dict[str, Any]]:
        """Search for query and return a list of result dicts."""
        raise NotImplementedError

    async def fetch_html(self, url: str, params: dict[str, str] | None = None,
                         headers: dict[str, str] | None = None) -> str:
        """Fetch HTML content from URL (raises RuntimeError on any failure)."""
        full_url = url
        if params:
            query_string = urllib.parse.urlencode(params)
            separator = "&" if "?" in url else "?"
            full_url = f"{url}{separator}{query_string}"

        # Build the headers that are actually sent: defaults + our UA, then any
        # per-call overrides. (The old code built this dict but then sent the
        # raw `headers` argument, so the defaults never applied.)
        request_headers = {**DEFAULT_HEADERS, "User-Agent": self.user_agent}
        if headers:
            request_headers.update(headers)

        try:
            response = self.client.request(
                "GET", full_url,
                headers=request_headers,
                timeout=REQUEST_TIMEOUT,
            )

            if response.status not in (200, 202):
                raise RuntimeError(f"HTTP {response.status}")

            body = response.read()
            return body.decode("utf-8", errors="replace")

        except Exception as e:
            raise RuntimeError(f"Fetch failed: {e}")

    def normalize_url(self, url: str) -> str:
        """Normalize a URL, resolving a relative one against this engine's base."""
        if not url:
            return ""
        url = html.unescape(url.strip())
        if url.startswith(("http://", "https://")):
            return url
        # Resolve //host, /path and relative refs against the engine's own base
        # (not a hard-coded bing.com).
        base = self.base_url or "https://www.bing.com/"
        return urllib.parse.urljoin(base, url)

    def clean_text(self, text: str) -> str:
        """Clean HTML entities and tags from text."""
        if not text:
            return ""
        text = html.unescape(text)
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def extract_domain(self, url: str) -> str:
        """Extract domain from URL for ranking."""
        try:
            parsed = urllib.parse.urlparse(self.normalize_url(url))
            return parsed.netloc.lower().replace("www.", "")
        except Exception:
            return ""
