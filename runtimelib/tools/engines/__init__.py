# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: Nemotron 3 Ultra
# Licensed under MIT License

"""Search engine implementations."""

from .base import SearchEngine, SearchResult, SearchResponse
from .brave import BraveEngine
from .bing import BingEngine
from .duckduckgo import DuckDuckGoEngine

__all__ = [
    "SearchEngine",
    "SearchResult",
    "SearchResponse",
    "BraveEngine",
    "BingEngine",
    "DuckDuckGoEngine",
]

# Engine registry
ENGINES = {
    "brave": "BraveEngine",
    "bing": "BingEngine",
    "duckduckgo": "DuckDuckGoEngine",
}

DEFAULT_ENGINES = ["brave", "bing", "duckduckgo"]
ENGINE_WEIGHTS = {"brave": 1.0, "bing": 0.9, "duckduckgo": 0.8}