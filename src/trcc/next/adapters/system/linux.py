"""LinuxPlatform — concrete Platform implementation for Linux.

This file owns every Linux-specific thing: sysfs walks, SG_IO ioctl,
XDG paths, udev-rule checks, autostart.  Other OSes have their own
sibling file.

Key pieces:
    LinuxPaths             — XDG + HOME resolution
    LinuxScsiTransport     — SCSI over /dev/sgN via SG_IO ioctl
    _resolve_scsi_path     — vid:pid → /dev/sg* via sysfs walk
    LinuxPlatform          — the Platform ABC wiring
"""
from __future__ import annotations

import ctypes
import fcntl
import logging
import os
from pathlib import Path
from typing import List, Optional

import usb.core
import usb.util

from ...core.errors import TransportError
from ...core.models import DeviceInfo
from ...core.ports import (
    AutostartManager,
    BulkTransport,
    Paths,
    Platform,
    ScsiTransport,
    SensorEnumerator,
)
from ...core.registry import ALL_DEVICES
from ..device.transport import PyUsbBulkTransport
from ..sensors.aggregator import build_linux_sensors
from ..sensors.gpu_detect import (
    detect_gpu_vendors,
    install_matching_gpu_extras,
)

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
# SG_IO — Linux kernel-native SCSI passthrough
# =========================================================================
#
# A single ioctl(SG_IO) bundles CDB + data phase + status, so a frame
# chunk costs one syscall on Linux (vs. 3 for userspace USB BOT).  The
# kernel also handles the mass-storage prelude (INQUIRY / TEST UNIT
# READY / Get Max LUN) that raw BOT skips — which is what stalls
# endpoints on vendor CDBs like 0xF5/0x1F5.

_SG_IO = 0x2285
_SG_DXFER_TO_DEV = -2
_SG_DXFER_FROM_DEV = -3
_SENSE_BUF_LEN = 32


class _SgIoHdr(ctypes.Structure):
    """Kernel sg_io_hdr_t — the ioctl argument for SG_IO."""

    _fields_ = [
        ('interface_id', ctypes.c_int),
        ('dxfer_direction', ctypes.c_int),
        ('cmd_len', ctypes.c_ubyte),
        ('mx_sb_len', ctypes.c_ubyte),
        ('iovec_count', ctypes.c_ushort),
        ('dxfer_len', ctypes.c_uint),
        ('dxferp', ctypes.c_void_p),
        ('cmdp', ctypes.c_void_p),
        ('sbp', ctypes.c_void_p),
        ('timeout', ctypes.c_uint),
        ('flags', ctypes.c_uint),
        ('pack_id', ctypes.c_int),
        ('usr_ptr', ctypes.c_void_p),
        ('status', ctypes.c_ubyte),
        ('masked_status', ctypes.c_ubyte),
        ('msg_status', ctypes.c_ubyte),
        ('sb_len_wr', ctypes.c_ubyte),
        ('host_status', ctypes.c_ushort),
        ('driver_status', ctypes.c_ushort),
        ('resid', ctypes.c_int),
        ('duration', ctypes.c_uint),
        ('info', ctypes.c_uint),
    ]


_SG_HDR_SIZE = ctypes.sizeof(_SgIoHdr)


def _resolve_scsi_path(vid: int, pid: int) -> Optional[str]:
    """Walk sysfs to find /dev/sgN for a given VID:PID.

    Pass 1: /sys/class/scsi_generic/sgN  (kernel `sg` module loaded)
    Pass 2: /sys/block/sdN               (sg not loaded — block fallback)

    Returns the absolute /dev path, or None if no match.
    """
    for base, name_prefix in (("/sys/class/scsi_generic", "sg"),
                              ("/sys/block", "sd")):
        base_path = Path(base)
        if not base_path.exists():
            continue
        for entry in base_path.iterdir():
            if not entry.name.startswith(name_prefix):
                continue
            sysfs_device = entry / "device"
            if not sysfs_device.exists():
                continue
            found = _walk_sysfs_for_vid_pid(sysfs_device)
            if found == (vid, pid):
                if name_prefix == "sd":
                    log.info("sg module not loaded — using block device /dev/%s",
                             entry.name)
                return f"/dev/{entry.name}"
    return None


