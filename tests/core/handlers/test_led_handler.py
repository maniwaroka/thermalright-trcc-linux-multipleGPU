"""Tests for core/handlers/led.py — LEDCommandHandler + LEDGuiCommandHandler + factories."""
from __future__ import annotations

from unittest.mock import MagicMock

from trcc.core.command_bus import CommandBus, RateLimitMiddleware
from trcc.core.commands.led import (
    SetClockFormatCommand,
    SetLEDBrightnessCommand,
    SetLEDColorCommand,
    SetLEDModeCommand,
    SetLEDSensorSourceCommand,
    SetZoneColorCommand,
    ToggleLEDCommand,
    ToggleSegmentCommand,
    ToggleZoneCommand,
    UpdateMetricsLEDCommand,
)
from trcc.core.handlers.led import (
    LEDCommandHandler,
    LEDGuiCommandHandler,
    build_led_bus,
    build_led_gui_bus,
)
from trcc.core.models import LEDMode


def _mock_led() -> MagicMock:
    led = MagicMock()
    led.set_color.return_value = {"success": True}
    led.set_mode.return_value = {"success": True}
    led.set_brightness.return_value = {"success": True}
    led.toggle_global.return_value = {"success": True}
    led.set_zone_color.return_value = {"success": True}
    led.set_sensor_source.return_value = {"success": True}
    led.update_metrics.return_value = {"success": True}
    led.toggle_segment.return_value = {"success": True}
    led.toggle_zone.return_value = {"success": True}
    led.set_clock_format.return_value = {"success": True}
    return led


# ── LEDCommandHandler dispatch ────────────────────────────────────────────────

class TestLEDCommandHandlerDispatch:
    def test_set_color(self):
        led = _mock_led()
        h = LEDCommandHandler(led)
        result = h(SetLEDColorCommand(r=0, g=255, b=0))
        assert result
        led.set_color.assert_called_once_with(0, 255, 0)

    def test_set_mode(self):
        led = _mock_led()
        h = LEDCommandHandler(led)
        h(SetLEDModeCommand(mode=LEDMode.STATIC))
        led.set_mode.assert_called_once_with(LEDMode.STATIC)

    def test_set_brightness(self):
        led = _mock_led()
        h = LEDCommandHandler(led)
        h(SetLEDBrightnessCommand(level=80))
        led.set_brightness.assert_called_once_with(80)

    def test_toggle_led(self):
        led = _mock_led()
        h = LEDCommandHandler(led)
        h(ToggleLEDCommand(on=False))
        led.toggle_global.assert_called_once_with(False)

    def test_set_zone_color(self):
        led = _mock_led()
        h = LEDCommandHandler(led)
        h(SetZoneColorCommand(zone=1, r=255, g=0, b=0))
        led.set_zone_color.assert_called_once_with(1, 255, 0, 0)

    def test_set_sensor_source(self):
        led = _mock_led()
        h = LEDCommandHandler(led)
        h(SetLEDSensorSourceCommand(source="gpu"))
        led.set_sensor_source.assert_called_once_with("gpu")

    def test_toggle_segment(self):
        led = _mock_led()
        h = LEDCommandHandler(led)
        h(ToggleSegmentCommand(index=2, on=True))
        led.toggle_segment.assert_called_once_with(2, True)

    def test_toggle_zone(self):
        led = _mock_led()
        h = LEDCommandHandler(led)
        h(ToggleZoneCommand(zone=0, on=False))
        led.toggle_zone.assert_called_once_with(0, False)

    def test_set_clock_format(self):
        led = _mock_led()
        h = LEDCommandHandler(led)
        h(SetClockFormatCommand(is_24h=False))
        led.set_clock_format.assert_called_once_with(False)


# ── Metrics validation — symmetric with LCD ───────────────────────────────────

