# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: Nemotron 3 Ultra
# Licensed under MIT License

"""Bing Search engine implementation."""

from __future__ import annotations

import html
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

    def __init__(self) -> None:
        super().__init__()
        self.name = "bing"
        self.base_url = "https://www.bing.com/search"
        self._last_html = ""

    async def search(self, query: str, max_results: int = 10) -> list:
        """Search Bing and return results."""
        if not query or not query.strip():
            return []

        params = {
            "q": query,
        }

        try:
            html_content = await self.fetch_html(
                self.base_url,
                params={"q": query},
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                                  "Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.5",
                    "Accept-Encoding": "gzip, deflate",
                    "Connection": "keep-alive",
                    "Upgrade-Insecure-Requests": "1",
                }
            )

            # Store for parsing
            self._last_html = html_content
            return self._parse_bing_results(html_content, max_results)

        except Exception as e:
            return []

    def _parse_bing_results(self, html_content: str, max_results: int) -> list:
        """Parse Bing Search HTML results."""
        results = []

        # Bing uses li.b_algo for result containers
        # Title: h2 a
        # Snippet: p or div.b_caption p
        # URL: cite or a cite

        # Pattern for result containers
        result_containers = re.findall(
            r'<li class="b_algo"[^>]*>(.*?)(?=<li class="b_algo"|</ol>|$)',
            html_content,
            re.DOTALL | re.IGNORECASE
        )

        results = []

        if result_containers:
            for container in result_containers[:max_results]:
                # Extract title and URL
                title_match = re.search(
                    r'<h2[^>]*><a[^>]*href="([^"]*)"[^>]*>(.*?)</a></h2>',
                    container, re.DOTALL | re.IGNORECASE
                )
                if not title_match:
                    # Try alternative pattern
                    title_match = re.search(
                        r'<h2[^>]*><a[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
                        container, re.DOTALL | re.IGNORECASE
                    )

                if not title_match:
                    continue

                url = self.normalize_url(title_match.group(1))
                title = html.unescape(title_match.group(2)).strip()

                if not title or not url:
                    continue

                # Extract snippet
                snippet = ""
                snippet_match = re.search(
                    r'<div class="b_caption"[^>]*>.*?<p[^>]*>(.*?)</p>',
                    container, re.DOTALL
                )
                if not snippet_match:
                    # Try alternative snippet pattern
                    snippet_match = re.search(
                        r'<p[^>]*>(.*?)</p>',
                        container, re.DOTALL
                    )
                if snippet_match:
                    snippet = html.unescape(snippet_match.group(1)).strip()

                # Extract URL
                url_match = re.search(
                    r'<cite[^>]*>(.*?)</cite>',
                    container, re.DOTALL | re.IGNORECASE
                )
                display_url = html.unescape(url_match.group(1)).strip() if url_match else ""

                rank = len(results) + 1
                score = 0.9 - (len(results)) * 0.02

                results.append({
                    "url": url,
                    "title": title,
                    "snippet": snippet,
                    "engine": "bing",
                    "score": max(score, 0.1),
                    "rank": len(results) + 1,
                })

                if len(results) >= max_results:
                    break

        # Fallback: try to find any h2 links
        if not results:
            title_matches = re.findall(
                r'<h2[^>]*><a[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
                html_content, re.DOTALL | re.IGNORECASE
            )

            results = []
            for i, (url, title) in enumerate(title_matches[:50]):
                title = html.unescape(title).strip()
                url = self.normalize_url(url)

                if not title or not url:
                    continue

                rank = len(results) + 1
                score = 0.9 - (len(results)) * 0.02

                results.append({
                    "url": self.normalize_url(url),
                    "title": title,
                    "snippet": "",
                    "engine": "bing",
                    "score": max(0.9 - len(results) * 0.02, 0.1),
                    "rank": len(results) + 1,
                })

                if len(results) >= max_results:
                    break

        return results