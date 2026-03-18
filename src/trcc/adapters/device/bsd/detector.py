"""FreeBSD USB device detection via pyusb.

On FreeBSD, pyusb works with the libusb backend (same as Linux/macOS).
USB devices appear as /dev/ugen* entries; pyusb abstracts this away.

SCSI passthrough uses /dev/pass* via camcontrol (no /dev/sg*).
HID devices work via hidapi/python-hid.

Requires: pkg install libusb py-pyusb
"""
from __future__ import annotations

import logging
import subprocess
from typing import Any, List

from trcc.core.models import DetectedDevice

log = logging.getLogger(__name__)


class BSDDeviceDetector:
    """Detect Thermalright USB devices on FreeBSD/BSD."""

    @staticmethod
    def detect() -> List[DetectedDevice]:
        """Scan for Thermalright USB devices.

        Strategy:
            1. Use pyusb (same as Linux/macOS) for VID:PID enumeration
            2. Filter by known device registries
            3. For SCSI devices: map to /dev/pass* via camcontrol

        Returns:
            List of DetectedDevice, same contract as Linux DeviceDetector.detect()
        """
        devices: List[DetectedDevice] = []

        try:
            import usb.core  # pyright: ignore[reportMissingImports]
        except ImportError:
            log.error("pyusb not installed — pkg install py-pyusb")
            return []

        from trcc.adapters.device.detector import (
            _BULK_DEVICES,
            _HID_LCD_DEVICES,
            _LED_DEVICES,
            _LY_DEVICES,
            KNOWN_DEVICES,
        )

        all_registries = [
            (KNOWN_DEVICES, 'scsi', 2),
            (_HID_LCD_DEVICES, 'hid', None),
            (_BULK_DEVICES, 'bulk', 4),
            (_LY_DEVICES, 'ly', 10),
            (_LED_DEVICES, 'hid', 0),
        ]

        # Build VID:PID → /dev/pass* map for SCSI routing
        pass_map = _get_pass_device_map()

        for registry, protocol, device_type in all_registries:
            for (vid, pid), entry in registry.items():
                usb_dev: Any = usb.core.find(idVendor=vid, idProduct=pid)
                if usb_dev is None:
                    continue

                impl = entry.implementation
                if registry is _LED_DEVICES:
                    impl = 'hid_led'

                dt = device_type
                if dt is None:
                    dt = getattr(entry, 'device_type', 2)

                usb_path = f'usb:{usb_dev.bus}:{usb_dev.address}'

                # FreeBSD: SCSI devices use /dev/pass*
                scsi_dev = None
                if protocol == 'scsi':
                    key = f'{vid:04x}:{pid:04x}'
                    scsi_dev = pass_map.get(key)

                devices.append(DetectedDevice(
                    vid=vid, pid=pid,
                    vendor_name=entry.vendor, product_name=entry.product,
                    usb_path=usb_path,
                    scsi_device=scsi_dev,
                    implementation=impl,
                    model=getattr(entry, 'model', ''),
                    button_image=getattr(entry, 'button_image', ''),
                    protocol=protocol,
                    device_type=dt,
                ))

        log.info("BSD detector found %d device(s)", len(devices))
        return devices


def _get_pass_device_map() -> dict[str, str]:
    """Map VID:PID to /dev/pass* via camcontrol devlist.

    Parses camcontrol output to find CAM passthrough devices
    that correspond to USB mass-storage devices.

    Returns:
        Dict mapping 'vid:pid' → '/dev/passN'
    """
    try:
        result = subprocess.run(
            ['camcontrol', 'devlist'],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return {}

        devices: dict[str, str] = {}
        for line in result.stdout.splitlines():
            # Format: <VENDOR MODEL REV>  at scbus0 target 0 lun 0 (pass0,da0)
            if 'pass' in line:
                # Extract pass device from parentheses
                paren = line.rsplit('(', 1)
                if len(paren) == 2:
                    devs = paren[1].rstrip(')')
                    for d in devs.split(','):
                        d = d.strip()
                        if d.startswith('pass'):
                            devices[d] = f'/dev/{d}'
        return devices
    except Exception:
        log.debug("camcontrol devlist failed")
    return {}


def get_usb_list() -> list[str]:
    """Get USB device list via usbconfig (for diagnostics).

    Returns raw output lines from `usbconfig list`.
    """
    try:
        result = subprocess.run(
            ['usbconfig', 'list'],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.splitlines()
    except Exception:
        log.debug("usbconfig list failed")
    return []
