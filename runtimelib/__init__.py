# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia-nemotron-super
# Licensed under MIT License
"""Back-compat shim: the engine moved to ai_runtime_rex.core.

`runtimelib` and every `runtimelib.<sub>` resolve to the identical module object as
`ai_runtime_rex.core` / `ai_runtime_rex.core.<sub>`, so old import paths and isinstance checks keep working.
Remove this package once every caller imports ai_runtime_rex.core directly (plan step 10).
"""
from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import sys

_OLD = "runtimelib"
_NEW = "ai_runtime_rex.core"


class _AliasLoader(importlib.abc.Loader):
    """Load `runtimelib.x` by returning the already-imported `ai_runtime_rex.core.x` object."""

    def __init__(self, target: str) -> None:
        self._target = target

    def create_module(self, spec):
        return importlib.import_module(self._target)

    def exec_module(self, module) -> None:
        pass


class _AliasFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name != _OLD and not name.startswith(_OLD + "."):
            return None
        new_name = _NEW + name[len(_OLD):]
        return importlib.util.spec_from_loader(name, _AliasLoader(new_name))


if not any(isinstance(f, _AliasFinder) for f in sys.meta_path):
    sys.meta_path.insert(0, _AliasFinder())

# Keep the single source of the version textually here for the consistency test.
__version__ = "0.3.0"

# Bind this module to the real core package object right now.
sys.modules[__name__] = importlib.import_module(_NEW)
