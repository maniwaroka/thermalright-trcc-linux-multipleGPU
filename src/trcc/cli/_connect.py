"""Shared CLI device connection helper — used by both LCD and LED commands."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def connect_device(
    device_type: str,
    device_path: str | None = None,
) -> int:
    """Connect device via discover(). Returns exit code (0 = success).

    Args:
        device_type: 'lcd' or 'led'
        device_path: Optional device path (e.g. '/dev/sg0') for LCD.
    """
    from trcc.core.app import TrccApp
    from trcc.core.instance import find_active
    from trcc.ipc import create_lcd_proxy, create_led_proxy

    proxy_map = {'lcd': create_lcd_proxy, 'led': create_led_proxy}
    has_attr = {'lcd': 'has_lcd', 'led': 'has_led'}

    log.debug("connecting %s device=%s", device_type, device_path)
    app = TrccApp.get()
    app.set_ipc_handlers(find_active, proxy_map[device_type])
    result = app.discover(path=device_path)
    if not result["success"] or not getattr(app, has_attr[device_type]):
        error = result.get("error", f"No {device_type.upper()} device found.")
        log.warning("%s connect failed: %s", device_type.upper(), error)
        print(error)
        if device_type == 'lcd':
            print("Run 'trcc report' to diagnose.")
        return 1
    log.debug("%s connected successfully", device_type.upper())
    return 0