def _walk_sysfs_for_vid_pid(start: Path) -> Optional[tuple[int, int]]:
    """Walk up sysfs parents until we find idVendor + idProduct files."""
    path = Path(os.path.realpath(start))
    for _ in range(10):
        path = path.parent
        vid_file = path / "idVendor"
        pid_file = path / "idProduct"
        if vid_file.exists() and pid_file.exists():
            try:
                return (int(vid_file.read_text().strip(), 16),
                        int(pid_file.read_text().strip(), 16))
            except (OSError, ValueError):
                return None
    return None


class LinuxScsiTransport(ScsiTransport):
    """SCSI transport over /dev/sgN using SG_IO ioctl.

    One ioctl per CDB — the kernel bundles CDB, data phase, and status.
    Buffer allocations are cached by data-length so the per-frame hot
    path does only memmoves + one ioctl (no Python-side allocation).
    """

    def __init__(self, device_path: str) -> None:
        self._path = device_path
        self._fd: Optional[int] = None
        # Cache for send_cdb: {data_len: (cdb_buf, data_buf, sense_buf, hdr, ioctl_buf)}
        self._write_bufs: dict[int, tuple] = {}

    @property
    def is_open(self) -> bool:
        return self._fd is not None

    def open(self) -> bool:
        if self._fd is not None:
            return True
        try:
            self._fd = os.open(self._path, os.O_RDWR | os.O_NONBLOCK)
            log.debug("SG_IO opened %s (fd=%d)", self._path, self._fd)
            return True
        except OSError as e:
            log.error("SG_IO open failed for %s: %s", self._path, e)
            return False

    def close(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
            self._write_bufs.clear()

    def send_cdb(self, cdb: bytes, data: bytes,
                 timeout_ms: int = 5000) -> bool:
        """SCSI CDB + data-out via single SG_IO ioctl.  True on status 0."""
        if self._fd is None:
            raise TransportError(f"LinuxScsiTransport {self._path} not open")

        bufs = self._write_bufs.get(len(data))
        if bufs is None:
            bufs = self._alloc_write_bufs(len(cdb), len(data))
            self._write_bufs[len(data)] = bufs
        cdb_buf, data_buf, _sense, hdr, ioctl_buf = bufs

        ctypes.memmove(cdb_buf, cdb, len(cdb))
        if data:
            ctypes.memmove(data_buf, data, len(data))
        hdr.timeout = timeout_ms
        ctypes.memmove(ioctl_buf, ctypes.addressof(hdr), _SG_HDR_SIZE)
        fcntl.ioctl(self._fd, _SG_IO, ioctl_buf)
        ctypes.memmove(ctypes.addressof(hdr), ioctl_buf, _SG_HDR_SIZE)

        if hdr.status != 0:
            log.warning("SG_IO send_cdb status=%d host=%d driver=%d",
                        hdr.status, hdr.host_status, hdr.driver_status)
            return False
        return True

    def read_cdb(self, cdb: bytes, length: int,
                 timeout_ms: int = 5000) -> bytes:
        """SCSI CDB + data-in via single SG_IO ioctl.  Empty bytes on error.

        Not cached — reads happen only at handshake/poll, not per frame.
        """
        if self._fd is None:
            raise TransportError(f"LinuxScsiTransport {self._path} not open")

        cdb_buf = (ctypes.c_ubyte * len(cdb)).from_buffer_copy(cdb)
        data_buf = (ctypes.c_ubyte * length)()
        sense_buf = (ctypes.c_ubyte * _SENSE_BUF_LEN)()

        hdr = _SgIoHdr()
        hdr.interface_id = ord('S')
        hdr.dxfer_direction = _SG_DXFER_FROM_DEV
        hdr.cmd_len = len(cdb)
        hdr.mx_sb_len = _SENSE_BUF_LEN
        hdr.dxfer_len = length
        hdr.dxferp = ctypes.addressof(data_buf)
        hdr.cmdp = ctypes.addressof(cdb_buf)
        hdr.sbp = ctypes.addressof(sense_buf)
        hdr.timeout = timeout_ms

        ioctl_buf = ctypes.create_string_buffer(_SG_HDR_SIZE)
        ctypes.memmove(ioctl_buf, ctypes.addressof(hdr), _SG_HDR_SIZE)
        fcntl.ioctl(self._fd, _SG_IO, ioctl_buf)
        ctypes.memmove(ctypes.addressof(hdr), ioctl_buf, _SG_HDR_SIZE)

        if hdr.status != 0:
            log.warning("SG_IO read_cdb status=%d host=%d driver=%d",
                        hdr.status, hdr.host_status, hdr.driver_status)
            return b""
        actual = length - hdr.resid
        return bytes(data_buf[:actual])

    def _alloc_write_bufs(self, cdb_len: int, data_len: int) -> tuple:
        """Build a (cdb, data, sense, hdr, ioctl) buffer set for one size class."""
        cdb_buf = (ctypes.c_ubyte * cdb_len)()
        data_buf = (ctypes.c_ubyte * data_len)()
        sense_buf = (ctypes.c_ubyte * _SENSE_BUF_LEN)()
        hdr = _SgIoHdr()
        hdr.interface_id = ord('S')
        hdr.dxfer_direction = _SG_DXFER_TO_DEV
        hdr.cmd_len = cdb_len
        hdr.mx_sb_len = _SENSE_BUF_LEN
        hdr.dxfer_len = data_len
        hdr.dxferp = ctypes.addressof(data_buf)
        hdr.cmdp = ctypes.addressof(cdb_buf)
        hdr.sbp = ctypes.addressof(sense_buf)
        ioctl_buf = ctypes.create_string_buffer(_SG_HDR_SIZE)
        return (cdb_buf, data_buf, sense_buf, hdr, ioctl_buf)


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

    # ── Transport factories ──────────────────────────────────────────

    def open_bulk(self, vid: int, pid: int,
                  serial: Optional[str] = None) -> BulkTransport:
        """Return an unopened PyUsbBulkTransport for HID/BULK/LY/LED."""
        return PyUsbBulkTransport(vid, pid, serial)

    def open_scsi(self, vid: int, pid: int,
                  serial: Optional[str] = None) -> ScsiTransport:
        """Return an unopened SG_IO-backed SCSI transport.

        Resolves vid:pid → /dev/sgN via sysfs before building the
        transport.  Raises TransportError if the device isn't present
        as a SCSI generic or sd block device.
        """
        path = _resolve_scsi_path(vid, pid)
        if path is None:
            raise TransportError(
                f"No SCSI device node found for {vid:04x}:{pid:04x} — "
                "check that the device is attached and the scsi_generic "
                "kernel module is loaded"
            )
        log.debug("LinuxPlatform.open_scsi: %04x:%04x → %s", vid, pid, path)
        return LinuxScsiTransport(path)

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
            self._sensors = build_linux_sensors()
        return self._sensors

    def autostart(self) -> AutostartManager:
        if self._autostart is None:
            self._autostart = _NoopAutostart()
        return self._autostart

    # ── Setup / permissions ──────────────────────────────────────────

    def setup(self, interactive: bool = True) -> int:
        """Run OS-specific setup.

        Currently: detects GPU vendors via PCI sysfs and installs any
        missing Python libs that match (e.g., nvidia-ml-py if NVIDIA is
        present).  udev-rules installation lands in a later phase.
        """
        vendors = detect_gpu_vendors()
        log.info("Detected GPU vendors: %s", sorted(vendors) or "none")
        return install_matching_gpu_extras(vendors, dry_run=not interactive)

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
