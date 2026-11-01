# SPDX-License-Identifier: MIT
"""Permission policy engine for REX Runtime."""

from __future__ import annotations

from .base import (
    PermissionResult,
    PermissionRule,
    PermissionPolicy,
    PermissionDeniedError,
    PermissionAskError,
)
from .policy import DefaultPermissionPolicy
from .executor import PermissionCheckingExecutor, create_permission_executor

__all__ = [
    "PermissionResult",
    "PermissionRule",
    "PermissionPolicy",
    "PermissionDeniedError",
    "PermissionAskError",
    "DefaultPermissionPolicy",
    "PermissionCheckingExecutor",
    "create_permission_executor",
]