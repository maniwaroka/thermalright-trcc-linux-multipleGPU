"""Tests for TrccApp bus factory methods — Phase 2."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from trcc.core.app import TrccApp
from trcc.core.command_bus import (
    CommandBus,
    LoggingMiddleware,
    RateLimitMiddleware,
    TimingMiddleware,
)
from trcc.core.commands.lcd import (
    EnableOverlayCommand,
    LoadThemeByNameCommand,
    SendColorCommand,
    SendImageCommand,
    SetBrightnessCommand,
    SetRotationCommand,
    SetSplitModeCommand,
    UpdateMetricsLCDCommand,
)
from trcc.core.commands.led import (
    SetLEDBrightnessCommand,
    SetLEDColorCommand,
    SetLEDModeCommand,
    SetLEDSensorSourceCommand,
    SetZoneColorCommand,
    ToggleLEDCommand,
    UpdateMetricsLEDCommand,
)
from trcc.core.models import LEDMode


@pytest.fixture()
def app():
    """Fresh TrccApp backed by a mock builder."""
    TrccApp.reset()
    inst = TrccApp(MagicMock())
    TrccApp._instance = inst
    yield inst
    TrccApp.reset()


def _mock_lcd():
    lcd = MagicMock()
    lcd.set_brightness.return_value = {"success": True}
    lcd.set_rotation.return_value = {"success": True}
    lcd.send_color.return_value = {"success": True}
    lcd.send_image.return_value = {"success": True}
    lcd.load_theme_by_name.return_value = {"success": True}
    lcd.set_split_mode.return_value = {"success": True}
    lcd.enable_overlay.return_value = {"success": True}
    lcd.update_metrics.return_value = {"success": True}
    return lcd


def _mock_led():
    led = MagicMock()
    led.set_color.return_value = {"success": True}
    led.set_mode.return_value = {"success": True}
    led.set_brightness.return_value = {"success": True}
    led.toggle_global.return_value = {"success": True}
    led.set_zone_color.return_value = {"success": True}
    led.set_sensor_source.return_value = {"success": True}
    led.update_metrics.return_value = {"success": True}
    return led


# ── build_lcd_bus ────────────────────────────────────────────────────────────

class TestBuildLcdBus:
    def test_returns_command_bus(self, app):
        assert isinstance(app.build_lcd_bus(_mock_lcd()), CommandBus)

    def test_has_logging_middleware(self, app):
        bus = app.build_lcd_bus(_mock_lcd())
        assert any(isinstance(m, LoggingMiddleware) for m in bus._middleware)

    def test_has_timing_middleware(self, app):
        bus = app.build_lcd_bus(_mock_lcd())
        assert any(isinstance(m, TimingMiddleware) for m in bus._middleware)

    def test_no_rate_limit_middleware(self, app):
        bus = app.build_lcd_bus(_mock_lcd())
        assert not any(isinstance(m, RateLimitMiddleware) for m in bus._middleware)

    def test_dispatches_set_brightness(self, app):
        lcd = _mock_lcd()
        app.build_lcd_bus(lcd).dispatch(SetBrightnessCommand(level=2))
        lcd.set_brightness.assert_called_once_with(2)

    def test_dispatches_set_rotation(self, app):
        lcd = _mock_lcd()
        app.build_lcd_bus(lcd).dispatch(SetRotationCommand(degrees=90))
        lcd.set_rotation.assert_called_once_with(90)

    def test_dispatches_send_color(self, app):
        lcd = _mock_lcd()
        app.build_lcd_bus(lcd).dispatch(SendColorCommand(r=255, g=0, b=0))
        lcd.send_color.assert_called_once_with(255, 0, 0)

    def test_dispatches_send_image(self, app):
        lcd = _mock_lcd()
        app.build_lcd_bus(lcd).dispatch(SendImageCommand(image_path="/tmp/x.png"))
        lcd.send_image.assert_called_once_with("/tmp/x.png")

    def test_dispatches_load_theme_by_name(self, app):
        lcd = _mock_lcd()
        app.build_lcd_bus(lcd).dispatch(LoadThemeByNameCommand(name="Theme1", width=320, height=320))
        lcd.load_theme_by_name.assert_called_once_with("Theme1", 320, 320)

    def test_dispatches_set_split_mode(self, app):
        lcd = _mock_lcd()
        app.build_lcd_bus(lcd).dispatch(SetSplitModeCommand(mode=1))
        lcd.set_split_mode.assert_called_once_with(1)

    def test_dispatches_enable_overlay(self, app):
        lcd = _mock_lcd()
        app.build_lcd_bus(lcd).dispatch(EnableOverlayCommand(on=True))
        lcd.enable_overlay.assert_called_once_with(True)

    def test_dispatches_update_metrics(self, app):
        lcd = _mock_lcd()
        metrics = MagicMock()
        app.build_lcd_bus(lcd).dispatch(UpdateMetricsLCDCommand(metrics=metrics))
        lcd.update_metrics.assert_called_once_with(metrics)


# ── build_lcd_gui_bus ────────────────────────────────────────────────────────

class TestBuildLcdGuiBus:
    def test_has_rate_limit_middleware(self, app):
        bus = app.build_lcd_gui_bus(_mock_lcd())
        assert any(isinstance(m, RateLimitMiddleware) for m in bus._middleware)

    def test_also_has_logging_and_timing(self, app):
        bus = app.build_lcd_gui_bus(_mock_lcd())
        assert any(isinstance(m, LoggingMiddleware) for m in bus._middleware)
        assert any(isinstance(m, TimingMiddleware) for m in bus._middleware)

    def test_dispatches_set_brightness(self, app):
        lcd = _mock_lcd()
        app.build_lcd_gui_bus(lcd).dispatch(SetBrightnessCommand(level=1))
        lcd.set_brightness.assert_called_once_with(1)


# ── build_led_bus ────────────────────────────────────────────────────────────

class TestBuildLedBus:
    def test_returns_command_bus(self, app):
        assert isinstance(app.build_led_bus(_mock_led()), CommandBus)

    def test_no_rate_limit_middleware(self, app):
        bus = app.build_led_bus(_mock_led())
        assert not any(isinstance(m, RateLimitMiddleware) for m in bus._middleware)

    def test_dispatches_set_led_color(self, app):
        led = _mock_led()
        app.build_led_bus(led).dispatch(SetLEDColorCommand(r=0, g=255, b=0))
        led.set_color.assert_called_once_with(0, 255, 0)

    def test_dispatches_set_led_mode(self, app):
        led = _mock_led()
        app.build_led_bus(led).dispatch(SetLEDModeCommand(mode=LEDMode.STATIC))
        led.set_mode.assert_called_once_with(LEDMode.STATIC)

    def test_dispatches_set_led_brightness(self, app):
        led = _mock_led()
        app.build_led_bus(led).dispatch(SetLEDBrightnessCommand(level=80))
        led.set_brightness.assert_called_once_with(80)

    def test_dispatches_toggle_led(self, app):
        led = _mock_led()
        app.build_led_bus(led).dispatch(ToggleLEDCommand(on=False))
        led.toggle_global.assert_called_once_with(False)

    def test_dispatches_set_zone_color(self, app):
        led = _mock_led()
        app.build_led_bus(led).dispatch(SetZoneColorCommand(zone=1, r=255, g=0, b=0))
        led.set_zone_color.assert_called_once_with(1, 255, 0, 0)

    def test_dispatches_set_sensor_source(self, app):
        led = _mock_led()
        app.build_led_bus(led).dispatch(SetLEDSensorSourceCommand(source="gpu"))
        led.set_sensor_source.assert_called_once_with("gpu")

    def test_dispatches_update_metrics_led(self, app):
        led = _mock_led()
        metrics = MagicMock()
        app.build_led_bus(led).dispatch(UpdateMetricsLEDCommand(metrics=metrics))
        led.update_metrics.assert_called_once_with(metrics)


# ── build_led_gui_bus ────────────────────────────────────────────────────────

class TestBuildLedGuiBus:
    def test_has_rate_limit_middleware(self, app):
        bus = app.build_led_gui_bus(_mock_led())
        assert any(isinstance(m, RateLimitMiddleware) for m in bus._middleware)

    def test_dispatches_set_led_color(self, app):
        led = _mock_led()
        app.build_led_gui_bus(led).dispatch(SetLEDColorCommand(r=128, g=128, b=128))
        led.set_color.assert_called_once_with(128, 128, 128)
