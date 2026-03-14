"""Windows USB device detection via SetupAPI / WMI.

Replaces Linux sysfs + lsusb scanning with Windows-native USB enumeration.
Returns the same DetectedDevice dataclass used by the rest of the system.

References:
    - C# TRCC uses System.Management (WMI) for USB enumeration
    - SetupAPI: SetupDiGetClassDevs + SetupDiEnumDeviceInterfaces
    - WMI: Win32_USBControllerDevice + Win32_PnPEntity
"""
from __future__ import annotations

import logging
from typing import List

from trcc.core.models import DetectedDevice

log = logging.getLogger(__name__)


class WindowsDeviceDetector:
    """Detect Thermalright USB devices on Windows."""

    @staticmethod
    def detect() -> List[DetectedDevice]:
        """Scan for Thermalright USB devices using WMI.

        Strategy:
            1. Query WMI Win32_USBControllerDevice for connected USB devices
            2. Filter by known VID:PID pairs (same registries as Linux detector)
            3. Map to DetectedDevice with protocol/implementation fields
            4. For SCSI devices: find matching PhysicalDrive via WMI

        Returns:
            List of DetectedDevice, same contract as Linux DeviceDetector.detect()
        """
        # Import here to avoid ImportError on Linux
        try:
            import wmi  # pyright: ignore[reportMissingImports]
        except ImportError:
            log.error("wmi package not installed — pip install wmi")
            return []

        devices: List[DetectedDevice] = []
        try:
            w = wmi.WMI()
            for usb in w.Win32_USBControllerDevice():
                dependent = usb.Dependent
                vid, pid = _parse_vid_pid(dependent.DeviceID)
                if vid is None or pid is None:
                    continue

                device = _match_device(vid, pid, dependent)
                if device:
                    devices.append(device)

        except Exception:
            log.exception("WMI USB enumeration failed")

        log.info("Windows detector found %d device(s)", len(devices))
        return devices


def _parse_vid_pid(device_id: str) -> tuple[int | None, int | None]:
    """Extract VID and PID from Windows device ID string.

    Format: USB\\VID_XXXX&PID_XXXX\\...
    """
    import re
    match = re.search(r'VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})', device_id)
    if not match:
        return None, None
    return int(match.group(1), 16), int(match.group(2), 16)


def _match_device(
    vid: int, pid: int, pnp_entity: object,
) -> DetectedDevice | None:
    """Check if VID:PID matches a known Thermalright device.

    Uses the same device registries as the Linux detector
    (KNOWN_DEVICES, _HID_LCD_DEVICES, _LED_DEVICES, _BULK_DEVICES, _LY_DEVICES).
    """
    # Lazy import to share registries with Linux detector
    from trcc.adapters.device.detector import (
        _BULK_DEVICES,
        _HID_LCD_DEVICES,
        _LED_DEVICES,
        _LY_DEVICES,
        KNOWN_DEVICES,
    )

    device_id = getattr(pnp_entity, 'DeviceID', '')

    # Check SCSI devices
    if (vid, pid) in KNOWN_DEVICES:
        entry = KNOWN_DEVICES[(vid, pid)]
        drive_path = _find_physical_drive(vid, pid)
        return DetectedDevice(
            vid=vid, pid=pid,
            vendor_name=entry.vendor, product_name=entry.product,
            usb_path=device_id,
            scsi_device=drive_path,
            implementation=entry.implementation,
            model=getattr(entry, 'model', ''),
            button_image=getattr(entry, 'button_image', ''),
            protocol='scsi',
            device_type=2,
        )

    # Check HID LCD devices
    if (vid, pid) in _HID_LCD_DEVICES:
        entry = _HID_LCD_DEVICES[(vid, pid)]
        return DetectedDevice(
            vid=vid, pid=pid,
            vendor_name=entry.vendor, product_name=entry.product,
            usb_path=device_id,
            scsi_device=None,
            implementation=entry.implementation,
            model=getattr(entry, 'model', ''),
            button_image=getattr(entry, 'button_image', ''),
            protocol='hid',
            device_type=getattr(entry, 'device_type', 2),
        )

    # Check Bulk devices
    if (vid, pid) in _BULK_DEVICES:
        entry = _BULK_DEVICES[(vid, pid)]
        return DetectedDevice(
            vid=vid, pid=pid,
            vendor_name=entry.vendor, product_name=entry.product,
            usb_path=device_id,
            scsi_device=None,
            implementation=entry.implementation,
            model=getattr(entry, 'model', ''),
            button_image=getattr(entry, 'button_image', ''),
            protocol='bulk',
            device_type=4,
        )

    # Check LY devices
    if (vid, pid) in _LY_DEVICES:
        entry = _LY_DEVICES[(vid, pid)]
        return DetectedDevice(
            vid=vid, pid=pid,
            vendor_name=entry.vendor, product_name=entry.product,
            usb_path=device_id,
            scsi_device=None,
            implementation=entry.implementation,
            model=getattr(entry, 'model', ''),
            button_image=getattr(entry, 'button_image', ''),
            protocol='ly',
            device_type=10,
        )

    # Check LED devices
    if (vid, pid) in _LED_DEVICES:
        entry = _LED_DEVICES[(vid, pid)]
        return DetectedDevice(
            vid=vid, pid=pid,
            vendor_name=entry.vendor, product_name=entry.product,
            usb_path=device_id,
            scsi_device=None,
            implementation='hid_led',
            model=getattr(entry, 'model', ''),
            button_image=getattr(entry, 'button_image', ''),
            protocol='hid',
            device_type=0,
        )

    return None


def _find_physical_drive(vid: int, pid: int) -> str | None:
    """Find the Windows physical drive path for a USB SCSI device.

    Maps USB VID:PID → PhysicalDrive via WMI disk associations.
    Returns e.g. '\\\\.\\PhysicalDrive2' or None.
    """
    try:
        import wmi  # pyright: ignore[reportMissingImports]
        w = wmi.WMI()
        for disk in w.Win32_DiskDrive():
            if f'VID_{vid:04X}' in (disk.PNPDeviceID or '').upper():
                return disk.DeviceID  # e.g., \\.\PHYSICALDRIVE2
    except Exception:
        log.debug("Could not find physical drive for %04X:%04X", vid, pid)
    return None
