# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: Nemotron 3 Ultra
# Licensed under MIT License

"""DuckDuckGo Search engine implementation."""

from __future__ import annotations

import html
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

    def __init__(self) -> None:
        super().__init__()
        self.name = "duckduckgo"
        self.base_url = "https://html.duckduckgo.com/html/"
        self._last_html = ""

    async def search(self, query: str, max_results: int = 10) -> list:
        """Search DuckDuckGo and return results."""
        if not query or not query.strip():
            return []

        params = {
            "q": query,
            "kl": "us-en",
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
            return self._parse_ddg_results(html_content, max_results)

        except Exception as e:
            return []

    def _parse_ddg_results(self, html_content: str, max_results: int) -> list:
        """Parse DuckDuckGo HTML results."""
        results = []

        # DDG uses data-testid attributes for results
        # Pattern: data-testid="result" containers
        # Title: data-testid="result-title-a"
        # Snippet: data-testid="result-snippet"
        # URL: data-testid="result-url"

        # Try to find result containers with data-testid="result"
        result_containers = re.findall(
            r'data-testid="result"[^>]*>(.*?)(?=data-testid="result"|$)',
            html_content,
            re.DOTALL | re.IGNORECASE
        )

        results = []

        if result_containers:
            for container in result_containers[:max_results]:
                # Extract title and URL
                title_match = re.search(
                    r'data-testid="result-title-a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
                    container, re.DOTALL | re.IGNORECASE
                )
                if not title_match:
                    # Try alternative
                    title_match = re.search(
                        r'class="result__title"[^>]*><a[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
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
                    r'data-testid="result-snippet"[^>]*>(.*?)</a>',
                    container, re.DOTALL
                )
                if not snippet_match:
                    snippet_match = re.search(
                        r'class="result__snippet"[^>]*>(.*?)</a>',
                        container, re.DOTALL
                    )
                if snippet_match:
                    snippet = html.unescape(snippet_match.group(1)).strip()

                # Extract URL
                url_match = re.search(
                    r'data-testid="result-url"[^>]*>(.*?)</span>',
                    container, re.DOTALL
                )
                if not url_match:
                    url_match = re.search(
                        r'class="result__url"[^>]*>(.*?)</span>',
                        container, re.DOTALL
                    )
                display_url = html.unescape(url_match.group(1)).strip() if url_match else ""

                rank = len(results) + 1
                score = 0.8 - (len(results)) * 0.02

                results.append({
                    "url": self.normalize_url(result_match.group(1)) if 'result_match' in locals() else url,
                    "title": title,
                    "snippet": snippet,
                    "engine": "duckduckgo",
                    "score": max(score, 0.1),
                    "rank": len(results) + 1,
                })

                if len(results) >= max_results:
                    break

            return results

        # Fallback: try to find result-title-a links
        title_matches = re.findall(
            r'data-testid="result-title-a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
            html_content, re.DOTALL | re.IGNORECASE
        )

        if not title_matches:
            # Try alternative class-based pattern
            title_matches = re.findall(
                r'class="result__title"[^>]*><a[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
                html_content, re.DOTALL | re.IGNORECASE
            )

        results = []
        for i, (url, title) in enumerate(title_matches[:50]):
            title = html.unescape(title).strip()
            url = self.normalize_url(url)

            if not title or not url:
                continue

            rank = len(results) + 1
            score = 0.8 - (len(results)) * 0.02

            # Try to find snippet
            snippet = ""
            snippet_match = re.search(
                r'data-testid="result-snippet"[^>]*>(.*?)</a>',
                html_content, re.DOTALL
            )
            if not snippet_match:
                snippet_match = re.search(
                    r'class="result__snippet"[^>]*>(.*?)</a>',
                    html_content, re.DOTALL
                )
            if snippet_match:
                snippet = html.unescape(snippet_match.group(1)).strip()

            results.append({
                "url": self.normalize_url(url),
                "title": title,
                "snippet": snippet,
                "engine": "duckduckgo",
                "score": max(0.8 - len(results) * 0.02, 0.1),
                "rank": len(results) + 1,
            })

            if len(results) >= max_results:
                break

        return results