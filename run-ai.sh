#!/bin/bash
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: NVIDIA Nemotron 3 Ultra
# Licensed under MIT License
# Run script for Runtime AI - tests LiteLLM proxy on port 4000 and runs CLI

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT"

echo "🔍 Checking LiteLLM proxy on port 4000..."
if curl -s http://127.0.0.1:4000/health > /dev/null 2>&1; then
    echo "✅ LiteLLM proxy is running on port 4000"
    echo "📋 Available models:"
    curl -s http://127.0.0.1:4000/v1/models | jq -r '.data[].id' 2>/dev/null || curl -s http://127.0.0.1:4000/v1/models
else
    echo "❌ LiteLLM proxy NOT running on port 4000"
    echo "   Start it with: cd /home/petark/kiko/kiko && python3 -m litellm --config config.yaml --port 4000"
    exit 1
fi

echo ""
echo "🚀 Running Runtime AI CLI..."
echo "   Usage: .venv/bin/python -m runtime \"your prompt\""
echo ""

# If no args, show help
if [ $# -eq 0 ]; then
    .venv/bin/python -m runtime --help
else
    .venv/bin/python -m runtime "$@"
fi