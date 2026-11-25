# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: Nemotron 3 Ultra, claude-sonnet-5-5
# Licensed under MIT License

"""Brave Search engine implementation."""

from __future__ import annotations

import re
from typing import Any

from .base import SearchEngine

# Brave wraps each organic result in a container carrying data-testid="result".
# The title is an <a> with data-testid="result-title-link"; the snippet lives in
# an element with data-testid="result-snippet". Attribute order varies, so the
# patterns match data-testid and href independently of their position.
_RESULT_SPLIT = re.compile(r'<(?:div|article)\b[^>]*\bdata-testid="result"', re.IGNORECASE)
_TITLE_LINK = re.compile(
    r'<a\b([^>]*\bdata-testid="result-title-link"[^>]*)>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_HREF = re.compile(r'\bhref="([^"]*)"', re.IGNORECASE)
_SNIPPET = re.compile(
    r'data-testid="result-snippet"[^>]*>(.*?)</(?:div|p|span)>',
    re.IGNORECASE | re.DOTALL,
)


class BraveEngine(SearchEngine):
    """Brave Search engine using HTML scraping."""

    name = "brave"
    base_url = "https://search.brave.com/search"

    def __init__(self, user_agent: str | None = None) -> None:
        super().__init__(user_agent)
        self.name = "brave"
        self.base_url = "https://search.brave.com/search"
        self._last_html = ""

    async def search(self, query: str, max_results: int = 10) -> list[dict[str, Any]]:
        """Search Brave and return results (raises on fetch failure)."""
        if not query or not query.strip():
            return []

        html_content = await self.fetch_html(
            self.base_url,
            params={"q": query, "source": "web"},
        )
        self._last_html = html_content
        return self._parse_brave_results(html_content, max_results)

    def _parse_brave_results(self, html_content: str, max_results: int) -> list[dict[str, Any]]:
        """Parse Brave Search HTML into result dicts."""
        results: list[dict[str, Any]] = []

        # Split the page into per-result chunks on the result container marker.
        # The first chunk is the page head (before any result) and is ignored
        # because it holds no title link.
        chunks = _RESULT_SPLIT.split(html_content)
        for chunk in chunks:
            title_match = _TITLE_LINK.search(chunk)
            if not title_match:
                continue

            attrs, inner = title_match.group(1), title_match.group(2)
            href_match = _HREF.search(attrs)
            if not href_match:
                continue

            url = self.normalize_url(href_match.group(1))
            title = self.clean_text(inner)
            if not url or not title:
                continue

            snippet_match = _SNIPPET.search(chunk)
            snippet = self.clean_text(snippet_match.group(1)) if snippet_match else ""

            rank = len(results) + 1
            score = max(1.0 - (rank - 1) * 0.02, 0.1)
            results.append({
                "url": url,
                "title": title,
                "snippet": snippet,
                "engine": "brave",
                "score": score,
                "rank": rank,
            })

            if len(results) >= max_results:
                break

        return results
