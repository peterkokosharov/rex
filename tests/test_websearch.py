# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: Nemotron 3 Ultra, claude-sonnet-5-5
# Licensed under MIT License

"""Tests for websearch engines and WebSearchTool."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from runtimelib.tools.engines.brave import BraveEngine
from runtimelib.tools.engines.bing import BingEngine
from runtimelib.tools.engines.duckduckgo import DuckDuckGoEngine
from runtimelib.tools.engines.merger import merge_results
from runtimelib.tools.websearch import WebSearchTool


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "websearch"


def load_fixture(name: str) -> str:
    """Load HTML fixture file."""
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


class TestBraveEngine:
    """Tests for BraveEngine."""

    @pytest.fixture
    def engine(self):
        return BraveEngine()

    def test_parse_brave_results(self, engine):
        """Test parsing Brave HTML results."""
        html = load_fixture("brave_results.html")
        engine._last_html = load_fixture("brave_results.html")

        results = engine._parse_brave_results(html, max_results=10)

        assert len(results) > 0
        assert len(results) <= 10

        # Check first result
        first = results[0]
        assert first["url"] == "https://docs.python.org/3/library/asyncio.html"
        assert "asyncio" in first["title"].lower()
        assert "asyncio" in first["snippet"].lower()
        assert first["engine"] == "brave"
        assert first["score"] > 0
        assert first["rank"] == 1

    def test_max_results_limit(self, engine):
        """Test max_results limit is respected."""
        html = load_fixture("brave_results.html")
        engine._last_html = html

        results = engine._parse_brave_results(html, max_results=2)
        assert len(results) == 2

    def test_empty_query(self, engine):
        """Test empty query returns empty list."""
        import asyncio

        async def test():
            results = await engine.search("", max_results=10)
            assert results == []

        asyncio.run(test())

    def test_parse_result_structure(self, engine):
        """Test result structure is correct."""
        html = load_fixture("brave_results.html")
        engine._last_html = load_fixture("brave_results.html")

        results = engine._parse_brave_results(html, max_results=10)

        for result in results:
            assert "url" in result
            assert "title" in result
            assert "snippet" in result
            assert "engine" in result
            assert "score" in result
            assert "rank" in result
            assert result["engine"] == "brave"
            assert result["score"] > 0
            assert result["rank"] > 0


class TestBingEngine:
    """Tests for BingEngine."""

    @pytest.fixture
    def engine(self):
        return BingEngine()

    def test_parse_bing_results(self, engine):
        """Test parsing Bing HTML results."""
        html = load_fixture("bing_results.html")
        engine._last_html = load_fixture("bing_results.html")

        results = engine._parse_bing_results(html, max_results=10)

        assert len(results) > 0
        assert len(results) <= 10

        first = results[0]
        assert first["url"] == "https://docs.python.org/3/library/asyncio.html"
        assert "asyncio" in first["title"].lower()
        assert first["engine"] == "bing"
        assert first["score"] > 0

    def test_max_results_limit(self, engine):
        """Test max_results limit is respected."""
        html = load_fixture("bing_results.html")
        engine._last_html = load_fixture("bing_results.html")

        results = engine._parse_bing_results(html, max_results=2)
        assert len(results) == 2


class TestDuckDuckGoEngine:
    """Tests for DuckDuckGoEngine."""

    @pytest.fixture
    def engine(self):
        return DuckDuckGoEngine()

    def test_parse_ddg_results(self, engine):
        """Test parsing DuckDuckGo HTML results."""
        html = load_fixture("duckduckgo_results.html")
        engine._last_html = load_fixture("duckduckgo_results.html")

        results = engine._parse_ddg_results(html, max_results=10)

        assert len(results) > 0
        assert len(results) <= 10

        first = results[0]
        assert first["url"] == "https://docs.python.org/3/library/asyncio.html"
        assert "asyncio" in first["title"].lower()
        assert first["engine"] == "duckduckgo"
        assert first["score"] > 0

    def test_max_results_limit(self, engine):
        """Test max_results limit is respected."""
        html = load_fixture("duckduckgo_results.html")
        engine._last_html = load_fixture("duckduckgo_results.html")

        results = engine._parse_ddg_results(html, max_results=2)
        assert len(results) == 2


class TestMerger:
    """Tests for result merger."""

    def test_merge_results_basic(self):
        """Test basic merging of results from multiple engines."""
        engine_results = {
            "brave": [
                {"url": "https://example.com/1", "title": "Result 1", "snippet": "snippet 1", "engine": "brave", "score": 1.0, "rank": 1},
                {"url": "https://example.com/2", "title": "Result 2", "snippet": "snippet 2", "engine": "brave", "score": 0.98, "rank": 2},
            ],
            "bing": [
                {"url": "https://example.com/1", "title": "Result 1", "snippet": "snippet 1", "engine": "bing", "score": 1.0, "rank": 1},
                {"url": "https://example.com/3", "title": "Result 3", "snippet": "snippet 3", "engine": "bing", "score": 0.98, "rank": 2},
            ],
            "duckduckgo": [
                {"url": "https://example.com/2", "title": "Result 2", "snippet": "snippet 2", "engine": "duckduckgo", "score": 0.9, "rank": 1},
                {"url": "https://example.com/3", "title": "Result 3", "snippet": "snippet 3", "engine": "duckduckgo", "score": 0.88, "rank": 2},
            ],
        }

        merged = merge_results(engine_results, max_results=5)

        # Should have 3 unique URLs (deduplicated)
        urls = {r["url"] for r in merged}
        assert len(urls) == 3

        # Check deduplication worked - URL 1 appears in brave and bing
        url1_results = [r for r in merged if "example.com/1" in r["url"]]
        assert len(url1_results) == 1

        # Check ranking - highest score first
        assert merged[0]["url"] == "https://example.com/1"

        # Check max_results limit
        merged_limited = merge_results(
            {"brave": [{"url": f"https://example.com/{i}", "title": f"R{i}", "snippet": "s", "engine": "brave", "score": 1.0, "rank": i} for i in range(1, 10)]},
            max_results=3
        )
        assert len(merged_limited) == 3

    def test_deduplication_prefers_higher_score(self):
        """On a weighted-score tie, dedup keeps the higher single-engine score.

        brave: 0.9 x weight 1.0 = 0.90; bing: 1.0 x weight 0.9 = 0.90 -> tie on
        the adjusted score. The documented tie-break (higher raw single-engine
        score, see merger.rank_key) then keeps bing (raw 1.0 > brave's 0.9).
        """
        merged = merge_results({"brave": [{"url": "https://example.com/1", "title": "R1", "snippet": "s", "engine": "brave", "score": 0.9, "rank": 1}],
                                 "bing": [{"url": "https://example.com/1", "title": "R1", "snippet": "s", "engine": "bing", "score": 1.0, "rank": 1}]}, max_results=5)

        # Should have 1 result, with bing's higher single-engine score
        assert len(merged) == 1
        assert merged[0]["engine"] == "bing"


class TestWebSearchTool:
    """Tests for WebSearchTool."""

    @pytest.fixture
    def tool(self):
        return WebSearchTool()

    def test_schema(self, tool):
        """Test tool schema is correct (a proper ToolSchema, like fileops/edit)."""
        schema = tool.schema
        assert schema.name == "websearch"
        assert "query" in schema.parameters["properties"]
        assert "max_results" in schema.parameters["properties"]
        assert "engines" in schema.parameters["properties"]
        assert schema.parameters["required"] == ["query"]

    def test_to_openai(self, tool):
        """Test OpenAI function format matches the schema's own conversion."""
        openai_schema = tool.to_openai()
        assert openai_schema == tool.schema.to_openai()
        assert openai_schema["type"] == "function"
        assert openai_schema["function"]["name"] == "websearch"

    def test_execute_empty_query(self, tool):
        """Test empty query returns error."""
        import asyncio

        async def test():
            result = await tool._execute_async(query="")
            assert result["success"] is False
            assert "Empty query" in result["error"]

        asyncio.run(test())

    def test_schema_structure(self, tool):
        """Test schema has correct structure."""
        params = tool.schema.parameters["properties"]

        assert params["query"]["type"] == "string"
        assert params["max_results"]["type"] == "integer"
        assert params["max_results"]["default"] == 10
        assert params["engines"]["default"] == ["brave", "bing", "duckduckgo"]


# Live tests (skipped by default, run with pytest -m live)
class TestLiveSearch:
    """Live search tests - require network, marked as live."""

    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_brave_live(self):
        """Live test of Brave engine."""
        from runtimelib.tools.engines.brave import BraveEngine

        engine = BraveEngine()
        results = await engine.search("Python asyncio", max_results=3)

        assert len(results) > 0
        assert len(results) <= 3
        for r in results:
            assert "url" in r
            assert "title" in r
            assert "engine" in r
            assert r["engine"] == "brave"

    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_bing_live(self):
        from runtimelib.tools.engines.bing import BingEngine

        engine = BingEngine()
        results = await engine.search("Python asyncio", max_results=3)

        assert len(results) > 0
        assert len(results) <= 3

    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_ddg_live(self):
        from runtimelib.tools.engines.duckduckgo import DuckDuckGoEngine

        engine = DuckDuckGoEngine()
        results = await engine.search("Python asyncio", max_results=3)

        assert len(results) > 0
        assert len(results) <= 3

    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_websearch_tool_live(self):
        from runtimelib.tools.websearch import WebSearchTool

        tool = WebSearchTool()
        result = await tool._execute_async(query="Python asyncio", max_results=3, engines=["brave", "bing"])

        assert result["success"] is True
        assert len(result["results"]) > 0
        assert result["count"] > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])