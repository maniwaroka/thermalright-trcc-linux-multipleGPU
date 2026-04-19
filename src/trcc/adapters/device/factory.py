"""
Device Protocol Factory — unified API for SCSI, HID LCD, and HID LED devices.

Observer pattern: DeviceProtocol ABC defines the contract. ScsiProtocol,
HidProtocol, and LedProtocol are separate implementations with identical API.
Observers register callbacks for send_complete, error, and state changes.

The factory creates the right protocol class based on device PID/implementation.

Usage::

    from trcc.device_factory import DeviceProtocolFactory

    protocol = DeviceProtocolFactory.get_protocol(device_info)
    protocol.on_send_complete = lambda ok: print(f"sent: {ok}")
    protocol.on_error = lambda msg: print(f"err: {msg}")
    protocol.send_data(rgb565_data, width, height)      # LCD devices
    # LED: LedProtocol.send_data(colors, is_on, True, 100)
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar, Dict, List, Optional, Tuple

from trcc.core.models import (
    DEVICE_TYPE_NAMES,
    PROTOCOL_NAMES,
    HandshakeResult,
)

log = logging.getLogger(__name__)


# USB errno constants.
_ERRNO_EACCES = 13  # Permission denied — udev rules missing.
_ERRNO_EBUSY = 16   # Device claimed by another process (e.g. GUI).


def _has_usb_errno(exc: Exception, errno_val: int) -> bool:
    """Check if exception chain contains a USB error with the given errno."""
    cur: Optional[BaseException] = exc
    while cur is not None:
        if getattr(cur, "errno", None) == errno_val:
            return True
        cur = cur.__cause__
    return False


def _permission_denied_hint() -> str:
    """Platform-aware hint for USB permission denied errors."""
    from trcc.core.platform import LINUX, MACOS
    if LINUX:
        return "run 'trcc setup-udev' to configure USB device permissions"
    if MACOS:
        return "try running with sudo, or check System Settings > Privacy & Security"
    return "ensure you have permission to access USB devices"


# =========================================================================
# DeviceProtocol ABC — the contract both SCSI and HID implement
# =========================================================================

class DeviceProtocol(ABC):
    """Abstract protocol interface for LCD device communication.

    Both ScsiProtocol and HidProtocol implement this identical API.
    The app codes against DeviceProtocol, never against a specific backend.

    Observer callbacks:
        on_send_complete(success: bool) — fired after each send attempt
        on_error(message: str) — fired on any protocol error
        on_state_changed(key: str, value) — fired on state transitions
    """

    def __init__(self):
        # Observer callbacks
        self.on_send_complete: Optional[Callable[[bool], None]] = None
        self.on_error: Optional[Callable[[str], None]] = None
        self.on_state_changed: Optional[Callable[[str, object], None]] = None
        # Handshake state — common to all protocols
        self._handshake_result: Optional[HandshakeResult] = None
        self._last_error: Optional[Exception] = None

    @abstractmethod
    def send_data(self, image_data: bytes, width: int, height: int) -> bool:
        """Send image data to the LCD device.

        Args:
            image_data: Pixel bytes (RGB565 for SCSI, JPEG for HID).
            width: Image width in pixels.
            height: Image height in pixels.

        Returns:
            True if the send succeeded.
        """

    @abstractmethod
    def close(self) -> None:
        """Release resources (USB transport, SCSI state, etc.)."""

    @abstractmethod
    def get_info(self) -> 'ProtocolInfo':
        """Get protocol/backend info for GUI display."""

    @property
    @abstractmethod
    def protocol_name(self) -> str:
        """Protocol identifier: 'scsi' or 'hid'."""

    @property
    @abstractmethod
    def is_available(self) -> bool:
        """Whether the required backend (sg_raw / pyusb / hidapi) is installed."""

    def handshake(self) -> Optional[HandshakeResult]:
        """Template Method: perform handshake, cache result, handle errors.

        Subclasses implement _do_handshake() with protocol-specific logic.
        """
        try:
            result = self._do_handshake()
            self._handshake_result = result
            if result:
                self._cache_handshake(result)
            return result
        except Exception as e:
            if _has_usb_errno(e, _ERRNO_EACCES):
                log.warning(
                    "%s permission denied — %s",
                    self._handshake_label,
                    _permission_denied_hint())
            elif _has_usb_errno(e, _ERRNO_EBUSY):
                log.warning("%s in use by another process",
                            self._handshake_label)
            else:
                log.exception("%s handshake failed", self._handshake_label)
            self._last_error = e
            self._notify_error(f"{self._handshake_label} handshake failed: {e}")
            return None

    @abstractmethod
    def _do_handshake(self) -> Optional[HandshakeResult]:
        """Protocol-specific handshake logic. Called by handshake()."""

    @property
    def _handshake_label(self) -> str:
        """Human-readable label for error messages (e.g. 'HID 0416:5302')."""
        return self.protocol_name

    @property
    def handshake_info(self) -> Optional[HandshakeResult]:
        """Cached handshake result (None if not yet handshaked)."""
        return self._handshake_result

    @property
    def last_error(self) -> Optional[Exception]:
        """Last exception from handshake."""
        return self._last_error

    def _cache_handshake(self, result: HandshakeResult) -> None:
        """Save handshake result so `trcc report` can read it while GUI runs."""
        try:
            from trcc.conf import save_last_handshake
            save_last_handshake({
                "protocol": self.protocol_name,
                "model_id": result.model_id,
                "resolution": list(result.resolution) if result.resolution else None,
                "serial": result.serial,
                "raw": result.raw_response.hex() if result.raw_response else "",
            })
        except Exception:
            log.debug("Failed to cache handshake result", exc_info=True)

    @property
    def is_led(self) -> bool:
        """Whether this protocol is for LED control (not LCD)."""
        return False

    def _notify_send_complete(self, success: bool):
        """Notify observers of send result."""
        if self.on_send_complete:
            self.on_send_complete(success)

    def _notify_error(self, message: str):
        """Notify observers of an error."""
        if self.on_error:
            self.on_error(message)

    def _notify_state_changed(self, key: str, value: object):
        """Notify observers of a state change."""
        if self.on_state_changed:
            self.on_state_changed(key, value)

    def _guarded_send(self, label: str, fn: Callable[[], bool]) -> bool:
        """Execute a send operation with error handling and observer notification."""
        try:
            if (success := fn()):
                log.debug("Frame sent: %s", label)
            else:
                log.debug("Frame send returned False: %s", label)
            self._notify_send_complete(success)
            return success
        except Exception as e:
            log.warning("Frame send failed (%s): %s — device may be disconnected",
                        label, e)
            self._notify_error(f"{label} send failed: {e}")
            self._notify_send_complete(False)
            return False

    @staticmethod
    def _build_usb_protocol_info(
        protocol: str, device_type: int, protocol_display: str,
        device_type_display: str, transport_open: bool,
        *, pyusb_only: bool = False,
    ) -> 'ProtocolInfo':
        """Build ProtocolInfo for USB-transport protocols (HID, LED, Bulk)."""
        backends = DeviceProtocolFactory._get_hid_backends()
        if pyusb_only:
            active = "pyusb" if backends["pyusb"] else "none"
        elif backends["pyusb"]:
            active = "pyusb"
        elif backends["hidapi"]:
            active = "hidapi"
        else:
            active = "none"
        backends["sg_raw"] = False
        return ProtocolInfo(
            protocol=protocol, device_type=device_type,
            protocol_display=protocol_display,
            device_type_display=device_type_display,
            active_backend=active, backends=backends,
            transport_open=transport_open,
        )


# =========================================================================
# UsbProtocol — shared USB transport lifecycle for HID + LED
# =========================================================================

class UsbProtocol(DeviceProtocol):
    """Base for USB-transport protocols (HID LCD, LED).

    Manages lazy transport lifecycle: open on first use, close on cleanup.
    Subclasses implement protocol-specific handshake, send, and info.
    """

    def __init__(self, vid: int, pid: int):
        super().__init__()
        self._vid = vid
        self._pid = pid
        self._transport = None

    def _ensure_transport(self) -> None:
        """Lazily open USB transport on first use."""
        if self._transport is None:
            log.debug("Opening %s transport: %04X:%04X",
                      self.protocol_name, self._vid, self._pid)
            self._transport = DeviceProtocolFactory.create_usb_transport(
                self._vid, self._pid)
            self._transport.open()
            self._notify_state_changed("transport_open", True)

    def _close_transport(self) -> None:
        """Close USB transport and notify observers."""
        if self._transport is not None:
            try:
                self._transport.close()
            except Exception:
                pass
            self._transport = None
            self._notify_state_changed("transport_open", False)

    def close(self) -> None:
        self._close_transport()

    @property
    def _handshake_label(self) -> str:
        return f"{self.protocol_name.upper()} {self._vid:04X}:{self._pid:04X}"

    @property
    def is_available(self) -> bool:
        backends = DeviceProtocolFactory._get_hid_backends()
        return backends["pyusb"] or backends["hidapi"]


# =========================================================================
# ScsiProtocol — SCSI command framing + transport lifecycle (merged).
# Absorbs the former adapters/device/scsi.py::ScsiDevice class so SCSI
# lives as one unified protocol, parallel to HID/Bulk/Ly/LED.
# =========================================================================

import binascii  # noqa: E402
import struct  # noqa: E402
import time  # noqa: E402
import zlib  # noqa: E402
from typing import TYPE_CHECKING  # noqa: E402

if TYPE_CHECKING:
    from trcc.adapters.device.scsi import ScsiTransport


class ScsiProtocol(DeviceProtocol):
    """LCD communication via SCSI protocol — transport-agnostic.

    Takes (path, vid, pid), lazily creates transport via the
    Platform-injected factory, then owns the full SCSI protocol:
    handshake (poll + init), frame chunking with CRC32 headers, and
    optional boot-animation upload.
    """

    # --- Protocol constants (mirrored from adapters/device/scsi.py) ---
    _BOOT_SIGNATURE = b'\xa1\xa2\xa3\xa4'
    _BOOT_WAIT_SECONDS = 3.0
    _BOOT_MAX_RETRIES = 5
    _POST_INIT_DELAY = 0.1
    _FRAME_CMD_BASE = 0x101F5
    _CHUNK_SIZE_LARGE = 0x10000
    _CHUNK_SIZE_SMALL = 0xE100
    _SMALL_DISPLAY_PIXELS = 76800
    _ANIM_FIRST_FRAME = 0x000201F5
    _ANIM_CAROUSEL = 0x000301F5
    _ANIM_COMPRESS_LEVEL = 3
    _ANIM_FIRST_DELAY_S = 0.5
    _ANIM_FRAME_DELAY_S = 0.01
    _ANIM_MAX_FRAMES = 249
    _BOOT_ANIM_RESOLUTIONS = {(240, 240), (240, 320), (320, 240), (320, 320)}

    def __init__(self, path: str, vid: int, pid: int,
                 transport: 'ScsiTransport | None' = None):
        super().__init__()
        self._path = path
        self._vid = vid
        self._pid = pid
        # `transport` may be pre-injected (tests) or left None for lazy
        # platform-backed creation on first use.
        self._transport = transport
        self.width = 0
        self.height = 0

    # --- Pure helpers (no transport needed) ---

    @staticmethod
    def _get_frame_chunks(width: int, height: int) -> list:
        pixels = width * height
        chunk_size = (ScsiProtocol._CHUNK_SIZE_SMALL
                      if pixels <= ScsiProtocol._SMALL_DISPLAY_PIXELS
                      else ScsiProtocol._CHUNK_SIZE_LARGE)
        total = pixels * 2
        chunks = []
        offset = 0
        idx = 0
        while offset < total:
            size = min(chunk_size, total - offset)
            cmd = ScsiProtocol._FRAME_CMD_BASE | (idx << 24)
            chunks.append((cmd, size))
            offset += size
            idx += 1
        return chunks

    @staticmethod
    def _crc32(data: bytes) -> int:
        return binascii.crc32(data) & 0xFFFFFFFF

    @staticmethod
    def _build_header(cmd: int, size: int) -> bytes:
        """20-byte SCSI header: cmd(4) + zeros(8) + size(4) + crc32(4)."""
        header_16 = struct.pack('<I', cmd) + b'\x00' * 8 + struct.pack('<I', size)
        crc = ScsiProtocol._crc32(header_16)
        return header_16 + struct.pack('<I', crc)

    @staticmethod
    def _build_anim_header(cmd: int, word2: int, compressed_size: int) -> bytes:
        """20-byte CDB for compressed animation commands (no CRC)."""
        return struct.pack('<IIIII', cmd, 0, word2, compressed_size, 0)

    @staticmethod
    def send_frame_via_transport(transport, image_data: bytes,
                                 width: int, height: int) -> bool:
        """Send one RGB565 frame via any ScsiTransport. OS-agnostic."""
        chunks = ScsiProtocol._get_frame_chunks(width, height)
        total_size = sum(size for _, size in chunks)
        if len(image_data) < total_size:
            image_data += b'\x00' * (total_size - len(image_data))
        offset = 0
        for cmd, size in chunks:
            header = ScsiProtocol._build_header(cmd, size)
            ok = transport.send_cdb(header[:16], image_data[offset:offset + size])
            if not ok:
                return False
            offset += size
        return True

    # --- Transport lifecycle ---

    def _ensure_transport(self) -> None:
        """Lazily create SCSI transport on first use."""
        if self._transport is None:
            fn = DeviceProtocolFactory._scsi_transport_fn
            if fn is None:
                log.error("SCSI transport factory not injected")
                return
            log.debug("Opening SCSI transport: %s", self._path)
            self._transport = fn(self._path, self._vid, self._pid)
            self._transport.open()
            self._notify_state_changed("transport_open", True)

    # --- Transport-backed I/O (delegate to transport) ---

    def _scsi_read(self, cdb: bytes, length: int) -> bytes:
        assert self._transport is not None, "SCSI transport not initialized"
        return self._transport.read_cdb(cdb, length)

    def _scsi_write(self, header: bytes, data: bytes) -> bool:
        assert self._transport is not None, "SCSI transport not initialized"
        return self._transport.send_cdb(header[:16], data)

    # --- Protocol sequences ---

    def _init_device(self) -> tuple[int, bytes]:
        """Poll + init handshake (must be called before first frame send)."""
        poll_header = ScsiProtocol._build_header(0xF5, 0xE100)
        response = b''
        for attempt in range(ScsiProtocol._BOOT_MAX_RETRIES):
            response = self._scsi_read(poll_header[:16], 0xE100)
            if (len(response) >= 8
                    and response[4:8] == ScsiProtocol._BOOT_SIGNATURE):
                log.info("Device %s still booting (attempt %d/%d), waiting %.0fs...",
                         self._path, attempt + 1,
                         ScsiProtocol._BOOT_MAX_RETRIES,
                         ScsiProtocol._BOOT_WAIT_SECONDS)
                time.sleep(ScsiProtocol._BOOT_WAIT_SECONDS)
            else:
                break

        if response:
            fbl = response[0]
            log.debug("SCSI poll byte[0] = %d (FBL)", fbl)
        else:
            fbl = self._fbl_from_registry()
            log.warning("SCSI poll returned empty on %s — using registry FBL %d",
                        self._path, fbl)

        init_header = ScsiProtocol._build_header(0x1F5, 0xE100)
        self._scsi_write(init_header, b'\x00' * 0xE100)
        time.sleep(ScsiProtocol._POST_INIT_DELAY)
        return fbl, response[:64]

    def _fbl_from_registry(self) -> int:
        from trcc.core.models import SCSI_DEVICES
        entry = SCSI_DEVICES.get((self._vid, self._pid))
        if entry is not None:
            return entry.fbl
        log.warning("Device %04X:%04X not in SCSI registry, defaulting to FBL 100",
                    self._vid, self._pid)
        return 100

    def _send_frame_data(self, rgb565_data: bytes) -> None:
        chunks = ScsiProtocol._get_frame_chunks(self.width, self.height)
        total_size = sum(size for _, size in chunks)
        if len(rgb565_data) < total_size:
            rgb565_data += b'\x00' * (total_size - len(rgb565_data))
        offset = 0
        for cmd, size in chunks:
            header = ScsiProtocol._build_header(cmd, size)
            self._scsi_write(header, rgb565_data[offset:offset + size])
            offset += size

    def _send_boot_animation(self, frames: list[bytes],
                             delays: list[int]) -> bool:
        if (self.width, self.height) not in ScsiProtocol._BOOT_ANIM_RESOLUTIONS:
            log.warning("Boot animation not supported for %dx%d",
                        self.width, self.height)
            return False
        n = len(frames)
        if n == 0 or n >= ScsiProtocol._ANIM_MAX_FRAMES:
            log.warning("Boot animation frame count %d out of range (1-%d)",
                        n, ScsiProtocol._ANIM_MAX_FRAMES - 1)
            return False

        compressed = zlib.compress(frames[0], ScsiProtocol._ANIM_COMPRESS_LEVEL)
        header = ScsiProtocol._build_anim_header(
            ScsiProtocol._ANIM_FIRST_FRAME, n, len(compressed))
        if not self._scsi_write(header, compressed):
            log.error("Boot animation: failed to send first frame")
            return False
        log.info("Boot animation: sent first frame (%d bytes compressed, %d frames total)",
                 len(compressed), n)
        time.sleep(ScsiProtocol._ANIM_FIRST_DELAY_S)

        for i in range(n):
            compressed = zlib.compress(frames[i], ScsiProtocol._ANIM_COMPRESS_LEVEL)
            delay_raw = delays[i] if i < len(delays) else 10
            delay_byte = min(delay_raw * 10, 250) & 0xFF
            cmd = ScsiProtocol._ANIM_CAROUSEL | (delay_byte << 24)
            header = ScsiProtocol._build_anim_header(cmd, i, len(compressed))
            if not self._scsi_write(header, compressed):
                log.error("Boot animation: failed to send frame %d", i)
                return False
            time.sleep(ScsiProtocol._ANIM_FRAME_DELAY_S)

        log.info("Boot animation: all %d frames sent successfully", n)
        return True

    # --- DeviceProtocol interface ---

    def _do_handshake(self) -> Optional[HandshakeResult]:
        from trcc.core.models import fbl_to_resolution
        self._ensure_transport()
        if self._transport is None:
            return None
        fbl, raw = self._init_device()
        resolution = fbl_to_resolution(fbl)
        self.width, self.height = resolution
        log.info("SCSI handshake OK: FBL=%d, resolution=%s", fbl, resolution)
        return HandshakeResult(
            resolution=resolution, model_id=fbl,
            pm_byte=fbl, sub_byte=0,
            raw_response=raw,
        )

    def send_data(self, image_data: bytes, width: int, height: int) -> bool:
        self._ensure_transport()
        if self._transport is None:
            return False
        return self._guarded_send(
            "SCSI",
            lambda: ScsiProtocol.send_frame_via_transport(
                self._transport, image_data, width, height),
        )

    def send_frame(self, rgb565_data: bytes) -> bool:
        """Send one RGB565 frame using the handshake-stored resolution.

        Called by `Device.send_frame_data` on the Trcc side; public for
        legacy/test callers that used ScsiDevice.send_frame directly.
        """
        if self._handshake_result is None:
            self.handshake()
        self._send_frame_data(rgb565_data)
        return True

    def send_boot_animation(self, frames: list[bytes],
                            delays: list[int]) -> bool:
        if self._handshake_result is None:
            self.handshake()
        return self._send_boot_animation(frames, delays)

    def close(self) -> None:
        if self._transport is not None:
            try:
                self._transport.close()
            except Exception:
                pass
            self._transport = None
            self._notify_state_changed("transport_open", False)
        # Reset handshake so re-open re-handshakes
        self._handshake_result = None

    def get_info(self) -> 'ProtocolInfo':
        backend = type(self._transport).__name__ if self._transport else "none"
        return ProtocolInfo(
            protocol="scsi",
            device_type=1,
            protocol_display=f"SCSI ({backend})",
            device_type_display="SCSI RGB565",
            active_backend=backend,
            backends={backend: True},
        )

    @property
    def protocol_name(self) -> str:
        return "scsi"

    @property
    def is_available(self) -> bool:
        return self._transport is not None

    def __repr__(self) -> str:
        return f"ScsiProtocol(transport={type(self._transport).__name__})"


# =========================================================================
# HidProtocol — HID/USB bulk implementation
# =========================================================================

class HidProtocol(UsbProtocol):
    """LCD communication via HID USB bulk protocol (pyusb or hidapi).

    Wraps hid_device.py. Transport opens lazily on first send.
    Prefers pyusb, falls back to hidapi.
    """

    def __init__(self, vid: int, pid: int, device_type: int):
        super().__init__(vid, pid)
        self._device_type = device_type

    def _do_handshake(self) -> Optional[HandshakeResult]:
        """Open HID transport and perform type-specific handshake."""
        self._ensure_transport()
        assert self._transport is not None

        from .hid import HidDeviceType2, HidDeviceType3
        if self._device_type == 2:
            handler = HidDeviceType2(self._transport)
        elif self._device_type == 3:
            handler = HidDeviceType3(self._transport)
        else:
            log.warning("Unknown HID device type: %d", self._device_type)
            return None

        if (result := handler.handshake()):
            log.info("HID handshake OK: PM=%s, FBL=%s, resolution=%s",
                     result.mode_byte_1, result.fbl, result.resolution)
        else:
            log.warning("HID handshake returned None")
        self._notify_state_changed("handshake_complete", True)
        return result

    @property
    def _handshake_label(self) -> str:
        return f"HID {self._vid:04X}:{self._pid:04X} type {self._device_type}"

    def send_data(self, image_data: bytes, width: int, height: int) -> bool:
        def _do_send() -> bool:
            from .hid import HidDeviceManager
            self._ensure_transport()
            assert self._transport is not None
            return HidDeviceManager.send_data(
                self._transport, image_data, self._device_type
            )
        return self._guarded_send("HID", _do_send)

    def get_info(self) -> 'ProtocolInfo':
        return self._build_usb_protocol_info(
            "hid", self._device_type, "HID (USB bulk)",
            DEVICE_TYPE_NAMES.get(self._device_type, f"Type {self._device_type}"),
            self._transport is not None and getattr(self._transport, 'is_open', False),
        )

    @property
    def protocol_name(self) -> str:
        return "hid"

    def __repr__(self) -> str:
        return (
            f"HidProtocol(vid=0x{self._vid:04x}, pid=0x{self._pid:04x}, "
            f"type={self._device_type})"
        )


# =========================================================================
# LedProtocol — HID LED RGB controller
# =========================================================================

class LedProtocol(UsbProtocol):
    """LED device communication via HID 64-byte reports (FormLED equivalent).

    Unlike HidProtocol (LCD images), LedProtocol sends LED color arrays
    for RGB LED effects. Uses the same UsbTransport as HidProtocol.
    """

    def __init__(self, vid: int, pid: int):
        super().__init__(vid, pid)
        self._sender = None

    def send_data(
        self,
        led_colors: List[Tuple[int, int, int]],
        is_on: Optional[List[bool]] = None,
        global_on: bool = True,
        brightness: int = 100,
    ) -> bool:
        """Send LED color data to the device."""
        def _do_send() -> bool:
            self._ensure_transport()
            assert self._transport is not None

            if self._sender is None:
                from .led import LedHidSender
                self._sender = LedHidSender(self._transport)

            from .led import LedPacketBuilder, remap_led_colors

            hr = self._handshake_result
            style = getattr(hr, 'style', None) if hr else None
            style_sub = getattr(hr, 'style_sub', 0) if hr else 0
            remapped = remap_led_colors(
                led_colors, style.style_id, style_sub,
            ) if style else led_colors

            packet = LedPacketBuilder.build_led_packet(
                remapped, is_on, global_on, brightness
            )

            try:
                return self._sender.send_data(packet)
            except Exception:
                log.warning("LED send failed, reconnecting and retrying")
                self.close()
                self._handshake_result = None
                self.handshake()
                return self._sender.send_data(packet)

        return self._guarded_send("LED", _do_send)

    def _do_handshake(self) -> Optional[HandshakeResult]:
        """LED handshake — cached after first call (firmware ignores re-handshakes)."""
        if self._handshake_result is not None:
            return self._handshake_result

        self._ensure_transport()
        assert self._transport is not None

        if self._sender is None:
            from .led import LedHidSender
            self._sender = LedHidSender(self._transport)

        result = self._sender.handshake()
        self._notify_state_changed("handshake_complete", True)
        return result

    def close(self) -> None:
        self._close_transport()
        self._sender = None

    def get_info(self) -> 'ProtocolInfo':
        return self._build_usb_protocol_info(
            "led", 1, "LED (HID 64-byte)", "RGB LED Controller",
            self._transport is not None and getattr(self._transport, 'is_open', False),
        )

    @property
    def protocol_name(self) -> str:
        return "led"

    @property
    def is_led(self) -> bool:
        return True

    def __repr__(self) -> str:
        return f"LedProtocol(vid=0x{self._vid:04x}, pid=0x{self._pid:04x})"


# =========================================================================
# BulkProtocol — raw USB bulk (USBLCDNew) implementation
# =========================================================================

class _BulkLikeProtocol(DeviceProtocol):
    """Shared base for BulkProtocol + LyProtocol (identical lifecycle)."""

    _label: str = ""  # "Bulk" or "LY" — set by subclass

    def __init__(self, vid: int, pid: int):
        super().__init__()
        self._vid = vid
        self._pid = pid
        self._device: Optional[Any] = None

    @staticmethod
    def _make_device(vid: int, pid: int) -> Any:
        raise NotImplementedError

    def _ensure_device(self) -> None:
        if self._device is None:
            log.debug("%s: creating device %04X:%04X", self._label, self._vid, self._pid)
            self._device = self._make_device(self._vid, self._pid)
            assert self._device is not None
            log.debug("%s: starting handshake", self._label)
            result = self._device.handshake()
            self._handshake_result = result
            if result.resolution:
                self._notify_state_changed("handshake_complete", True)
                log.info("%s handshake OK: PM=%d, resolution=%s",
                         self._label, result.model_id, result.resolution)
            else:
                log.warning("%s handshake: no resolution detected (result=%s)",
                            self._label, result)

    def _do_handshake(self) -> Optional[HandshakeResult]:
        self._ensure_device()
        return self._handshake_result

    @property
    def _handshake_label(self) -> str:
        return f"{self._label} {self._vid:04X}:{self._pid:04X}"

    def send_data(self, image_data: bytes, width: int, height: int) -> bool:
        def _do_send() -> bool:
            self._ensure_device()
            assert self._device is not None
            return self._device.send_frame(image_data)
        return self._guarded_send(self._label, _do_send)

    def close(self) -> None:
        if self._device is not None:
            try:
                self._device.close()
            except Exception:
                pass
            self._device = None

    @property
    def is_available(self) -> bool:
        backends = DeviceProtocolFactory._get_hid_backends()
        return backends["pyusb"]


class BulkProtocol(_BulkLikeProtocol):
    """LCD via raw USB bulk (USBLCDNew, 87AD:70DB)."""

    _label = "Bulk"

    @staticmethod
    def _make_device(vid: int, pid: int) -> Any:
        from .bulk import BulkDevice
        return BulkDevice(vid, pid)

    def get_info(self) -> 'ProtocolInfo':
        return self._build_usb_protocol_info(
            "bulk", 4, "USB Bulk (USBLCDNew)", "Raw USB Bulk LCD",
            self._device is not None, pyusb_only=True,
        )

    @property
    def protocol_name(self) -> str:
        return "bulk"

    def __repr__(self) -> str:
        return f"BulkProtocol(vid=0x{self._vid:04x}, pid=0x{self._pid:04x})"


class LyProtocol(_BulkLikeProtocol):
    """LCD via LY USB bulk (0416:5408 / 0416:5409)."""

    _label = "LY"

    @staticmethod
    def _make_device(vid: int, pid: int) -> Any:
        from .ly import LyDevice
        return LyDevice(vid, pid)

    def get_info(self) -> 'ProtocolInfo':
        return self._build_usb_protocol_info(
            "ly", 5, "USB Bulk LY", "USB Bulk LY LCD",
            self._device is not None, pyusb_only=True,
        )

    @property
    def protocol_name(self) -> str:
        return "ly"

    def __repr__(self) -> str:
        return f"LyProtocol(vid=0x{self._vid:04x}, pid=0x{self._pid:04x})"


# =========================================================================
# Factory
# =========================================================================

class DeviceProtocolFactory:
    """Factory that creates and caches protocol instances.

    Protocols are cached by device identity so USB transports stay open
    across successive frame sends. SCSI is the default/primary protocol.

    Usage::

        protocol = DeviceProtocolFactory.get_protocol(device_info)
        protocol.on_send_complete = lambda ok: update_ui(ok)
        protocol.send_data(data, w, h)

        # When done:
        DeviceProtocolFactory.close_all()
    """

    _protocols: Dict[str, DeviceProtocol] = {}
    _scsi_transport_fn: ClassVar[Optional[Callable]] = None

    # Registry map: (protocol, implementation) → factory function.
    _PROTOCOL_REGISTRY: ClassVar[Dict[Tuple[str, str], Callable[..., DeviceProtocol]]] = {
        ('scsi', ''):  lambda di: ScsiProtocol(di.path, di.vid, di.pid),
        ('bulk', ''):  lambda di: BulkProtocol(vid=di.vid, pid=di.pid),
        ('ly', ''):    lambda di: LyProtocol(vid=di.vid, pid=di.pid),
        ('led', ''):   lambda di: LedProtocol(vid=di.vid, pid=di.pid),
        ('hid', ''):   lambda di: HidProtocol(vid=di.vid, pid=di.pid,
                           device_type=getattr(di, 'device_type', 2)),
    }

    @classmethod
    def set_scsi_transport(cls, fn: Callable) -> None:
        """Inject the OS-specific SCSI transport factory.

        Called once at startup by ControllerBuilder with
        os_platform.create_scsi_transport.
        """
        cls._scsi_transport_fn = fn

    @classmethod
    def _device_key(cls, device_info) -> str:
        """Build a cache key from device info."""
        vid = getattr(device_info, 'vid', 0)
        pid = getattr(device_info, 'pid', 0)
        path = getattr(device_info, 'path', '')
        return f"{vid:04x}_{pid:04x}_{path}"

    @classmethod
    def create_protocol(cls, device_info) -> DeviceProtocol:
        """Create a new protocol for the given device (not cached).

        Routes to the appropriate protocol class via _PROTOCOL_REGISTRY.
        Lookup: exact (protocol, implementation) first, then (protocol, '').

        Args:
            device_info: Object with protocol, vid, pid, path, device_type.

        Returns:
            DeviceProtocol subclass instance.

        Raises:
            ValueError: If protocol is unknown.
        """
        protocol = getattr(device_info, 'protocol', 'scsi')
        implementation = getattr(device_info, 'implementation', '')
        log.debug("create_protocol: protocol=%s impl=%s", protocol, implementation)

        factory_fn = cls._PROTOCOL_REGISTRY.get(
            (protocol, implementation),
        ) or cls._PROTOCOL_REGISTRY.get((protocol, ''))

        if factory_fn is None:
            raise ValueError(f"Unknown protocol: {protocol!r}")

        result = factory_fn(device_info)
        path = getattr(device_info, 'path', f'{device_info.vid:04X}:{device_info.pid:04X}')
        log.info("create_protocol: %s for %s", type(result).__name__, path)
        return result

    @classmethod
    def get_protocol(cls, device_info) -> DeviceProtocol:
        """Get or create a cached protocol for the device.

        Args:
            device_info: Object with protocol, vid, pid, path, device_type.

        Returns:
            Cached DeviceProtocol instance.
        """
        key = cls._device_key(device_info)
        if key not in cls._protocols:
            log.debug("get_protocol: cache miss for %s — creating", key)
            cls._protocols[key] = cls.create_protocol(device_info)
        else:
            log.debug("get_protocol: cache hit for %s", key)
        return cls._protocols[key]

    @classmethod
    def remove_protocol(cls, device_info) -> None:
        """Remove and close a cached protocol."""
        key = cls._device_key(device_info)
        if (proto := cls._protocols.pop(key, None)) is not None:
            proto.close()

    @classmethod
    def close_all(cls) -> None:
        """Close all cached protocols and clear the cache."""
        for proto in cls._protocols.values():
            try:
                proto.close()
            except Exception:
                pass
        cls._protocols.clear()

    @classmethod
    def get_cached_count(cls) -> int:
        """Number of cached protocols (for testing)."""
        return len(cls._protocols)

    # =================================================================
    # USB transport + backend helpers
    # =================================================================

    @staticmethod
    def _check_sg_raw() -> bool:
        """Check if sg_raw is available on the system."""
        import shutil
        return shutil.which("sg_raw") is not None

    @staticmethod
    def create_usb_transport(vid: int, pid: int):
        """Create the best available USB transport (pyusb preferred, hidapi fallback)."""
        from .hid import HIDAPI_AVAILABLE, PYUSB_AVAILABLE
        if PYUSB_AVAILABLE:
            from .hid import PyUsbTransport
            return PyUsbTransport(vid, pid)
        elif HIDAPI_AVAILABLE:
            from .hid import HidApiTransport
            return HidApiTransport(vid, pid)
        else:
            raise ImportError(
                "No USB backend available. Install pyusb or hidapi:\n"
                "  pip install pyusb   (+ apt install libusb-1.0-0)\n"
                "  pip install hidapi  (+ apt install libhidapi-dev)"
            )

    @staticmethod
    def _get_hid_backends() -> Dict[str, bool]:
        """Check HID backend availability."""
        try:
            from .hid import HIDAPI_AVAILABLE, PYUSB_AVAILABLE
            return {"pyusb": PYUSB_AVAILABLE, "hidapi": HIDAPI_AVAILABLE}
        except ImportError:
            return {"pyusb": False, "hidapi": False}

    @classmethod
    def get_backend_availability(cls) -> Dict[str, bool]:
        """Check which USB/SCSI backends are installed.

        Returns dict with keys: sg_raw, pyusb, hidapi — each True/False.
        """
        hid = cls._get_hid_backends()
        return {
            "sg_raw": cls._check_sg_raw(),
            "pyusb": hid["pyusb"],
            "hidapi": hid["hidapi"],
        }

    @classmethod
    def get_protocol_info(cls, device_info=None) -> 'ProtocolInfo':
        """Get protocol/backend info for a device (or system defaults).

        If a cached protocol exists for this device, delegates to its get_info().
        Otherwise builds ProtocolInfo from backend availability.

        Args:
            device_info: DeviceInfo object (or None for system-level info).

        Returns:
            ProtocolInfo with all fields populated.
        """
        if device_info is None:
            backends = cls.get_backend_availability()
            return ProtocolInfo(
                protocol="none",
                device_type=0,
                protocol_display="No device",
                device_type_display="",
                active_backend="none",
                backends=backends,
            )

        # If there's a cached protocol, ask it directly
        key = cls._device_key(device_info)
        if (proto := cls._protocols.get(key)) is not None:
            return proto.get_info()

        # No cached protocol — build info from scratch
        backends = cls.get_backend_availability()
        protocol = getattr(device_info, 'protocol', 'scsi')
        device_type = getattr(device_info, 'device_type', 1)

        from trcc.core.models import PROTOCOL_TRAITS
        traits = PROTOCOL_TRAITS.get(protocol)
        if traits is None:
            active = "none"
        elif backends.get(traits.backend_key, False):
            active = traits.backend_key
        elif traits.fallback_backend and backends.get(traits.fallback_backend, False):
            active = traits.fallback_backend
        else:
            active = "none"

        return ProtocolInfo(
            protocol=protocol,
            device_type=device_type,
            protocol_display=PROTOCOL_NAMES.get(protocol, protocol),
            device_type_display=DEVICE_TYPE_NAMES.get(device_type, f"Type {device_type}"),
            active_backend=active,
            backends=backends,
            transport_open=False,
        )


# =========================================================================
# Protocol Info API — for GUI to query device/backend state
# =========================================================================

# Domain data re-exported from core.models (canonical location):
# PROTOCOL_NAMES, DEVICE_TYPE_NAMES, LED_DEVICE_TYPE_NAME


@dataclass
class ProtocolInfo:
    """Protocol and backend info for a device — returned to the GUI.

    Usage in GUI::

        info = DeviceProtocolFactory.get_protocol_info(device)
        label.setText(f"{info.protocol_display} via {info.active_backend}")
    """
    protocol: str = "scsi"
    device_type: int = 1
    protocol_display: str = ""
    device_type_display: str = ""
    active_backend: str = ""
    backends: Dict[str, bool] = field(default_factory=dict)
    transport_open: bool = False

    @property
    def is_scsi(self) -> bool:
        return self.protocol == "scsi"

    @property
    def is_hid(self) -> bool:
        return self.protocol == "hid"

    @property
    def is_led(self) -> bool:
        return self.protocol == "led"

    @property
    def has_backend(self) -> bool:
        """Whether at least one usable backend is available."""
        from trcc.core.models import PROTOCOL_TRAITS
        traits = PROTOCOL_TRAITS.get(self.protocol)
        if traits is None:
            return False
        if self.backends.get(traits.backend_key, False):
            return True
        return bool(traits.fallback_backend
                    and self.backends.get(traits.fallback_backend, False))

