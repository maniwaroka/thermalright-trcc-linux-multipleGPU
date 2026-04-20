"""LinuxPlatform — concrete Platform implementation for Linux.

Phase 2 scope: USB discovery (scan_devices) and USB access (open_usb).
Sensors, autostart, and the full setup wizard land in later phases with
stub implementations here that satisfy the ABC but do nothing useful.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import List, Optional

import usb.core
import usb.util

from ...core.models import DeviceInfo
from ...core.ports import (
    AutostartManager,
    Paths,
    Platform,
    SensorEnumerator,
    UsbTransport,
)
from ...core.registry import ALL_DEVICES
from ..device.transport import PyUsbTransport

log = logging.getLogger(__name__)


# =========================================================================
# LinuxPaths — XDG-style locations
# =========================================================================


class LinuxPaths(Paths):
    """XDG + HOME locations for user data."""

    def __init__(self) -> None:
        home = Path(os.path.expanduser("~"))
        self._root = home / ".trcc"
        self._user_content = home / ".trcc-user"

    def config_dir(self) -> Path:
        return self._root

    def data_dir(self) -> Path:
        return self._root / "data"

    def user_content_dir(self) -> Path:
        return self._user_content

    def log_file(self) -> Path:
        return self._root / "trcc.log"


# =========================================================================
# Stubs for non-Phase-2 ports (satisfy ABC; raise or return defaults)
# =========================================================================


class _NoopSensors(SensorEnumerator):
    """Placeholder sensor enumerator.  Real impl lands in Phase 5."""

    def discover(self) -> List:
        return []

    def read_all(self) -> dict[str, float]:
        return {}

    def read_one(self, sensor_id: str) -> Optional[float]:
        return None

    def start_polling(self, interval_s: float = 2.0) -> None:
        pass

    def stop_polling(self) -> None:
        pass


class _NoopAutostart(AutostartManager):
    """Placeholder autostart manager.  Real impl lands in Phase 12."""

    def is_enabled(self) -> bool:
        return False

    def enable(self) -> None:
        pass

    def disable(self) -> None:
        pass

    def refresh(self) -> None:
        pass


# =========================================================================
# LinuxPlatform
# =========================================================================


class LinuxPlatform(Platform):
    """Linux implementation of Platform.

    USB access via pyusb (libusb).  Udev rules installed by setup() give
    non-root users access to the devices listed in the product registry.
    """

    def __init__(self) -> None:
        self._paths = LinuxPaths()
        self._sensors: Optional[SensorEnumerator] = None
        self._autostart: Optional[AutostartManager] = None

    # ── USB I/O ───────────────────────────────────────────────────────

    def open_usb(self, vid: int, pid: int,
                 serial: Optional[str] = None) -> UsbTransport:
        """Return an unopened PyUsbTransport for the given device."""
        return PyUsbTransport(vid, pid, serial)

    def scan_devices(self) -> List[DeviceInfo]:
        """Walk ALL_DEVICES and return a DeviceInfo for each present VID/PID.

        No kernel-subsystem filtering — we ask pyusb whether the device
        physically enumerated and let the Device subclass handle any
        per-OS driver detach on connect().
        """
        found: List[DeviceInfo] = []
        for (vid, pid) in ALL_DEVICES:
            for dev in (usb.core.find(find_all=True, idVendor=vid, idProduct=pid) or []):
                serial_idx = getattr(dev, 'iSerialNumber', 0)
                serial: str = ""
                try:
                    if serial_idx:
                        serial = usb.util.get_string(dev, serial_idx) or ""
                except Exception:
                    serial = ""
                found.append(DeviceInfo(vid=vid, pid=pid, serial=serial or None))
        log.debug("scan_devices: %d device(s) found", len(found))
        return found

    # ── Filesystem ────────────────────────────────────────────────────

    def paths(self) -> Paths:
        return self._paths

    # ── Sensors / Autostart (stubs; real impls later) ─────────────────

    def sensors(self) -> SensorEnumerator:
        if self._sensors is None:
            self._sensors = _NoopSensors()
        return self._sensors

    def autostart(self) -> AutostartManager:
        if self._autostart is None:
            self._autostart = _NoopAutostart()
        return self._autostart

    # ── Setup / permissions ──────────────────────────────────────────

    def setup(self, interactive: bool = True) -> int:
        """Run OS-specific setup — full impl lands in Phase 12."""
        log.warning("LinuxPlatform.setup: not yet implemented (Phase 12)")
        return 0

    def check_permissions(self) -> List[str]:
        """Return user-facing warnings if udev rules are missing, etc."""
        warnings: List[str] = []
        # Quick check: is the trcc udev rules file present?
        if not Path("/etc/udev/rules.d/99-trcc-lcd.rules").exists():
            warnings.append(
                "udev rules not installed — device access may require root. "
                "Run 'trcc setup' to install them."
            )
        return warnings

    # ── OS identity ───────────────────────────────────────────────────

    def distro_name(self) -> str:
        """Parse /etc/os-release for the pretty name."""
        path = Path("/etc/os-release")
        if not path.exists():
            return "Linux"
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
        except Exception:
            pass
        return "Linux"

    def install_method(self) -> str:
        """Rough heuristic: PyInstaller bundle > pip > source."""
        import sys
        if getattr(sys, 'frozen', False):
            return "pyinstaller"
        try:
            import shutil
            if shutil.which("trcc"):
                return "pip"
        except Exception:
            pass
        return "source"
