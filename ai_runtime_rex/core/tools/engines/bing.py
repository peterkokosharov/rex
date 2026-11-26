# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: Nemotron 3 Ultra, claude-sonnet-5-5, nemotron-super
# Licensed under MIT License

"""Bing Search engine implementation."""

from __future__ import annotations

import re
from typing import Any
from .base import SearchEngine

BING_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


class BingEngine(SearchEngine):
    """Bing Search engine using HTML scraping."""

    name = "bing"
    base_url = "https://www.bing.com/search"

    def __init__(self, user_agent: str | None = None) -> None:
        super().__init__(user_agent)
        self.name = "bing"
        self.base_url = "https://www.bing.com/search"
        self._last_html = ""

    async def search(self, query: str, max_results: int = 10) -> list:
        """Search Bing and return results (raises on fetch failure)."""
        if not query or not query.strip():
            return []

        html_content = await self.fetch_html(self.base_url, params={"q": query})
        self._last_html = html_content
        return self._parse_bing_results(html_content, max_results)

    def _parse_bing_results(self, html_content: str, max_results: int) -> list:
        """Parse Bing HTML into result dicts.

        We split on the result-container marker with a plain string op (no regex
        on the whole untrusted page), then read each field from the small block
        with bounded patterns, so there is no super-linear backtracking (S8786)
        and no empty-match ambiguity (S6019).
        """
        results = []
        # First chunk is page preamble before the first result; drop it.
        blocks = html_content.split('<li class="b_algo"')[1:]
        for block in blocks:
            if len(results) >= max_results:
                break
            title_match = re.search(
                r'<h2[^>]*>\s*<a[^>]*href="([^"]*)"[^>]*>(.{0,400}?)</a>',
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
                r'<p[^>]*>(.{0,2000}?)</p>', block, re.IGNORECASE | re.DOTALL,
            )
            if snippet_match:
                snippet = self.clean_text(snippet_match.group(1))
            rank = len(results) + 1
            results.append({
                "url": url,
                "title": title,
                "snippet": snippet,
                "engine": "bing",
                "score": max(0.9 - (rank - 1) * 0.02, 0.1),
                "rank": rank,
            })
        return results
