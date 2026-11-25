# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: Nemotron 3 Ultra
# Licensed under MIT License

"""Base search engine classes."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
import html
import re
import urllib.parse

# Import kikolib http - vendor/kikolib
import sys
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "vendor"))
from kikolib import http

from kikolib import http as kikolib_http

# User agent for requests
CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "User-Agent": CHROME_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

REQUEST_TIMEOUT = 10000  # milliseconds


@dataclass
class SearchResult:
    """A single search result."""
    url: str
    title: str
    snippet: str
    engine: str
    score: float = 0.0
    rank: int = 0


@dataclass
class SearchResponse:
    """Complete search response."""
    query: str
    results: list["SearchResult"]
    engine: str
    total_results: int
    elapsed_ms: int


class SearchEngine(ABC):
    """Abstract base class for search engines."""

    name: str
    base_url: str

    def __init__(self) -> None:
        self.client = http.client()

    @abstractmethod
    async def search(self, query: str, max_results: int = 10) -> list["SearchResult"]:
        """Search for query and return results."""
        pass

    async def fetch_html(self, url: str, params: dict[str, str] | None = None,
                         headers: dict[str, str] | None = None) -> str:
        """Fetch HTML content from URL."""
        full_url = url
        if params:
            query_string = urllib.parse.urlencode(params)
            separator = "&" if "?" in url else "?"
            full_url = f"{url}{separator}{query_string}"

        request_headers = {**DEFAULT_HEADERS}
        if headers:
            request_headers.update(headers)

        try:
            response = self.client.request(
                "GET", full_url,
                headers=headers,
                timeout=REQUEST_TIMEOUT
            )

            if response.status not in (200, 202):
                raise RuntimeError(f"HTTP {response.status}")

            body = response.read()
            return body.decode("utf-8", errors="replace")

        except Exception as e:
            raise RuntimeError(f"Fetch failed: {e}")

    def normalize_url(self, url: str) -> str:
        """Normalize URL (handle relative, //, etc.)."""
        if not url:
            return ""
        url = html.unescape(url.strip())
        if url.startswith("//"):
            return "https:" + url
        if url.startswith("/"):
            return "https://www.bing.com" + url  # fallback base
        return url

    def clean_text(self, text: str) -> str:
        """Clean HTML entities and tags from text."""
        if not text:
            return ""
        # Decode HTML entities
        text = html.unescape(text)
        # Remove HTML tags
        text = re.sub(r"<[^>]+>", "", text)
        # Normalize whitespace
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def extract_domain(self, url: str) -> str:
        """Extract domain from URL for ranking."""
        try:
            parsed = urllib.parse.urlparse(self.normalize_url(url))
            return parsed.netloc.lower().replace("www.", "")
        except Exception:
            return ""


class SearchResult:
    """A single search result."""
    def __init__(self, url: str, title: str, snippet: str, engine: str,
                 score: float = 0.0, rank: int = 0):
        self.url = url
        self.title = title
        self.snippet = snippet
        self.engine = engine
        self.score = score
        self.rank = rank

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "title": self.title,
            "snippet": self.snippet,
            "engine": self.engine,
            "score": self.score,
            "rank": self.rank,
        }


class SearchResponse:
    """Complete search response."""
    def __init__(self, query: str, results: list, engine: str,
                 total_results: int, elapsed_ms: int):
        self.query = query
        self.results = results
        self.engine = engine
        self.total_results = total_results
        self.elapsed_ms = elapsed_ms