"""Platform detection — routes to the correct adapter implementations.

Zero imports from adapters. Returns string identifiers that composition roots
use to select the right concrete classes.
"""
from __future__ import annotations

import sys

LINUX = sys.platform.startswith('linux')
WINDOWS = sys.platform == 'win32'
MACOS = sys.platform == 'darwin'
BSD = 'bsd' in sys.platform


def platform_name() -> str:
    """Human-readable platform name."""
    if WINDOWS:
        return 'Windows'
    if MACOS:
        return 'macOS'
    if BSD:
        return 'BSD'
    return 'Linux'
