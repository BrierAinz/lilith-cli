"""Smart Tool Router — semantic matching, chaining, recovery, and analytics."""

from __future__ import annotations

from .analytics import ToolAnalytics, ToolStats, ToolUsage
from .chainer import BUILTIN_CHAINS, ChainExecutor, ChainResult, ChainStep, ToolChain
from .matcher import MatchResult, ToolMatcher
from .recovery import (
    DEFAULT_NETWORK_POLICY,
    DEFAULT_RATE_LIMIT_POLICY,
    DEFAULT_TIMEOUT_POLICY,
    FallbackChain,
    RecoveryManager,
    RetryPolicy,
)
from .router import SmartToolRouter


__all__ = [
    "BUILTIN_CHAINS",
    "DEFAULT_NETWORK_POLICY",
    "DEFAULT_RATE_LIMIT_POLICY",
    "DEFAULT_TIMEOUT_POLICY",
    "ChainExecutor",
    "ChainResult",
    "ChainStep",
    "FallbackChain",
    "MatchResult",
    "RecoveryManager",
    "RetryPolicy",
    "SmartToolRouter",
    "ToolAnalytics",
    "ToolChain",
    "ToolMatcher",
    "ToolStats",
    "ToolUsage",
]
