"""Noop transports — fake USB at the wire, everything above is real.

These implement the real transport ABCs (ScsiTransport, UsbTransport) and
return canned bytes that mimic what a physical device would send. Every
layer above the transport — ScsiDevice, HidDeviceType2, LedHidSender,
the factory protocols — is production code.

Usage:
    transport = NoopScsiTransport(fbl=100, pm=32)
    dev = ScsiDevice("/dev/sg0", transport)
    result = dev.handshake()  # real code, fake USB
"""
from __future__ import annotations

from trcc.adapters.device.hid import UsbTransport
from trcc.adapters.device.scsi import ScsiTransport

# ═════════════════════════════════════════════════════════════════════════════
# SCSI — canned poll response, accepts writes
# ═════════════════════════════════════════════════════════════════════════════


class NoopScsiTransport(ScsiTransport):
    """Fake SCSI transport — returns canned handshake bytes, discards frames.

    The poll response (read_cdb) returns 0xE100 bytes with:
      byte[0] = FBL (resolution identifier)
      bytes[4:8] = zeros (not booting)
    This is what a real device sends after it finishes booting.
    """

    def __init__(self, fbl: int = 100, pm: int = 0) -> None:
        self._fbl = fbl
        self._pm = pm
        self._open = False
        self.frames_sent: int = 0

    def open(self) -> bool:
        self._open = True
        return True

    def close(self) -> None:
        self._open = False

    def send_cdb(self, cdb: bytes, data: bytes) -> bool:
        self.frames_sent += 1
        return True

    def read_cdb(self, cdb: bytes, length: int) -> bytes:
        """Return canned poll response: FBL at byte[0], no boot signature."""
        resp = bytearray(length)
        resp[0] = self._fbl
        return bytes(resp)


# ═════════════════════════════════════════════════════════════════════════════
# USB (HID/LED/Bulk) — canned handshake response, accepts writes
# ═════════════════════════════════════════════════════════════════════════════


class NoopUsbTransport(UsbTransport):
    """Fake USB transport — returns canned HID/LED handshake, discards frames.

    Configure with the handshake response bytes to return on first read.
    Subsequent reads return zeros (frame acks).
    """

    def __init__(self, handshake_response: bytes) -> None:
        self._resp = handshake_response
        self._open = False
        self._first_read = True
        self.writes: int = 0

    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    def write(self, endpoint: int, data: bytes, timeout: int = 5000) -> int:
        self.writes += 1
        return len(data)

    def read(self, endpoint: int, length: int, timeout: int = 5000) -> bytes:
        if self._first_read:
            self._first_read = False
            return self._resp[:length]
        return b'\x00' * length


# ═════════════════════════════════════════════════════════════════════════════
# Response builders — construct valid handshake bytes for each protocol
# ═════════════════════════════════════════════════════════════════════════════


def build_hid_type2_response(pm: int, sub: int = 0) -> bytes:
    """Build a valid Type 2 HID handshake response.

    Layout (512 bytes):
      [0:4]   = DA DB DC DD (magic)
      [4]     = sub byte
      [5]     = pm byte
      [12]    = 0x01 (command ack)
    """
    resp = bytearray(512)
    resp[0:4] = bytes([0xDA, 0xDB, 0xDC, 0xDD])
    resp[4] = sub
    resp[5] = pm
    resp[12] = 0x01
    return bytes(resp)


def build_hid_type3_response(fbl: int = 100) -> bytes:
    """Build a valid Type 3 HID handshake response.

    Layout (1024 bytes):
      [0]     = fbl + 1 (0x65 for fbl=100, 0x66 for fbl=101)
      [10:14] = serial bytes
    """
    resp = bytearray(1024)
    resp[0] = fbl + 1  # 0x65 = 100, 0x66 = 101
    resp[10:14] = b'\xDE\xAD\xBE\xEF'  # fake serial
    return bytes(resp)


def build_led_response(pm: int, sub: int = 0) -> bytes:
    """Build a valid LED handshake response.

    Layout (64 bytes):
      [0:4]   = DA DB DC DD (magic, same as HID Type 2)
      [4]     = sub byte
      [5]     = pm byte
      [12]    = 0x01 (command ack)
    """
    resp = bytearray(64)
    resp[0:4] = bytes([0xDA, 0xDB, 0xDC, 0xDD])
    resp[4] = sub
    resp[5] = pm
    resp[12] = 0x01
    return bytes(resp)
