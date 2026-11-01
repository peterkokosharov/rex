# SPDX-License-Identifier: MIT
# Docker Guard - Sandboxed tool execution container

FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -u 1000 -s /bin/bash guarduser

# Set up workspace
WORKDIR /workspace
RUN chown guarduser:guarduser /workspace

# Copy runtime code (kikolib + guard entrypoint)
COPY runtime/kikolib /app/runtime/kikolib
COPY guard/entrypoint.py /app/guard/entrypoint.py

# Create __init__.py files for imports
RUN touch /app/runtime/__init__.py /app/runtime/kikolib/__init__.py /app/guard/__init__.py

# Switch to non-root user
USER guarduser

# Environment
ENV PYTHONPATH=/app
ENV GUARD_WORKSPACE=/workspace

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import sys; sys.path.insert(0, '/app'); from runtime.kikolib import http_client; print('OK')" || exit 1

# Entrypoint
ENTRYPOINT ["python3", "-m", "guard.entrypoint"]