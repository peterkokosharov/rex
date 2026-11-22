# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra
# Licensed under MIT License

"""Agent Client Protocol server for rex (editors -> rex JSONL bridge)."""

from .server import ACPServer, main, run_acp

__all__ = ["ACPServer", "main", "run_acp"]
