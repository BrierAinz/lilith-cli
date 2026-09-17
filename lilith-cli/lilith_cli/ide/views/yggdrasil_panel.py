"""Legacy import shim for the retired Yggdrasil IDE panel.

The old bus/preset/spawn-control surface is intentionally gone. New code should
import :class:`CourtPanelMixin` from ``views.court_panel``.
"""

from __future__ import annotations

from .court_panel import CourtPanelMixin

YggdrasilPanelMixin = CourtPanelMixin

__all__ = ["YggdrasilPanelMixin"]
