"""Linux device detection facade — enriches raw detection with config + LED probe.

Wraps the raw detector output (sysfs enumeration) with saved identity lookup,
LED model probing, and device dict assembly for the rest of the app.
"""
from __future__ import annotations

import logging
from typing import Dict, List

log = logging.getLogger(__name__)


def _load_saved_identity(
    dev_index: int, vid: int, pid: int,
) -> tuple[str | None, str | None]:
    """Load previously resolved button_image + product name from config.

    Returns (button_image, product) or (None, None) if not saved.
    """
    try:
        from trcc.conf import Settings
        key = Settings.device_config_key(dev_index, vid, pid)
        cfg = Settings.get_device_config(key)
        return cfg.get('resolved_button_image'), cfg.get('resolved_product')
    except Exception:
        return None, None


def find_lcd_devices() -> List[Dict]:
    """Detect connected LCD devices (SCSI and HID).

    Returns:
        List of dicts with keys: name, path, resolution, vendor, product,
        model, button_image, protocol, device_type, vid, pid
    """
    try:
        from trcc.adapters.device.detector import detect_devices
    except ImportError:
        return []

    raw = detect_devices()
    devices = []

    for dev in raw:
        protocol = getattr(dev, 'protocol', 'scsi')
        device_type = getattr(dev, 'device_type', 1)

        # Load previously resolved device identity from config (C# SetButtonImage).
        # First launch: no saved data, uses registry defaults. Handshake resolves
        # the real product and saves to config. Subsequent launches: correct button
        # image shown immediately. Re-verified on each handshake (detects cooler swaps).
        dev_idx = len(devices)
        saved_btn, saved_product = _load_saved_identity(dev_idx, dev.vid, dev.pid)

        if protocol == 'scsi':
            # SCSI devices need a /dev/sgX path
            if not dev.scsi_device:
                continue

            product = saved_product or dev.product_name
            # Resolution (0,0) until handshake polls FBL from device
            devices.append({
                'name': f"Thermalright {product}" if saved_product
                        else f"{dev.vendor_name} {dev.product_name}",
                'path': dev.scsi_device,
                'resolution': (0, 0),
                'vendor': dev.vendor_name,
                'product': product,
                'model': dev.model,
                'button_image': saved_btn or dev.button_image,
                'vid': dev.vid,
                'pid': dev.pid,
                'protocol': 'scsi',
                'device_type': 1,
                'implementation': dev.implementation,
            })
        elif protocol == 'hid':
            # HID devices use USB VID:PID directly (no SCSI path)
            # Path is a synthetic identifier for the factory
            hid_path = f"hid:{dev.vid:04x}:{dev.pid:04x}"

            model = dev.model
            button_image = saved_btn or dev.button_image
            led_style_id = None

            # All LED devices share PID 0x8001 — probe via HID handshake
            # to discover the real model (AX120, PA120, LC1, etc.).
            if dev.implementation == 'hid_led':
                try:
                    from trcc.adapters.device.led import PmRegistry, probe_led_model
                    info = probe_led_model(dev.vid, dev.pid,
                                           usb_path=dev.usb_path)
                    if info and info.model_name:
                        model = info.model_name
                        led_style_id = info.style.style_id if info.style else None
                        btn = PmRegistry.get_button_image(info.pm, info.sub_type)
                        if btn:
                            button_image = btn
                except Exception:
                    pass  # Fall back to registry default

            product = saved_product or dev.product_name
            devices.append({
                'name': f"Thermalright {product}" if saved_product
                        else f"{dev.vendor_name} {dev.product_name}",
                'path': hid_path,
                'resolution': (0, 0),  # Unknown until HID handshake (PM->FBL->resolution)
                'vendor': dev.vendor_name,
                'product': product,
                'model': model,
                'led_style_id': led_style_id,
                'button_image': button_image,
                'vid': dev.vid,
                'pid': dev.pid,
                'protocol': 'hid',
                'device_type': device_type,
                'implementation': dev.implementation,
            })
        elif protocol in ('bulk', 'ly'):
            # Bulk / LY USB devices — no SCSI path, use VID:PID
            dev_path = f"{protocol}:{dev.vid:04x}:{dev.pid:04x}"

            product = saved_product or dev.product_name
            devices.append({
                'name': f"Thermalright {product}" if saved_product
                        else f"{dev.vendor_name} {dev.product_name}",
                'path': dev_path,
                'resolution': (0, 0),
                'vendor': dev.vendor_name,
                'product': product,
                'model': dev.model,
                'button_image': saved_btn or dev.button_image,
                'vid': dev.vid,
                'pid': dev.pid,
                'protocol': protocol,
                'device_type': device_type,
                'implementation': dev.implementation,
            })

    # Sort by path for stable ordinal assignment
    devices.sort(key=lambda d: d['path'])
    for i, d in enumerate(devices):
        d['device_index'] = i

    return devices


def send_image_to_device(
    device_path: str,
    rgb565_data: bytes,
    width: int,
    height: int,
) -> bool:
    """Send RGB565 image data to an LCD device via SCSI.

    Initializes (poll + init) on first send to each device, then skips
    init for subsequent sends.

    Args:
        device_path: SCSI device path (e.g. /dev/sg0)
        rgb565_data: Raw RGB565 pixel bytes (big-endian, width*height*2 bytes)
        width: Image width in pixels
        height: Image height in pixels

    Returns:
        True if the send succeeded.
    """
    from trcc.adapters.device.scsi import ScsiDevice

    try:
        if device_path not in ScsiDevice._initialized_devices:
            ScsiDevice._init_device(device_path)  # return value unused here
            ScsiDevice._initialized_devices.add(device_path)

        ScsiDevice._send_frame(device_path, rgb565_data, width, height)
        return True
    except Exception as e:
        log.error("SCSI send failed (%s): %s", device_path, e)
        # Allow re-init on next attempt
        ScsiDevice._initialized_devices.discard(device_path)
        return False