class TestLEDMetricsValidation:
    def test_valid_metrics_dispatched(self):
        led = _mock_led()
        h = LEDCommandHandler(led)
        metrics = {"cpu_temp": 60}
        result = h(UpdateMetricsLEDCommand(metrics=metrics))
        assert result
        led.update_metrics.assert_called_once_with(metrics)

    def test_none_metrics_returns_fail(self):
        led = _mock_led()
        h = LEDCommandHandler(led)
        result = h(UpdateMetricsLEDCommand(metrics=None))
        assert not result
        led.update_metrics.assert_not_called()

    def test_none_metrics_error_message(self):
        h = LEDCommandHandler(_mock_led())
        result = h(UpdateMetricsLEDCommand(metrics=None))
        assert "invalid metrics" in result.payload["error"]


# ── LEDGuiCommandHandler ──────────────────────────────────────────────────────

class TestLEDGuiCommandHandler:
    def test_color_calls_update_not_set(self):
        led = _mock_led()
        h = LEDGuiCommandHandler(led)
        h(SetLEDColorCommand(r=128, g=128, b=128))
        led.update_color.assert_called_once_with(128, 128, 128)
        led.set_color.assert_not_called()

    def test_brightness_calls_update(self):
        led = _mock_led()
        h = LEDGuiCommandHandler(led)
        h(SetLEDBrightnessCommand(level=75))
        led.update_brightness.assert_called_once_with(75)

    def test_mode_calls_update(self):
        led = _mock_led()
        h = LEDGuiCommandHandler(led)
        h(SetLEDModeCommand(mode=LEDMode.BREATHING))
        led.update_mode.assert_called_once_with(LEDMode.BREATHING)

    def test_unregistered_command_returns_fail(self):
        led = _mock_led()
        h = LEDGuiCommandHandler(led)
        result = h(ToggleLEDCommand(on=True))
        assert not result


# ── bus factories ─────────────────────────────────────────────────────────────

class TestBuildLedBus:
    def test_returns_command_bus(self):
        assert isinstance(build_led_bus(_mock_led()), CommandBus)

    def test_no_rate_limit(self):
        assert RateLimitMiddleware not in build_led_bus(_mock_led())

    def test_dispatches_set_color(self):
        led = _mock_led()
        build_led_bus(led).dispatch(SetLEDColorCommand(r=0, g=255, b=0))
        led.set_color.assert_called_once_with(0, 255, 0)

    def test_all_handles_registered(self):
        led = _mock_led()
        bus = build_led_bus(led)
        bus.dispatch(SetLEDBrightnessCommand(level=50))
        bus.dispatch(ToggleLEDCommand(on=True))
        bus.dispatch(SetLEDSensorSourceCommand(source="cpu"))
        led.set_brightness.assert_called_once_with(50)
        led.toggle_global.assert_called_once_with(True)
        led.set_sensor_source.assert_called_once_with("cpu")


class TestBuildLedGuiBus:
    def test_has_rate_limit(self):
        assert RateLimitMiddleware in build_led_gui_bus(_mock_led())

    def test_dispatches_update_color(self):
        led = _mock_led()
        build_led_gui_bus(led).dispatch(SetLEDColorCommand(r=128, g=128, b=128))
        led.update_color.assert_called_once_with(128, 128, 128)
        led.set_color.assert_not_called()

    def test_dispatches_update_brightness(self):
        led = _mock_led()
        build_led_gui_bus(led).dispatch(SetLEDBrightnessCommand(level=75))
        led.update_brightness.assert_called_once_with(75)

    def test_dispatches_update_mode(self):
        led = _mock_led()
        build_led_gui_bus(led).dispatch(SetLEDModeCommand(mode=LEDMode.BREATHING))
        led.update_mode.assert_called_once_with(LEDMode.BREATHING)


# ── __repr__ ──────────────────────────────────────────────────────────────────

class TestLEDHandlerRepr:
    def test_repr_includes_class_name(self):
        assert "LEDCommandHandler" in repr(LEDCommandHandler(_mock_led()))

    def test_gui_repr_includes_class_name(self):
        assert "LEDGuiCommandHandler" in repr(LEDGuiCommandHandler(_mock_led()))
