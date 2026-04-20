"""ScsiLcd — Device implementation for SCSI-protocol LCD hardware.

The physical device enumerates as USB mass-storage; we frame SCSI CDBs
as USB Bulk-Only Transport (CBW → data → CSW) over a UsbTransport.  One
class, one code path — Linux's SG_IO and Windows' DeviceIoControl are
no longer used (behavioral change vs. legacy trcc; requires raw-USB
udev access on Linux and WinUSB/libusbK on Windows).
"""
from __future__ import annotations

import binascii
import logging
import struct
import time
from typing import List, Tuple

from ...core.errors import HandshakeError, TransportError
from ...core.models import HandshakeResult, ProductInfo
from ...core.ports import Device, Platform

log = logging.getLogger(__name__)


# =========================================================================
# USB Bulk-Only Transport (BOT) constants
# =========================================================================

_CBW_SIGNATURE = 0x43425355       # 'USBC'
_CSW_SIZE = 13
_CBW_DIR_OUT = 0x00                # host → device
_CBW_DIR_IN = 0x80                 # device → host

_EP_WRITE = 0x02
_EP_READ = 0x81
_BOT_TIMEOUT_MS = 5000


# =========================================================================
# SCSI protocol constants (USBLCD.exe decompiled C#)
# =========================================================================

# Handshake / init
_BOOT_SIGNATURE = b'\xa1\xa2\xa3\xa4'
_BOOT_WAIT_S = 3.0
_BOOT_MAX_RETRIES = 5
_POST_INIT_DELAY_S = 0.1

_POLL_CMD = 0xF5
_INIT_CMD = 0x1F5
_POLL_SIZE = 0xE100

# Frame chunking
_FRAME_CMD_BASE = 0x101F5
_CHUNK_SIZE_LARGE = 0x10000
_CHUNK_SIZE_SMALL = 0xE100
_SMALL_DISPLAY_PIXELS = 76800      # ≤320×240 uses the small chunk size


# =========================================================================
# ScsiLcd
# =========================================================================


