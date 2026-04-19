"""SCSI transport ABC + protocol constants.

The `ScsiProtocol` class (command framing, handshake, frame chunking,
boot animation) lives in `factory.py` — all five protocols are unified
there as subclasses of `DeviceProtocol`.

Platform-agnostic SCSI protocol logic. OS-specific SCSI I/O is injected
via the `ScsiTransport` ABC — Linux (SG_IO ioctl), Windows
(DeviceIoControl), macOS / BSD (pyusb USB BOT).
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod

log = logging.getLogger(__name__)


# =========================================================================
# SCSI transport ABC — OS-specific implementations injected via DI
# =========================================================================


class ScsiTransport(ABC):
    """Abstract SCSI transport — platform implementations injected via DI.

    Same pattern as UsbTransport in hid.py. Each OS provides a concrete
    implementation; the protocol logic never knows which OS it's on.
    """

    @abstractmethod
    def open(self) -> bool:
        """Open the device for SCSI I/O."""

    @abstractmethod
    def close(self) -> None:
        """Release device resources."""

    @abstractmethod
    def send_cdb(self, cdb: bytes, data: bytes) -> bool:
        """Send a SCSI CDB with data payload. Returns True on success."""

    @abstractmethod
    def read_cdb(self, cdb: bytes, length: int) -> bytes:
        """Send a SCSI CDB and read response. Returns response bytes."""


# =========================================================================
# Protocol constants — kept here as the historical home. ScsiProtocol in
# factory.py mirrors these as class attributes; they are public so
# tooling (diagnostics, legacy tests) can read them by name.
# =========================================================================

# Boot signature: device still initializing its display controller
_BOOT_SIGNATURE = b'\xa1\xa2\xa3\xa4'
_BOOT_WAIT_SECONDS = 3.0
_BOOT_MAX_RETRIES = 5
# Brief pause after init before first frame (lets controller settle)
_POST_INIT_DELAY = 0.1

# Base command for frame data chunks; chunk index goes in bits [27:24]
_FRAME_CMD_BASE = 0x101F5
_CHUNK_SIZE_LARGE = 0x10000  # 64 KiB per chunk for large displays (320x320+)
_CHUNK_SIZE_SMALL = 0xE100   # 57,600 bytes per chunk for small displays (≤320x240)
# Threshold: displays with ≤76,800 pixels (320x240=76,800) use small chunks.
# USBLCD.exe Mode 1 (240x240) and Mode 2 (320x240) use 0xE100 chunks;
# Mode 3 (320x320+) uses 0x10000 chunks.
_SMALL_DISPLAY_PIXELS = 76800

# Boot animation SCSI commands (from USBLCD.exe reverse engineering)
_ANIM_FIRST_FRAME = 0x000201F5   # Compressed first frame (CDB[8]=frame_count)
_ANIM_CAROUSEL = 0x000301F5      # Compressed carousel frame (CDB[3]=delay, CDB[8]=frame_index)
_ANIM_COMPRESS_LEVEL = 3         # zlib compression level (fast, matches USBLCD.exe)
_ANIM_FIRST_DELAY_S = 0.5       # 500ms sleep after first frame
_ANIM_FRAME_DELAY_S = 0.01      # 10ms sleep between carousel frames
_ANIM_MAX_FRAMES = 249           # C# rejects >= 250

# Boot animation supported resolutions (C# returns immediately for others)
_BOOT_ANIM_RESOLUTIONS = {
    (240, 240), (240, 320), (320, 240), (320, 320),
}
