"""Renderer factory — picks QtRenderer or PilRenderer based on availability."""
from __future__ import annotations

from trcc.core.ports import Renderer


def create_renderer() -> Renderer:
    """Create the best available renderer.

    Returns QtRenderer (primary, C++ QPainter) if PySide6 is initialized,
    otherwise falls back to PilRenderer (CPU-only PIL/Pillow).
    """
    try:
        from trcc.adapters.render.qt import QtRenderer
        return QtRenderer()
    except Exception:
        from trcc.adapters.render.pil import PilRenderer
        return PilRenderer()