class ScsiLcd(Device):
    """SCSI LCD device over USB Bulk-Only Transport.

    connect():   detach kernel driver → poll + init handshake → return FBL
    send(data):  RGB565 frame in 16-byte-CDB chunked writes
    disconnect(): close transport
    """

    def __init__(self, info: ProductInfo, platform: Platform) -> None:
        super().__init__(info, platform)
        self._cbw_tag = 0

    # ── Device ABC ────────────────────────────────────────────────────

    def connect(self) -> HandshakeResult:
        """Open transport, perform poll + init handshake, return FBL."""
        self._transport = self._platform.open_usb(self.info.vid, self.info.pid)
        if not self._transport.open():
            raise HandshakeError(
                f"Failed to open USB transport for {self.info.key}"
            )

        # Step 1: Poll with boot-state check
        poll_cdb = self._build_cdb(_POLL_CMD, _POLL_SIZE)
        response = b""
        for attempt in range(_BOOT_MAX_RETRIES):
            response = self._scsi_read(poll_cdb, _POLL_SIZE)
            if len(response) >= 8 and response[4:8] == _BOOT_SIGNATURE:
                log.info("Device %s still booting (attempt %d/%d), waiting %.0fs",
                         self.info.key, attempt + 1, _BOOT_MAX_RETRIES, _BOOT_WAIT_S)
                time.sleep(_BOOT_WAIT_S)
            else:
                break

        fbl = response[0] if response else (self.info.fbl or 100)
        log.debug("SCSI poll byte[0] = %d (FBL)", fbl)

        # Step 2: Init
        init_cdb = self._build_cdb(_INIT_CMD, _POLL_SIZE)
        self._scsi_write(init_cdb, b"\x00" * _POLL_SIZE)

        # Step 3: let the display controller settle
        time.sleep(_POST_INIT_DELAY_S)

        result = HandshakeResult(
            resolution=self.info.native_resolution,
            model_id=fbl,
            pm_byte=fbl,
            sub_byte=0,
            fbl=fbl,
            raw_response=bytes(response[:64]),
        )
        self._handshake = result
        log.info("SCSI handshake OK: FBL=%d, resolution=%s",
                 fbl, result.resolution)
        return result

    def send(self, payload: bytes) -> bool:
        """Send one RGB565 frame, chunked by resolution class."""
        if self._transport is None or not self._transport.is_open:
            raise TransportError(
                f"ScsiLcd {self.info.key} not connected — call connect() first"
            )

        width, height = (self._handshake.resolution if self._handshake
                         else self.info.native_resolution)
        chunks = self._frame_chunks(width, height)
        total = sum(size for _, size in chunks)

        data = payload
        if len(data) < total:
            data = data + b"\x00" * (total - len(data))

        offset = 0
        for cmd, size in chunks:
            cdb = self._build_cdb(cmd, size)
            ok = self._scsi_write(cdb, data[offset:offset + size])
            if not ok:
                log.warning("SCSI frame chunk failed at offset %d", offset)
                return False
            offset += size
        return True

    def disconnect(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        self._handshake = None

    # ── SCSI framing helpers ──────────────────────────────────────────

    @staticmethod
    def _build_cdb(cmd: int, size: int) -> bytes:
        """Build the 16-byte SCSI CDB: cmd(4) + zeros(8) + size(4) + crc32(4)."""
        header_16 = struct.pack("<I", cmd) + b"\x00" * 8 + struct.pack("<I", size)
        crc = binascii.crc32(header_16) & 0xFFFFFFFF
        full = header_16 + struct.pack("<I", crc)
        return full[:16]

    @staticmethod
    def _frame_chunks(width: int, height: int) -> List[Tuple[int, int]]:
        """Compute (cmd, size) pairs for chunked frame send."""
        pixels = width * height
        chunk_size = (_CHUNK_SIZE_SMALL if pixels <= _SMALL_DISPLAY_PIXELS
                      else _CHUNK_SIZE_LARGE)
        total = pixels * 2  # RGB565 = 2 bytes per pixel
        chunks: List[Tuple[int, int]] = []
        offset = 0
        idx = 0
        while offset < total:
            size = min(chunk_size, total - offset)
            cmd = _FRAME_CMD_BASE | (idx << 24)
            chunks.append((cmd, size))
            offset += size
            idx += 1
        return chunks

    # ── USB BOT framing (CBW → data → CSW) ────────────────────────────

    def _next_tag(self) -> int:
        self._cbw_tag = (self._cbw_tag + 1) & 0xFFFFFFFF
        return self._cbw_tag

    def _build_cbw(self, cdb: bytes, data_length: int, direction: int) -> bytes:
        """Command Block Wrapper — 31 bytes, CDB padded to 16."""
        cbw = struct.pack(
            "<IIIBBB",
            _CBW_SIGNATURE,
            self._next_tag(),
            data_length,
            direction,
            0,                     # LUN
            len(cdb),
        )
        return cbw + cdb.ljust(16, b"\x00")

    def _scsi_write(self, cdb: bytes, data: bytes) -> bool:
        """SCSI CDB + data-out over USB BOT.  Returns True on CSW status 0."""
        assert self._transport is not None
        cbw = self._build_cbw(cdb, len(data), _CBW_DIR_OUT)
        try:
            self._transport.write(_EP_WRITE, cbw, _BOT_TIMEOUT_MS)
            if data:
                self._transport.write(_EP_WRITE, data, _BOT_TIMEOUT_MS)
            csw = self._transport.read(_EP_READ, _CSW_SIZE, _BOT_TIMEOUT_MS)
            if len(csw) >= _CSW_SIZE and csw[12] != 0:
                log.warning("SCSI write CSW status %d", csw[12])
                return False
            return True
        except TransportError:
            log.exception("SCSI write transfer failed")
            return False

    def _scsi_read(self, cdb: bytes, length: int) -> bytes:
        """SCSI CDB + data-in over USB BOT.  Returns data bytes, or b'' on error."""
        assert self._transport is not None
        cbw = self._build_cbw(cdb, length, _CBW_DIR_IN)
        try:
            self._transport.write(_EP_WRITE, cbw, _BOT_TIMEOUT_MS)
            data = self._transport.read(_EP_READ, length, _BOT_TIMEOUT_MS)
            csw = self._transport.read(_EP_READ, _CSW_SIZE, _BOT_TIMEOUT_MS)
            if len(csw) >= _CSW_SIZE and csw[12] != 0:
                log.warning("SCSI read CSW status %d", csw[12])
                return b""
            return data
        except TransportError:
            log.exception("SCSI read transfer failed")
            return b""
