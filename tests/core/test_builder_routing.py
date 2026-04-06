"""Tests for ControllerBuilder routing — per-OS platform contract and per-device.

Design:
  - Platform builder fixtures (linux_builder, windows_builder, etc.) inject real
    PlatformAdapters directly — no patching of module-level OS flags.
  - Per-device tests parametrize over ALL_DEVICES — adding a device to the
    registry automatically adds coverage here.
  - for_current_os() is tested as a smoke test only — which platform it picks
    is OS-dependent; the per-platform contract is verified via the fixtures.

Coverage targets:
  - core/builder.py: for_current_os(), build_device() per-protocol routing
  - core/models.py: ALL_DEVICES protocol assignments
  - core/models.py: PROTOCOL_TRAITS completeness
"""
from __future__ import annotations

import pytest

from trcc.core.builder import ControllerBuilder
from trcc.core.models import (
    ALL_DEVICES,
    LED_DEVICES,
    PROTOCOL_TRAITS,
    SCSI_DEVICES,
    DetectedDevice,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _detected_for(vid: int, pid: int, entry) -> DetectedDevice:
    """Build a DetectedDevice exactly as the detector would for a given registry entry."""
    return DetectedDevice(
        vid=vid, pid=pid,
        vendor_name=entry.vendor,
        product_name=entry.product,
        usb_path="2-1",
        protocol=entry.protocol,
        device_type=entry.device_type,
        implementation=entry.implementation,
    )


# ── for_current_os() — smoke test ─────────────────────────────────────────────

def test_for_current_os_returns_controller_builder():
    """for_current_os() must return a ControllerBuilder on any supported OS."""
    # The autouse _mock_builder patches this — bypass it for this specific test.
    ControllerBuilder.__new__(ControllerBuilder)
    actual = ControllerBuilder.for_current_os()
    assert isinstance(actual, ControllerBuilder)


# ── Platform adapter contract — each builder carries the right platform ───────

class TestLinuxBuilderContract:
    def test_has_platform_adapter(self, linux_builder):
        from trcc.core.ports import PlatformAdapter
        assert isinstance(linux_builder._platform, PlatformAdapter)

    def test_build_detect_fn_returns_callable(self, linux_builder):
        from unittest.mock import patch
        with patch("trcc.adapters.device.detector.DeviceDetector.make_detect_fn",
                   return_value=lambda: []):
            fn = linux_builder.build_detect_fn()
        assert callable(fn)

    def test_build_setup_returns_platform_setup(self, linux_builder):
        from trcc.core.ports import PlatformSetup
        assert isinstance(linux_builder.build_setup(), PlatformSetup)

    def test_build_device_led_returns_led(self, linux_builder):
        from trcc.core.device import Device
        detected = DetectedDevice(
            vid=0x0416, pid=0x8001, vendor_name="Winbond", product_name="LED",
            usb_path="2-1", protocol="led",
        )
        device = linux_builder.build_device(detected)
        assert isinstance(device, Device)
        assert device.is_led


class TestWindowsBuilderContract:
    def test_has_platform_adapter(self, windows_builder):
        from trcc.core.ports import PlatformAdapter
        assert isinstance(windows_builder._platform, PlatformAdapter)

    def test_build_setup_returns_platform_setup(self, windows_builder):
        from trcc.core.ports import PlatformSetup
        assert isinstance(windows_builder.build_setup(), PlatformSetup)

    def test_build_device_led_returns_led(self, windows_builder):
        from trcc.core.device import Device
        detected = DetectedDevice(
            vid=0x0416, pid=0x8001, vendor_name="Winbond", product_name="LED",
            usb_path="2-1", protocol="led",
        )
        device = windows_builder.build_device(detected)
        assert isinstance(device, Device)
        assert device.is_led


class TestMacOSBuilderContract:
    def test_has_platform_adapter(self, macos_builder):
        from trcc.core.ports import PlatformAdapter
        assert isinstance(macos_builder._platform, PlatformAdapter)

    def test_build_setup_returns_platform_setup(self, macos_builder):
        from trcc.core.ports import PlatformSetup
        assert isinstance(macos_builder.build_setup(), PlatformSetup)

    def test_build_device_led_returns_led(self, macos_builder):
        from trcc.core.device import Device
        detected = DetectedDevice(
            vid=0x0416, pid=0x8001, vendor_name="Winbond", product_name="LED",
            usb_path="2-1", protocol="led",
        )
        device = macos_builder.build_device(detected)
        assert isinstance(device, Device)
        assert device.is_led


class TestBSDBuilderContract:
    def test_has_platform_adapter(self, bsd_builder):
        from trcc.core.ports import PlatformAdapter
        assert isinstance(bsd_builder._platform, PlatformAdapter)

    def test_build_setup_returns_platform_setup(self, bsd_builder):
        from trcc.core.ports import PlatformSetup
        assert isinstance(bsd_builder.build_setup(), PlatformSetup)

    def test_build_device_led_returns_led(self, bsd_builder):
        from trcc.core.device import Device
        detected = DetectedDevice(
            vid=0x0416, pid=0x8001, vendor_name="Winbond", product_name="LED",
            usb_path="2-1", protocol="led",
        )
        device = bsd_builder.build_device(detected)
        assert isinstance(device, Device)
        assert device.is_led


# ── build_device() — per-device protocol routing ─────────────────────────────
#
# Parametrized over ALL_DEVICES — every known VID:PID is tested.
# Adding a new device to models.py automatically adds coverage here.

@pytest.mark.parametrize("vid_pid,entry", list(ALL_DEVICES.items()),
                         ids=[f"{v:04X}:{p:04X}" for v, p in ALL_DEVICES])
def test_build_device_routes_to_correct_type(vid_pid, entry, linux_builder):
    """Every device in ALL_DEVICES must build the right Device type.

    LED devices (protocol='led') → Device with device_type=False.
    Everything else → Device with is_lcd=True.
    """
    from trcc.core.device import Device
    from trcc.services.image import ImageService

    detected = _detected_for(*vid_pid, entry)
    trait = PROTOCOL_TRAITS.get(entry.protocol, PROTOCOL_TRAITS['scsi'])

    if trait.is_led:
        device = linux_builder.build_device(detected)
        assert isinstance(device, Device) and device.is_led, (
            f"{vid_pid[0]:04X}:{vid_pid[1]:04X} (protocol={entry.protocol!r}) "
            f"should build LED Device"
        )
    else:
        linux_builder._renderer = ImageService._r()
        device = linux_builder.build_device(detected)
        assert isinstance(device, Device) and device.is_lcd, (
            f"{vid_pid[0]:04X}:{vid_pid[1]:04X} (protocol={entry.protocol!r}) "
            f"should build LCD Device"
        )


@pytest.mark.parametrize("vid_pid,entry", list(ALL_DEVICES.items()),
                         ids=[f"{v:04X}:{p:04X}" for v, p in ALL_DEVICES])
def test_build_device_wires_device_service(vid_pid, entry, linux_builder):
    """Every built device has a wired DeviceService so connect() can work."""
    from trcc.services.image import ImageService

    detected = _detected_for(*vid_pid, entry)
    trait = PROTOCOL_TRAITS.get(entry.protocol, PROTOCOL_TRAITS['scsi'])

    if not trait.is_led:
        linux_builder._renderer = ImageService._r()

    device = linux_builder.build_device(detected)
    assert device._device_svc is not None


@pytest.mark.parametrize("vid_pid,entry", list(LED_DEVICES.items()),
                         ids=[f"{v:04X}:{p:04X}" for v, p in LED_DEVICES])
def test_led_devices_wire_get_protocol(vid_pid, entry, linux_builder):
    """LED devices must have get_protocol wired — needed to send LED data."""
    detected = _detected_for(*vid_pid, entry)
    device = linux_builder.build_device(detected)
    assert device._get_protocol is not None


def test_build_device_no_detected_builds_lcd(linux_builder):
    """build_device(None) builds an unconnected LCD Device as the default."""
    from trcc.core.device import Device
    from trcc.services.image import ImageService

    linux_builder._renderer = ImageService._r()
    device = linux_builder.build_device(None)
    assert isinstance(device, Device)
    assert device.is_lcd


def test_build_device_lcd_without_renderer_raises(linux_builder):
    """build_device() for an LCD device raises RuntimeError if renderer not set."""
    vid, pid = next(iter(SCSI_DEVICES))
    entry = SCSI_DEVICES[(vid, pid)]
    detected = _detected_for(vid, pid, entry)
    linux_builder._renderer = None

    with pytest.raises(RuntimeError, match="renderer"):
        linux_builder.build_device(detected)


# ── PROTOCOL_TRAITS completeness — models are the contract ───────────────────

def test_all_devices_have_known_protocol():
    """Every device in ALL_DEVICES must have a protocol key in PROTOCOL_TRAITS."""
    unknown = [
        f"{v:04X}:{p:04X} ({e.protocol!r})"
        for (v, p), e in ALL_DEVICES.items()
        if e.protocol not in PROTOCOL_TRAITS
    ]
    assert not unknown, f"Unknown protocols in ALL_DEVICES: {unknown}"


def test_scsi_devices_are_not_led():
    """SCSI devices must never classify as LED — they are LCD displays."""
    for (v, p), entry in SCSI_DEVICES.items():
        trait = PROTOCOL_TRAITS[entry.protocol]
        assert not trait.is_led, (
            f"{v:04X}:{p:04X} SCSI device incorrectly flagged as LED"
        )


def test_led_devices_classify_as_led():
    """LED_DEVICES entries must resolve to device_type=False via PROTOCOL_TRAITS."""
    for (v, p), entry in LED_DEVICES.items():
        trait = PROTOCOL_TRAITS.get(entry.protocol)
        assert trait is not None, (
            f"{v:04X}:{p:04X} protocol={entry.protocol!r} missing from PROTOCOL_TRAITS"
        )
        assert trait.is_led, (
            f"{v:04X}:{p:04X} LED device not classified as LED "
            f"(protocol={entry.protocol!r}, is_led={trait.is_led})"
        )


def test_lcd_and_led_have_distinct_protocols():
    """HID LCD and LED devices must have different protocol keys.

    Before the fix both used 'hid' — that required special-casing everywhere.
    Now LED uses 'led' and the factory routes cleanly.
    """
    from trcc.core.models import HID_LCD_DEVICES
    hid_lcd_protocols = {e.protocol for e in HID_LCD_DEVICES.values()}
    led_protocols = {e.protocol for e in LED_DEVICES.values()}
    assert hid_lcd_protocols.isdisjoint(led_protocols), (
        f"HID LCD and LED devices share protocol keys: "
        f"{hid_lcd_protocols & led_protocols}"
    )
