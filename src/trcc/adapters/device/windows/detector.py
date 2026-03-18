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


def _scan_physical_drives_ctypes() -> list[tuple[str, str]]:
    """Scan PhysicalDrive0..15 for USB-bus drives using ctypes.

    Returns list of (drive_path, vendor_id_string) for USB-connected drives.
    No WMI dependency — uses DeviceIoControl IOCTL_STORAGE_QUERY_PROPERTY.
    """
    import ctypes
    import ctypes.wintypes  # pyright: ignore[reportMissingImports]

    kernel32 = ctypes.windll.kernel32  # pyright: ignore[reportAttributeAccessIssue]

    GENERIC_READ = 0x80000000
    FILE_SHARE_RW = 0x3
    OPEN_EXISTING = 3
    IOCTL_STORAGE_QUERY_PROPERTY = 0x2D1400

    # STORAGE_PROPERTY_QUERY: PropertyId=0 (StorageDeviceProperty), QueryType=0
    query = (ctypes.c_ubyte * 12)()
    query[0] = 0   # PropertyId = StorageDeviceProperty
    query[4] = 0   # QueryType = PropertyStandardQuery

    results: list[tuple[str, str]] = []

    for i in range(16):
        path = f'\\\\.\\PhysicalDrive{i}'
        handle = kernel32.CreateFileW(
            path, GENERIC_READ, FILE_SHARE_RW,
            None, OPEN_EXISTING, 0, None,
        )
        if handle == -1:
            continue
        try:
            # Query storage device descriptor (contains bus type + vendor ID)
            out_buf = (ctypes.c_ubyte * 1024)()
            bytes_returned = ctypes.wintypes.DWORD(0)
            ok = kernel32.DeviceIoControl(
                handle,
                IOCTL_STORAGE_QUERY_PROPERTY,
                ctypes.byref(query), ctypes.sizeof(query),
                ctypes.byref(out_buf), ctypes.sizeof(out_buf),
                ctypes.byref(bytes_returned),
                None,
            )
            if not ok:
                continue

            # STORAGE_DEVICE_DESCRIPTOR layout:
            #   offset 12: VendorIdOffset (DWORD) — offset to vendor string
            #   offset 28: BusType (DWORD) — 7 = BusTypeUsb
            bus_type = int.from_bytes(bytes(out_buf[28:32]), 'little')
            if bus_type != 7:  # Not USB
                continue

            vendor_offset = int.from_bytes(bytes(out_buf[12:16]), 'little')
            vendor = ''
            if 0 < vendor_offset < 1024:
                # Read null-terminated ASCII string
                end = vendor_offset
                while end < 1024 and out_buf[end] != 0:
                    end += 1
                vendor = bytes(out_buf[vendor_offset:end]).decode('ascii', errors='replace').strip()

            results.append((path, vendor))
        finally:
            kernel32.CloseHandle(handle)

    return results


def _find_physical_drive(vid: int, pid: int) -> str | None:
    """Find the Windows physical drive path for a USB SCSI device.

    Maps USB VID:PID → PhysicalDrive via WMI disk associations.
    Returns e.g. '\\\\.\\PhysicalDrive2' or None.

    Two lookup strategies:
    1. Direct: disk PNPDeviceID contains VID_xxxx (some USB devices).
    2. USBSTOR: disk is a USBSTOR child — trace USB parent via
       Win32_USBControllerDevice to match VID/PID.
    """
    vid_tag = f'VID_{vid:04X}'
    pid_tag = f'PID_{pid:04X}'
    try:
        import wmi  # pyright: ignore[reportMissingImports]
        w = wmi.WMI()

        # Strategy 1: VID directly in disk PNPDeviceID
        for disk in w.Win32_DiskDrive():
            pnp = (disk.PNPDeviceID or '').upper()
            log.debug("Strategy 1: disk %s PNP=%s", disk.DeviceID, pnp[:80])
            if vid_tag in pnp:
                return disk.DeviceID

        # Strategy 2: USBSTOR disks — PNPDeviceID uses vendor names
        # (e.g. USBSTOR\DISK&VEN_USBLCD) instead of VID/PID.
        # Confirm the USB device exists, then find the USBSTOR disk
        # by matching known Thermalright USBSTOR vendor strings.
        _USBSTOR_VENDORS = ('VEN_USBLCD', 'VEN_THERMALR', 'VEN_WINBOND')
        has_usb_device = False
        for rel in w.Win32_USBControllerDevice():
            dep = str(rel.Dependent or '').upper()
            if vid_tag in dep and pid_tag in dep:
                has_usb_device = True
                log.debug("USB parent found: %s", dep[:120])
                break
        log.debug("Strategy 2: has_usb_device=%s", has_usb_device)
        if has_usb_device:
            for disk in w.Win32_DiskDrive():
                pnp_upper = (disk.PNPDeviceID or '').upper()
                log.debug("Strategy 2: checking disk %s PNP=%s", disk.DeviceID, pnp_upper[:80])
                if not pnp_upper.startswith('USBSTOR'):
                    continue
                if any(v in pnp_upper for v in _USBSTOR_VENDORS):
                    log.debug("Strategy 2: vendor match! %s", disk.DeviceID)
                    return disk.DeviceID
            # Fallback: no vendor match but USB device confirmed —
            # try any USBSTOR disk with zero/tiny capacity (LCD devices
            # report no real storage, unlike USB flash drives).
            for disk in w.Win32_DiskDrive():
                pnp_upper = (disk.PNPDeviceID or '').upper()
                if not pnp_upper.startswith('USBSTOR'):
                    continue
                size = int(disk.Size or 0)
                if size < 1_000_000:  # < 1MB = not a real storage device
                    log.debug("USBSTOR fallback: %s (size=%d)", disk.DeviceID, size)
                    return disk.DeviceID
    except Exception as e:
        log.debug("WMI strategies failed for %04X:%04X: %s", vid, pid, e)

    # Strategy 3: brute-force scan PhysicalDrive0..15 — no WMI needed.
    # Query each drive's bus type via IOCTL_STORAGE_QUERY_PROPERTY.
    # USB devices report BusTypeUsb (7). Filter to USB-bus drives only,
    # then check vendor string for Thermalright LCD identifiers.
    log.debug("Strategy 3: brute-force PhysicalDrive scan")
    try:
        usb_drives = _scan_physical_drives_ctypes()
        for path, vendor in usb_drives:
            log.debug("Strategy 3: USB drive %s vendor=%r", path, vendor)
            # Match known LCD vendor strings from USBSTOR enumeration
            v_upper = vendor.upper()
            if any(k in v_upper for k in ('USBLCD', 'USB PRC', 'THERMALR', 'WINBOND')):
                log.info("Strategy 3: vendor match at %s (%s)", path, vendor)
                return path
        # Fallback: if only one USB drive found and we confirmed VID/PID
        # exists via WMI (or detection), it's likely our device
        if len(usb_drives) == 1:
            path, vendor = usb_drives[0]
            log.info("Strategy 3: single USB drive fallback %s (%s)", path, vendor)
            return path
    except Exception as e:
        log.debug("Strategy 3 failed: %s", e)

    log.debug("Could not find physical drive for %04X:%04X", vid, pid)
    return None
