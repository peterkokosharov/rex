# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nemotron-super
# Licensed under MIT License

"""Regression tests for Bing and DuckDuckGo HTML parsing (S6019 / S8786 fixes)."""

from __future__ import annotations

import time
from pathlib import Path

from runtimelib.tools.engines.bing import BingEngine
from runtimelib.tools.engines.duckduckgo import DuckDuckGoEngine


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "websearch"


def load_fixture(name: str) -> str:
    """Load HTML fixture file."""
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


class TestBingParsingRegression:
    """Regression tests for BingEngine._parse_bing_results."""

    def test_bing_fixture_yields_all_five_nonempty_results(self):
        engine = BingEngine()
        html = load_fixture("bing_results.html")
        results = engine._parse_bing_results(html, max_results=10)

        assert len(results) == 5
        for result in results:
            assert result["url"]
            assert result["title"]
        assert results[0]["url"] == "https://docs.python.org/3/library/asyncio.html"

    def test_bing_parser_is_fast_on_hostile_html(self):
        engine = BingEngine()
        hostile = '<li class="b_algo"' + "<a " * 300000

        start = time.perf_counter()
        results = engine._parse_bing_results(hostile, max_results=10)
        elapsed = time.perf_counter() - start

        assert elapsed < 1.0
        assert results == []


class TestDuckDuckGoParsingRegression:
    """Regression tests for DuckDuckGoEngine._parse_ddg_results."""

    def test_ddg_fixture_yields_all_five_nonempty_results(self):
        engine = DuckDuckGoEngine()
        html = load_fixture("duckduckgo_results.html")
        results = engine._parse_ddg_results(html, max_results=10)

        assert len(results) == 5
        for result in results:
            assert result["url"]
            assert result["title"]
        assert results[0]["url"] == "https://docs.python.org/3/library/asyncio.html"

    def test_ddg_parser_is_fast_on_hostile_html(self):
        engine = DuckDuckGoEngine()
        hostile = 'data-testid="result">' + "<a " * 300000

        start = time.perf_counter()
        results = engine._parse_ddg_results(hostile, max_results=10)
        elapsed = time.perf_counter() - start

        assert elapsed < 1.0
        assert results == []
