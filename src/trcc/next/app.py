"""App — the hub that wires Platform + Devices + EventBus + Commands.

Holds one Platform (the OS), one dict of live Devices keyed by their
'vid:pid' string, and one EventBus.  UIs dispatch Commands through
`app.dispatch(cmd)`; nothing else touches devices directly.
"""
from __future__ import annotations

import logging
from typing import Dict, Type

from .adapters.device.hid_lcd import HidLcd
from .adapters.device.scsi_lcd import ScsiLcd
from .core.commands import Command
from .core.errors import DeviceNotFoundError
from .core.events import EventBus
from .core.models import Wire
from .core.ports import Device, Platform
from .core.registry import find_product
from .core.results import Result

log = logging.getLogger(__name__)


# =========================================================================
# App
# =========================================================================


class App:
    """Application hub.

    * `platform` — the OS (Linux/Windows/macOS/BSD), DI'd at construction.
    * `devices`  — key → Device, populated when ConnectDevice runs.
    * `events`   — EventBus for async updates to UIs.

    UIs never hold Device references directly.  They dispatch Commands
    and subscribe to events.
    """

    # Wire → Device subclass.  New wire protocol = new entry.
    _DEVICE_CLASSES: Dict[Wire, Type[Device]] = {
        Wire.SCSI: ScsiLcd,
        Wire.HID: HidLcd,
        # BULK, LY, LED land in Phase 8
    }

    def __init__(self, platform: Platform) -> None:
        self.platform = platform
        self.devices: Dict[str, Device] = {}
        self.events = EventBus()

    # ── Device lifecycle ──────────────────────────────────────────────

    def attach(self, vid: int, pid: int) -> Device:
        """Build and cache a Device for (vid, pid).  Does not connect."""
        info = find_product(vid, pid)
        if info is None:
            raise DeviceNotFoundError(
                f"Unknown product: {vid:04x}:{pid:04x}"
            )
        cls = self._DEVICE_CLASSES.get(info.wire)
        if cls is None:
            raise DeviceNotFoundError(
                f"No Device implementation for wire={info.wire.value!r}"
            )
        device = cls(info, self.platform)
        self.devices[device.key] = device
        log.debug("App.attach: %s → %s", device.key, cls.__name__)
        return device

    def get(self, key: str) -> Device:
        """Look up an attached device.  Raises if not attached."""
        device = self.devices.get(key)
        if device is None:
            raise DeviceNotFoundError(f"Not attached: {key}")
        return device

    def detach(self, key: str) -> None:
        """Disconnect and drop a device."""
        device = self.devices.pop(key, None)
        if device is not None:
            device.disconnect()

    def close(self) -> None:
        """Disconnect every attached device."""
        for key in list(self.devices):
            self.detach(key)

    # ── Command dispatch ──────────────────────────────────────────────

    def dispatch(self, cmd: Command) -> Result:
        """Execute a Command and return its Result.

        UIs should only reach the rest of the app through this method.
        """
        log.debug("dispatch: %s", type(cmd).__name__)
        return cmd.execute(self)
