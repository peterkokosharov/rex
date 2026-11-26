# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-sonnet-5-5
# Licensed under MIT License
"""ai_runtime_rex.core: the agent engine. For now an alias that re-exports ``runtimelib``.

No files are moved yet; ``runtimelib`` remains the real package. Submodules are still imported from
``runtimelib.*`` (a path alias would load every module twice under two names).
"""

import runtimelib as _impl
from runtimelib import *  # noqa: F401,F403

__doc_impl__ = _impl.__name__
