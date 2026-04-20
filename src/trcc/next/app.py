"""App — the hub that wires Platform + Devices + EventBus + Commands.

Filled in during Phase 4 (see PHASES.md).  Phase 1 provides only the
shape needed for Command type annotations to resolve.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict

from .core.events import EventBus

if TYPE_CHECKING:
    from .core.ports import Device, Platform


class App:
    """Application hub.  Concrete behavior lands in Phase 4."""

    def __init__(self) -> None:
        self.platform: "Platform | None" = None
        self.devices: Dict[str, "Device"] = {}
        self.events = EventBus()
