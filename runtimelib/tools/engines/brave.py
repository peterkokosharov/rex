# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: Nemotron 3 Ultra
# Licensed under MIT License

"""Brave Search engine implementation."""

from __future__ import annotations

import html
import re
import urllib.parse
from typing import Any
from .base import SearchEngine

# User agent and headers for Brave
CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

BRAVE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


class BraveEngine(SearchEngine):
    """Brave Search engine using HTML scraping."""

    name = "brave"
    base_url = "https://search.brave.com/search"

    def __init__(self) -> None:
        super().__init__()
        self.name = "brave"
        self.base_url = "https://search.brave.com/search"
        self._last_html = ""

    async def search(self, query: str, max_results: int = 10) -> list:
        """Search Brave and return results."""
        if not query or not query.strip():
            return []

        params = {
            "q": query,
            "source": "web",
        }

        try:
            html_content = await self.fetch_html(
                self.base_url,
                params=params,
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
            return self._parse_brave_results(html_content, max_results)

        except Exception as e:
            return []

    def _parse_brave_results(self, html_content: str, max_results: int) -> list:
        """Parse Brave Search HTML results using data-testid attributes."""
        results = []

        # Method 1: Find result containers with data-testid="result"
        result_containers = re.findall(
            r'data-testid="result"[^>]*>(.*?)(?=data-testid="result"|$)',
            html_content,
            re.DOTALL | re.IGNORECASE
        )

        results = []

        if result_containers:
            # Parse each container
            for container in result_containers[:max_results]:
                # Extract title and URL
                title_match = re.search(
                    r'data-testid="result-title-link"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
                    container, re.DOTALL | re.IGNORECASE
                )
                if not title_match:
                    continue

                url = self.normalize_url(title_match.group(1))
                title = html.unescape(title_match.group(2)).strip()

                if not title or not url:
                    continue

                # Extract snippet
                snippet_match = re.search(
                    r'data-testid="result-snippet"[^>]*>(.*?)</div>',
                    container, re.DOTALL
                )
                snippet = html.unescape(snippet_match.group(1)).strip() if snippet_match else ""

                # Extract URL display
                url_match = re.search(
                    r'data-testid="result-url"[^>]*>(.*?)</div>',
                    container, re.DOTALL
                )
                display_url = html.unescape(url_match.group(1)).strip() if url_match else ""

                rank = len(results) + 1
                score = 1.0 - (rank - 1) * 0.02

                results.append({
                    "url": url,
                    "title": title,
                    "snippet": snippet,
                    "engine": "brave",
                    "score": max(score, 0.1),
                    "rank": rank,
                })

                if len(results) >= max_results:
                    break

            return results

        # Fallback: try to find title links directly
        title_matches = re.findall(
            r'data-testid="result-title-link"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
            html_content,
            re.DOTALL | re.IGNORECASE
        )

        results = []
        for i, (url, title) in enumerate(title_matches[:50]):
            title = html.unescape(title).strip()
            url = self.normalize_url(url)

            if not title or not url:
                continue

            rank = len(results) + 1
            score = 1.0 - (rank - 1) * 0.02

            # Try to find snippet for this result
            snippet = ""
            snippet_match = re.search(
                r'data-testid="result-snippet"[^>]*>(.*?)</div>',
                self._last_html if hasattr(self, '_last_html') else '',
                re.DOTALL
            )
            if snippet_match:
                snippet = html.unescape(snippet_match.group(1)).strip()

            results.append({
                "url": self.normalize_url(url),
                "title": title,
                "snippet": "",
                "engine": "brave",
                "score": max(1.0 - (len(results)) * 0.02, 0.1),
                "rank": len(results) + 1,
            })

            if len(results) >= max_results:
                break

        return results