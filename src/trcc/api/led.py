"""LED RGB control endpoints — color, mode, zones, segments."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from trcc.api.models import (
    ClockFormatRequest,
    LEDBrightnessRequest,
    LEDColorRequest,
    LEDModeRequest,
    LEDSensorRequest,
    SegmentToggleRequest,
    TempUnitRequest,
    ZoneBrightnessRequest,
    ZoneColorRequest,
    ZoneModeRequest,
    ZoneSyncRequest,
    ZoneToggleRequest,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/led", tags=["led"])


def _get_led():
    """Get the active LEDDispatcher, raise 409 if not connected."""
    from trcc.api import _led_dispatcher

    if not _led_dispatcher or not _led_dispatcher.connected:
        raise HTTPException(status_code=409, detail="No LED device selected. POST /devices/{id}/select first.")
    return _led_dispatcher


def _parse_hex(hex_color: str) -> tuple[int, int, int]:
    """Parse hex color string to (r, g, b). Raises 400 on invalid format."""
    hex_color = hex_color.lstrip('#')
    if len(hex_color) != 6:
        raise HTTPException(status_code=400, detail="Invalid hex color (use 6-digit hex, e.g. '00ff00')")
    try:
        return (int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid hex color (use 6-digit hex, e.g. '00ff00')")


def _dispatch_result(result: dict) -> dict:
    """Convert dispatcher result to API response. Raises on failure."""
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Unknown error"))
    # Strip non-serializable fields (color arrays with numpy)
    safe = {}
    for k, v in result.items():
        if k == "colors":
            continue  # numpy arrays / large lists — skip for JSON
        safe[k] = v
    return safe


# ── Global operations ──────────────────────────────────────────────────

@router.post("/color")
def set_color(body: LEDColorRequest) -> dict:
    """Set LED static color."""
    led = _get_led()
    r, g, b = _parse_hex(body.hex)
    return _dispatch_result(led.set_color(r, g, b))


@router.post("/mode")
def set_mode(body: LEDModeRequest) -> dict:
    """Set LED effect mode (static, breathing, colorful, rainbow)."""
    led = _get_led()
    return _dispatch_result(led.set_mode(body.mode))


@router.post("/brightness")
def set_brightness(body: LEDBrightnessRequest) -> dict:
    """Set LED brightness (0-100)."""
    led = _get_led()
    return _dispatch_result(led.set_brightness(body.level))


@router.post("/off")
def turn_off() -> dict:
    """Turn LEDs off."""
    led = _get_led()
    return _dispatch_result(led.off())


@router.post("/sensor")
def set_sensor(body: LEDSensorRequest) -> dict:
    """Set CPU/GPU sensor source for temp/load linked modes."""
    led = _get_led()
    return _dispatch_result(led.set_sensor_source(body.source))


# ── Zone operations ────────────────────────────────────────────────────

@router.post("/zones/{zone}/color")
def set_zone_color(zone: int, body: ZoneColorRequest) -> dict:
    """Set color for a specific LED zone."""
    led = _get_led()
    r, g, b = _parse_hex(body.hex)
    return _dispatch_result(led.set_zone_color(zone, r, g, b))


@router.post("/zones/{zone}/mode")
def set_zone_mode(zone: int, body: ZoneModeRequest) -> dict:
    """Set effect mode for a specific LED zone."""
    led = _get_led()
    return _dispatch_result(led.set_zone_mode(zone, body.mode))


@router.post("/zones/{zone}/brightness")
def set_zone_brightness(zone: int, body: ZoneBrightnessRequest) -> dict:
    """Set brightness for a specific LED zone (0-100)."""
    led = _get_led()
    return _dispatch_result(led.set_zone_brightness(zone, body.level))


@router.post("/zones/{zone}/toggle")
def toggle_zone(zone: int, body: ZoneToggleRequest) -> dict:
    """Toggle a specific LED zone on/off."""
    led = _get_led()
    return _dispatch_result(led.toggle_zone(zone, body.on))


@router.post("/sync")
def set_sync(body: ZoneSyncRequest) -> dict:
    """Enable/disable zone sync (circulate/select-all)."""
    led = _get_led()
    return _dispatch_result(led.set_zone_sync(body.enabled, body.interval))


# ── Segment operations ─────────────────────────────────────────────────

@router.post("/segments/{index}/toggle")
def toggle_segment(index: int, body: SegmentToggleRequest) -> dict:
    """Toggle a specific LED segment on/off."""
    led = _get_led()
    return _dispatch_result(led.toggle_segment(index, body.on))


@router.post("/clock")
def set_clock(body: ClockFormatRequest) -> dict:
    """Set LED segment display clock format (12h/24h)."""
    led = _get_led()
    return _dispatch_result(led.set_clock_format(body.is_24h))


@router.post("/temp-unit")
def set_temp_unit(body: TempUnitRequest) -> dict:
    """Set LED segment display temperature unit (C/F)."""
    led = _get_led()
    return _dispatch_result(led.set_temp_unit(body.unit))


# ── Status ─────────────────────────────────────────────────────────────

@router.get("/status")
def led_status() -> dict:
    """Get current LED state — connected, init status."""
    from trcc.api import _led_dispatcher

    if not _led_dispatcher or not _led_dispatcher.connected:
        return {"connected": False}

    led = _led_dispatcher
    return {
        "connected": True,
        "status": led.status,
    }
