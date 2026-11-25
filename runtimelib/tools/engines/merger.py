# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: Nemotron 3 Ultra
# Licensed under MIT License

"""Result merger for combining search results from multiple engines."""

from __future__ import annotations

import re
from typing import Any
from .base import SearchResult


# Engine weights for ranking
ENGINE_WEIGHTS = {
    "brave": 1.0,
    "bing": 0.9,
    "duckduckgo": 0.8,
}


def normalize_url(url: str) -> str:
    """Normalize URL for deduplication."""
    if not url:
        return ""
    url = url.strip()
    # Remove fragment
    url = url.split("#")[0]
    # Remove trailing slash
    url = url.rstrip("/")
    # Normalize protocol
    if url.startswith("http://"):
        url = "https://" + url[7:]
    # Remove www.
    url = re.sub(r"^https?://www\.", "https://", url)
    return url.lower()


def merge_results(
    engine_results: dict[str, list[dict]],
    max_results: int = 10,
    engine_weights: dict[str, float] = None
) -> list:
    """
    Merge results from multiple engines, deduplicate, rank, and trim.

    Args:
        engine_results: Dict of engine_name -> list of result dicts
        max_results: Maximum number of results to return
        engine_weights: Weight multiplier per engine

    Returns:
        List of merged result dicts, sorted by score
    """
    if engine_weights is None:
        engine_weights = {"brave": 1.0, "bing": 0.9, "duckduckgo": 0.8}

    # Collect all results with engine weight applied
    all_results = []
    for engine, results in engine_results.items():
        weight = engine_weights.get(engine, 0.5)
        for i, result in enumerate(results):
            # Apply engine weight and position penalty
            base_score = result.get("score", 1.0)
            position_penalty = (result.get("rank", 1) - 1) * 0.02
            adjusted_score = result.get("score", 1.0) * weight - (result.get("rank", 1) - 1) * 0.02
            adjusted_score = max(adjusted_score, 0.01)

            # Create a copy with adjusted score
            result = dict(result)
            result["adjusted_score"] = adjusted_score
            result["engine"] = engine  # Ensure engine is set
            all_results.append(result)

    # Deduplicate by normalized URL
    seen_urls = {}
    for result in all_results:
        url = result.get("url", "")
        norm_url = normalize_url(url)

        if norm_url not in seen_urls:
            seen_urls[norm_url] = result
        else:
            # Keep the one with higher score
            existing = seen_urls[norm_url]
            if result.get("adjusted_score", 0) > existing.get("adjusted_score", 0):
                seen_urls[norm_url] = result

    # Sort by adjusted score descending
    merged = sorted(seen_urls.values(), key=lambda r: r.get("adjusted_score", 0), reverse=True)

    # Assign final ranks
    for i, result in enumerate(merged[:max_results]):
        result["rank"] = i + 1

    return merged[:max_results]


def merge_results_sync(
    engine_results: dict[str, list[dict]],
    max_results: int = 10,
    engine_weights: dict[str, float] = None
) -> list:
    """Synchronous version of merge_results."""
    return merge_results(engine_results, max_results, engine_weights)