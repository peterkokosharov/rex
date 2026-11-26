# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: Nemotron 3 Ultra, claude-sonnet-5-5, nemotron-super
# Licensed under MIT License

"""DuckDuckGo Search engine implementation."""

from __future__ import annotations

import re
from typing import Any
from .base import SearchEngine

DDG_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


class DuckDuckGoEngine(SearchEngine):
    """DuckDuckGo Search engine using HTML scraping."""

    name = "duckduckgo"
    base_url = "https://html.duckduckgo.com/html/"

    def __init__(self, user_agent: str | None = None) -> None:
        super().__init__(user_agent)
        self.name = "duckduckgo"
        self.base_url = "https://html.duckduckgo.com/html/"
        self._last_html = ""

    async def search(self, query: str, max_results: int = 10) -> list:
        """Search DuckDuckGo and return results (raises on fetch failure)."""
        if not query or not query.strip():
            return []

        html_content = await self.fetch_html(
            self.base_url,
            params={"q": query, "kl": "us-en"},
        )
        self._last_html = html_content
        return self._parse_ddg_results(html_content, max_results)

    def _parse_ddg_results(self, html_content: str, max_results: int) -> list:
        """Parse DuckDuckGo HTML into result dicts.

        Same approach as Bing: split on the result-container marker, then read
        each field from the small block with bounded patterns (no S8786/S6019).
        The split marker includes the closing '>' so it matches only the
        container div, not the 'result-title-a'/'result-snippet'/'result-url'
        attributes that share the 'result' prefix.
        """
        results = []
        blocks = html_content.split('data-testid="result">')[1:]
        for block in blocks:
            if len(results) >= max_results:
                break
            title_match = re.search(
                r'data-testid="result-title-a"[^>]*href="([^"]*)"[^>]*>(.{0,400}?)</a>',
                block, re.IGNORECASE | re.DOTALL,
            )
            if not title_match:
                continue
            url = self.normalize_url(title_match.group(1))
            title = self.clean_text(title_match.group(2))
            if not url or not title:
                continue
            snippet = ""
            snippet_match = re.search(
                r'data-testid="result-snippet"[^>]*>(.{0,2000}?)</a>',
                block, re.IGNORECASE | re.DOTALL,
            )
            if snippet_match:
                snippet = self.clean_text(snippet_match.group(1))
            rank = len(results) + 1
            results.append({
                "url": url,
                "title": title,
                "snippet": snippet,
                "engine": "duckduckgo",
                "score": max(0.8 - (rank - 1) * 0.02, 0.1),
                "rank": rank,
            })
        return results
