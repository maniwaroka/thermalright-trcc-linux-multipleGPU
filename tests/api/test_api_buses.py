"""Phase 4 — verify API endpoints dispatch through CommandBus.

Tests confirm that:
  - The right Command type reaches the device method.
  - dispatch_result() is applied to CommandResult.payload.
  - Both LCD and LED endpoints route through TrccApp.build_*_bus().

Strategy:
  - Set up a real TrccApp backed by a MagicMock builder.
  - Provide a mock LCDDevice / LEDDevice whose methods return known dicts.
  - Set api_module._display_dispatcher / _led_dispatcher to the mock device.
  - POST to the endpoint and assert the device method was invoked with the
    correct arguments AND the response payload matches what the device returned.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import trcc.api as api_module
from trcc.api import app, configure_auth
from trcc.core.app import TrccApp
from trcc.core.models import FBL_PROFILES, SCSI_DEVICES

# Resolution for the first registered SCSI device (via models — single source of truth)
_SCSI_VID_PID = next(iter(SCSI_DEVICES))
_LCD_RESOLUTION = FBL_PROFILES[SCSI_DEVICES[_SCSI_VID_PID].fbl].resolution

# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture()
def real_app():
    """Fresh TrccApp backed by a mock builder — direct construction."""
    TrccApp.reset()
    inst = TrccApp(MagicMock())
    TrccApp._instance = inst
    yield inst
    TrccApp.reset()


@pytest.fixture()
def mock_lcd(real_app):
    """MagicMock LCD device wired as the active display dispatcher.

    Resolution comes from FBL_PROFILES for the first registered SCSI device
    so the fixture stays in sync with models without hardcoding (320, 320).
    """
    from trcc.core.handlers.lcd import build_lcd_bus
    configure_auth(None)
    dev = MagicMock()
    dev.connected = True
    dev.resolution = _LCD_RESOLUTION
    dev.device_path = "/dev/sg0"
    api_module._display_dispatcher = dev
    real_app._lcd_bus = build_lcd_bus(dev)
    yield dev
    api_module._display_dispatcher = None


@pytest.fixture()
def mock_led(real_app):
    """MagicMock LED device wired as the active LED dispatcher."""
    from trcc.core.handlers.led import build_led_bus
    configure_auth(None)
    dev = MagicMock()
    dev.connected = True
    api_module._led_dispatcher = dev
    real_app._led_bus = build_led_bus(dev)
    yield dev
    api_module._led_dispatcher = None


@pytest.fixture()
def client():
    return TestClient(app)


# ── LCD endpoints ─────────────────────────────────────────────────────────

class TestDisplayAPIBus:
    @patch('trcc.api.stop_overlay_loop')
    @patch('trcc.api.stop_video_playback')
    def test_set_color_calls_device(self, _sv, _so, mock_lcd, client):
        payload = {"success": True, "message": "Color set"}
        mock_lcd.send_color.return_value = payload
        resp = client.post("/display/color", json={"hex": "ff0000"})
        assert resp.status_code == 200
        assert resp.json() == payload
        mock_lcd.send_color.assert_called_once_with(255, 0, 0)

    def test_set_brightness_calls_device(self, mock_lcd, client):
        payload = {"success": True, "message": "Brightness set"}
        mock_lcd.set_brightness.return_value = payload
        resp = client.post("/display/brightness", json={"level": 2})
        assert resp.status_code == 200
        assert resp.json() == payload
        mock_lcd.set_brightness.assert_called_once_with(2)

    def test_set_rotation_calls_device(self, mock_lcd, client):
        payload = {"success": True, "message": "Rotation set"}
        mock_lcd.set_rotation.return_value = payload
        resp = client.post("/display/rotation", json={"degrees": 90})
        assert resp.status_code == 200
        assert resp.json() == payload
        mock_lcd.set_rotation.assert_called_once_with(90)

    def test_set_split_calls_device(self, mock_lcd, client):
        payload = {"success": True, "message": "Split mode set"}
        mock_lcd.set_split_mode.return_value = payload
        resp = client.post("/display/split", json={"mode": 1})
        assert resp.status_code == 200
        assert resp.json() == payload
        mock_lcd.set_split_mode.assert_called_once_with(1)

    def test_brightness_failure_returns_400(self, mock_lcd, client):
        mock_lcd.set_brightness.return_value = {
            "success": False, "error": "Device not responding"}
        resp = client.post("/display/brightness", json={"level": 2})
        assert resp.status_code == 400


# ── LED endpoints ─────────────────────────────────────────────────────────

class TestLEDAPIBus:
    def test_set_color_calls_device(self, mock_led, client):
        payload = {"success": True, "message": "Color set"}
        mock_led.set_color.return_value = payload
        with patch('trcc.api.stop_led_loop'):
            resp = client.post("/led/color", json={"hex": "00ff00"})
        assert resp.status_code == 200
        assert resp.json() == payload
        mock_led.set_color.assert_called_once_with(0, 255, 0)

    def test_set_brightness_calls_device(self, mock_led, client):
        payload = {"success": True, "message": "Brightness set"}
        mock_led.set_brightness.return_value = payload
        resp = client.post("/led/brightness", json={"level": 75})
        assert resp.status_code == 200
        assert resp.json() == payload
        mock_led.set_brightness.assert_called_once_with(75)

    def test_set_sensor_calls_device(self, mock_led, client):
        payload = {"success": True, "message": "Sensor set"}
        mock_led.set_sensor_source.return_value = payload
        resp = client.post("/led/sensor", json={"source": "gpu"})
        assert resp.status_code == 200
        assert resp.json() == payload
        mock_led.set_sensor_source.assert_called_once_with("gpu")

    def test_set_zone_color_calls_device(self, mock_led, client):
        payload = {"success": True, "message": "Zone color set"}
        mock_led.set_zone_color.return_value = payload
        resp = client.post("/led/zones/2/color", json={"hex": "0000ff"})
        assert resp.status_code == 200
        assert resp.json() == payload
        mock_led.set_zone_color.assert_called_once_with(2, 0, 0, 255)
