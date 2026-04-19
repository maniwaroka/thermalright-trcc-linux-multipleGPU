"""HID Protocol adapter — DeviceProtocol ABC conformance for HID LCD devices.

Delegates raw I/O to `hid.py::HidDeviceType2/Type3/HidDeviceManager`.
Transport lifecycle is inherited from `UsbProtocol` (lazy open via the
Platform-injected `DeviceProtocolFactory.create_usb_transport`).
"""
from __future__ import annotations

import logging
from typing import Optional

from trcc.core.models import DEVICE_TYPE_NAMES, HandshakeResult

from .factory import ProtocolInfo, UsbProtocol

log = logging.getLogger(__name__)


class HidProtocol(UsbProtocol):
    """LCD communication via HID USB bulk protocol (pyusb or hidapi).

    Type 2 and Type 3 are firmware variants of the same HID LCD protocol,
    selected by `device_type`. Prefers pyusb, falls back to hidapi.
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

    def get_info(self) -> ProtocolInfo:
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
