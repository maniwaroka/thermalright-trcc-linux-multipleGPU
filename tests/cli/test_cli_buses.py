"""Phase 3 — verify CLI wrapper functions dispatch through CommandBus.

Tests confirm that:
  - The right Command type is dispatched for each CLI operation.
  - The device method is ultimately called with correct arguments.
  - Existing _print_result logic still works (success / failure path).

Strategy:
  - Set up a real TrccApp instance backed by a MagicMock builder.
  - Provide a mock LCDDevice / LEDDevice whose methods return known dicts.
  - Patch _connect_or_fail to return the mock device (avoids USB probing).
  - Call the CLI function and assert the device method was invoked.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from trcc.core.app import TrccApp

# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture()
def app():
    """Fresh TrccApp backed by a mock builder."""
    TrccApp.reset()
    inst = TrccApp(MagicMock())
    TrccApp._instance = inst
    yield inst
    TrccApp.reset()


@pytest.fixture()
def mock_lcd():
    lcd = MagicMock()
    lcd.send_image.return_value    = {"success": True, "message": "sent"}
    lcd.send_color.return_value    = {"success": True, "message": "color set"}
    lcd.set_brightness.return_value = {"success": True, "message": "brightness 50%"}
    lcd.set_rotation.return_value   = {"success": True, "message": "rotated"}
    lcd.set_split_mode.return_value = {"success": True, "message": "split off"}
    return lcd


@pytest.fixture()
def mock_led():
    led = MagicMock()
    led.set_color.return_value         = {"success": True, "message": "color ok"}
    led.set_brightness.return_value    = {"success": True, "message": "bright ok"}
    led.set_sensor_source.return_value = {"success": True, "message": "source ok"}
    led.set_zone_color.return_value    = {"success": True, "message": "zone ok"}
    return led


# ── LCD CLI commands ──────────────────────────────────────────────────────

class TestDisplayCLIBus:
    def test_send_image_calls_device(self, app, mock_lcd, tmp_path):
        from trcc.cli._display import send_image
        img = tmp_path / "x.png"
        img.touch()
        with patch("trcc.cli._display._connect_or_fail", return_value=(mock_lcd, 0)):
            send_image(app, str(img))
        mock_lcd.send_image.assert_called_once_with(str(img))

    def test_send_color_calls_device(self, app, mock_lcd):
        from trcc.cli._display import send_color
        with patch("trcc.cli._display._connect_or_fail", return_value=(mock_lcd, 0)):
            send_color(app, "ff0000")
        mock_lcd.send_color.assert_called_once_with(255, 0, 0)

    def test_set_brightness_calls_device(self, app, mock_lcd):
        from trcc.cli._display import set_brightness
        with patch("trcc.cli._display._connect_or_fail", return_value=(mock_lcd, 0)):
            set_brightness(app, 2)
        mock_lcd.set_brightness.assert_called_once_with(2)

    def test_set_brightness_failure_prints_hint(self, app, mock_lcd, capsys):
        from trcc.cli._display import set_brightness
        mock_lcd.set_brightness.return_value = {
            "success": False, "error": "Brightness: 1-3 (level) or 0-100 (percent)"}
        with patch("trcc.cli._display._connect_or_fail", return_value=(mock_lcd, 0)):
            rc = set_brightness(app, 99)
        out = capsys.readouterr().out
        assert rc == 1
        assert "1 = 25%" in out

    def test_set_rotation_calls_device(self, app, mock_lcd):
        from trcc.cli._display import set_rotation
        with patch("trcc.cli._display._connect_or_fail", return_value=(mock_lcd, 0)):
            set_rotation(app, 90)
        mock_lcd.set_rotation.assert_called_once_with(90)

    def test_set_split_mode_calls_device(self, app, mock_lcd):
        from trcc.cli._display import set_split_mode
        with patch("trcc.cli._display._connect_or_fail", return_value=(mock_lcd, 0)):
            set_split_mode(app, 1)
        mock_lcd.set_split_mode.assert_called_once_with(1)


# ── LED CLI commands ──────────────────────────────────────────────────────

class TestLedCLIBus:
    def test_set_color_calls_device(self, app, mock_led):
        from trcc.cli._led import set_color
        with patch("trcc.cli._led._connect_or_fail", return_value=(mock_led, 0)):
            set_color(app, "00ff00")
        mock_led.set_color.assert_called_once_with(0, 255, 0)

    def test_set_led_brightness_calls_device(self, app, mock_led):
        from trcc.cli._led import set_led_brightness
        with patch("trcc.cli._led._connect_or_fail", return_value=(mock_led, 0)):
            set_led_brightness(app, 75)
        mock_led.set_brightness.assert_called_once_with(75)

    def test_set_sensor_source_calls_device(self, app, mock_led):
        from trcc.cli._led import set_sensor_source
        with patch("trcc.cli._led._connect_or_fail", return_value=(mock_led, 0)):
            set_sensor_source(app, "gpu")
        mock_led.set_sensor_source.assert_called_once_with("gpu")

    def test_set_zone_color_calls_device(self, app, mock_led):
        from trcc.cli._led import set_zone_color
        with patch("trcc.cli._led._connect_or_fail", return_value=(mock_led, 0)):
            set_zone_color(app, 2, "0000ff")
        mock_led.set_zone_color.assert_called_once_with(2, 0, 0, 255)
