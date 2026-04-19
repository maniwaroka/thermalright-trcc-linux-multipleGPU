"""Per-device tagged logger factory — shared by LCDDevice and LEDDevice.

Lives in its own module to avoid circular import (the package __init__
imports the Device classes, so they cannot import from __init__).
"""
from __future__ import annotations

import logging


def tagged_logger(namespace: str, label: str) -> logging.Logger:
    """Get a child logger and stamp the device label on its `.dev` attr.

    The project's custom logger subclass exposes a `.dev` field that the
    diagnostics formatter reads. Standard `logging.Logger` doesn't have
    it, so we guard with hasattr and use setattr (no type: ignore needed).
    """
    log = logging.getLogger(f'{namespace}.{label}')
    if hasattr(log, 'dev'):
        setattr(log, 'dev', label)
    return log
