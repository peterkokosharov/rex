# SPDX-License-Identifier: MIT
# Docker Guard - Sandboxed tool execution container
#
# Image layout mirrors the repository: /app/runtimelib/kikolib (only kikolib is copied,
# not the rest of runtimelib) and /app/guard/entrypoint.py.

FROM python:3.12-slim

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

# Copy guard code: kikolib at the same package path as in the repo + guard entrypoint
COPY runtimelib/kikolib /app/runtimelib/kikolib
COPY guard/entrypoint.py /app/guard/entrypoint.py

# Minimal package markers (the real runtimelib/__init__.py imports the whole runtime; not wanted here)
RUN touch /app/runtimelib/__init__.py /app/guard/__init__.py

# Switch to non-root user
USER guarduser

# Environment (read-only rootfs: do not try to write .pyc files)
ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1
ENV GUARD_WORKSPACE=/workspace

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "from runtimelib.kikolib import http_client; print('OK')" || exit 1

# Entrypoint
ENTRYPOINT ["python3", "-m", "guard.entrypoint"]
