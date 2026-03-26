"""LED command handlers — driving port boundary for all LED operations.

Two handlers:
  - LEDCommandHandler     — full LED bus (CLI / API / hotplug)
  - LEDGuiCommandHandler  — GUI-only bus: calls update_* (state-only) instead
                            of set_* (immediate send). The 150ms animation tick
                            handles sending so sliders don't saturate the bus.

build_led_bus / build_led_gui_bus are the only public entry points.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from ..command_bus import (
    Command,
    CommandBus,
    CommandResult,
    LoggingMiddleware,
    RateLimitMiddleware,
    TimingMiddleware,
)
from ..commands.led import (
    SetClockFormatCommand,
    SetLEDBrightnessCommand,
    SetLEDColorCommand,
    SetLEDModeCommand,
    SetLEDSensorSourceCommand,
    SetTempUnitLEDCommand,
    SetZoneBrightnessCommand,
    SetZoneColorCommand,
    SetZoneModeCommand,
    SetZoneSyncCommand,
    ToggleLEDCommand,
    ToggleSegmentCommand,
    ToggleZoneCommand,
    UpdateMetricsLEDCommand,
)
from .base import DeviceCommandHandler

if TYPE_CHECKING:
    from ..led_device import LEDDevice


class LEDCommandHandler(DeviceCommandHandler):
    """Handles all LED commands — one __call__, one match statement.

    Closes over led. Calls set_* methods (immediate hardware send).
    UpdateMetricsLEDCommand validates data via _validate_metrics() — symmetric
    with LCDCommandHandler, same contract, same base method.
    """

    __slots__ = ('_led',)

    handles: ClassVar[tuple[type[Command], ...]] = (
        SetLEDColorCommand,
        SetLEDModeCommand,
        SetLEDBrightnessCommand,
        ToggleLEDCommand,
        SetZoneColorCommand,
        SetZoneModeCommand,
        SetZoneBrightnessCommand,
        ToggleZoneCommand,
        SetZoneSyncCommand,
        ToggleSegmentCommand,
        SetClockFormatCommand,
        SetTempUnitLEDCommand,
        SetLEDSensorSourceCommand,
        UpdateMetricsLEDCommand,
    )

    def __init__(self, led: LEDDevice) -> None:
        self._led = led

    def __call__(self, cmd: Command) -> CommandResult:
        match cmd:
            case SetLEDColorCommand(r=r, g=g, b=b):
                return CommandResult.from_dict(self._led.set_color(r, g, b))

            case SetLEDModeCommand(mode=mode):
                return CommandResult.from_dict(self._led.set_mode(mode))

            case SetLEDBrightnessCommand(level=level):
                return CommandResult.from_dict(self._led.set_brightness(level))

            case ToggleLEDCommand(on=on):
                return CommandResult.from_dict(self._led.toggle_global(on))

            case SetZoneColorCommand(zone=zone, r=r, g=g, b=b):
                return CommandResult.from_dict(
                    self._led.set_zone_color(zone, r, g, b))

            case SetZoneModeCommand(zone=zone, mode=mode):
                return CommandResult.from_dict(
                    self._led.set_zone_mode(zone, mode))

            case SetZoneBrightnessCommand(zone=zone, level=level):
                return CommandResult.from_dict(
                    self._led.set_zone_brightness(zone, level))

            case ToggleZoneCommand(zone=zone, on=on):
                return CommandResult.from_dict(self._led.toggle_zone(zone, on))

            case SetZoneSyncCommand(enabled=enabled, interval=interval):
                return CommandResult.from_dict(
                    self._led.set_zone_sync(enabled, interval))

            case ToggleSegmentCommand(index=index, on=on):
                return CommandResult.from_dict(
                    self._led.toggle_segment(index, on))

            case SetClockFormatCommand(is_24h=is_24h):
                return CommandResult.from_dict(self._led.set_clock_format(is_24h))

            case SetTempUnitLEDCommand(unit=unit):
                return CommandResult.from_dict(self._led.set_temp_unit(unit))

            case SetLEDSensorSourceCommand(source=source):
                return CommandResult.from_dict(self._led.set_sensor_source(source))

            case UpdateMetricsLEDCommand(metrics=metrics):
                if not self._validate_metrics(metrics):
                    return CommandResult.fail(
                        "invalid metrics: expected non-None value")
                return CommandResult.from_dict(self._led.update_metrics(metrics))

            case _:
                return CommandResult.fail(
                    f"BUG: unhandled LED command {type(cmd).__name__}")

    def __repr__(self) -> str:
        return f"LEDCommandHandler(led={self._led!r})"


class LEDGuiCommandHandler(DeviceCommandHandler):
    """GUI-only LED handler — calls update_* (state-only) instead of set_*.

    The 150ms animation tick handles sending to hardware. Sliders dispatch
    here so rapid movements update state without saturating the USB bus.
    Only handles the three slider commands; all others fall through to fail.
    """

    __slots__ = ('_led',)

    def __init__(self, led: LEDDevice) -> None:
        self._led = led

    def __call__(self, cmd: Command) -> CommandResult:
        match cmd:
            case SetLEDColorCommand(r=r, g=g, b=b):
                self._led.update_color(r, g, b)
                return CommandResult.ok(message="color updated")

            case SetLEDBrightnessCommand(level=level):
                self._led.update_brightness(level)
                return CommandResult.ok(message="brightness updated")

            case SetLEDModeCommand(mode=mode):
                self._led.update_mode(mode)
                return CommandResult.ok(message="mode updated")

            case _:
                return CommandResult.fail(
                    f"BUG: unhandled GUI LED command {type(cmd).__name__}")

    def __repr__(self) -> str:
        return f"LEDGuiCommandHandler(led={self._led!r})"


def build_led_bus(led: LEDDevice) -> CommandBus:
    """Build a CommandBus for CLI/API LED operations.

    Logging + timing middleware. One handler instance auto-registered
    across all command types in LEDCommandHandler.handles.
    """
    h = LEDCommandHandler(led)
    bus = (CommandBus()
           .add_middleware(LoggingMiddleware())
           .add_middleware(TimingMiddleware(threshold_ms=200.0)))
    for cmd_type in LEDCommandHandler.handles:
        bus.register(cmd_type, h)
    return bus


def build_led_gui_bus(led: LEDDevice) -> CommandBus:
    """Build a CommandBus for GUI LED slider operations.

    Uses LEDGuiCommandHandler (state-only update_* calls) with RateLimitMiddleware
    to prevent USB saturation from rapid slider movement.
    """
    h = LEDGuiCommandHandler(led)
    return (CommandBus()
            .add_middleware(LoggingMiddleware())
            .add_middleware(TimingMiddleware(threshold_ms=200.0))
            .add_middleware(RateLimitMiddleware(min_interval_ms=50.0))
            .register(SetLEDColorCommand, h)
            .register(SetLEDBrightnessCommand, h)
            .register(SetLEDModeCommand, h))
