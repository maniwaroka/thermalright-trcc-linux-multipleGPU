"""LED command handlers — driving port boundary for all LED operations.

Two handlers:
  - LEDCommandHandler     — full LED bus (CLI / API / hotplug)
  - LEDGuiCommandHandler  — GUI-only bus: calls update_* (state-only) instead
                            of set_* (immediate send). The 150ms animation tick
                            handles sending so sliders don't saturate the bus.

build_led_bus / build_led_gui_bus are the only public entry points.
"""
from __future__ import annotations

import logging
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
    SelectZoneCommand,
    SetClockFormatCommand,
    SetDiskIndexCommand,
    SetLEDBrightnessCommand,
    SetLEDColorCommand,
    SetLEDModeCommand,
    SetLEDSensorSourceCommand,
    SetMemoryRatioCommand,
    SetTempUnitLEDCommand,
    SetTestModeCommand,
    SetWeekStartCommand,
    SetZoneBrightnessCommand,
    SetZoneColorCommand,
    SetZoneModeCommand,
    SetZoneSyncCommand,
    SetZoneSyncIntervalCommand,
    SetZoneSyncZoneCommand,
    ToggleLEDCommand,
    ToggleSegmentCommand,
    ToggleZoneCommand,
    UpdateMetricsLEDCommand,
)
from .base import DeviceCommandHandler

if TYPE_CHECKING:
    from ..led_device import LEDDevice

log = logging.getLogger(__name__)


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

    The 150ms animation tick handles sending to hardware. Every LED mutation
    dispatched from the GUI arrives here so the bus middleware (logging,
    rate-limiting) applies uniformly. update_* updates in-memory state only;
    the animation tick reads that state and sends to hardware at 150ms intervals.
    """

    __slots__ = ('_led',)

    def __init__(self, led: LEDDevice) -> None:
        self._led = led

    def __call__(self, cmd: Command) -> CommandResult:  # noqa: C901
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

            case ToggleLEDCommand(on=on):
                self._led.update_global_on(on)
                return CommandResult.ok(message=f"global on={on}")

            case ToggleSegmentCommand(index=index, on=on):
                self._led.update_segment(index, on)
                return CommandResult.ok(message=f"segment {index} on={on}")

            case SelectZoneCommand(zone=zone):
                self._led.update_selected_zone(zone)
                return CommandResult.ok(message=f"zone selected={zone}")

            case ToggleZoneCommand(zone=zone, on=on):
                self._led.update_zone_on(zone, on)
                return CommandResult.ok(message=f"zone {zone} on={on}")

            case SetZoneModeCommand(zone=zone, mode=mode):
                self._led.update_zone_mode(zone, mode)
                return CommandResult.ok(message=f"zone {zone} mode={mode}")

            case SetZoneColorCommand(zone=zone, r=r, g=g, b=b):
                self._led.update_zone_color(zone, r, g, b)
                return CommandResult.ok(message=f"zone {zone} color updated")

            case SetZoneBrightnessCommand(zone=zone, level=level):
                self._led.update_zone_brightness(zone, level)
                return CommandResult.ok(message=f"zone {zone} brightness={level}")

            case SetZoneSyncCommand(enabled=enabled):
                self._led.update_zone_sync(enabled)
                return CommandResult.ok(message=f"zone sync={enabled}")

            case SetZoneSyncZoneCommand(zi=zi, sel=sel):
                self._led.update_zone_sync_zone(zi, sel)
                return CommandResult.ok(message=f"sync zone {zi} sel={sel}")

            case SetZoneSyncIntervalCommand(secs=secs):
                self._led.update_zone_sync_interval(secs)
                return CommandResult.ok(message=f"sync interval={secs}s")

            case SetClockFormatCommand(is_24h=is_24h):
                self._led.update_clock_format(is_24h)
                return CommandResult.ok(message=f"clock 24h={is_24h}")

            case SetWeekStartCommand(is_sun=is_sun):
                self._led.update_week_start(is_sun)
                return CommandResult.ok(message=f"week start sun={is_sun}")

            case SetDiskIndexCommand(idx=idx):
                self._led.update_disk_index(idx)
                return CommandResult.ok(message=f"disk index={idx}")

            case SetMemoryRatioCommand(ratio=ratio):
                self._led.update_memory_ratio(ratio)
                return CommandResult.ok(message=f"memory ratio={ratio}")

            case SetTestModeCommand(on=on):
                self._led.update_test_mode(on)
                return CommandResult.ok(message=f"test mode={on}")

            case SetTempUnitLEDCommand(unit=unit):
                self._led.set_seg_temp_unit(unit)
                return CommandResult.ok(message=f"temp unit={unit}")

            case UpdateMetricsLEDCommand(metrics=metrics):
                if not self._validate_metrics(metrics):
                    return CommandResult.fail(
                        "invalid metrics: expected non-None value")
                self._led.update_metrics(metrics)
                return CommandResult.ok(message="metrics updated")

            case _:
                return CommandResult.fail(
                    f"BUG: unhandled GUI LED command {type(cmd).__name__}")

    def __repr__(self) -> str:
        return f"LEDGuiCommandHandler(led={self._led!r})"


# All commands routed through the GUI bus (state-only update_* path)
_GUI_COMMANDS: tuple[type[Command], ...] = (
    SetLEDColorCommand,
    SetLEDBrightnessCommand,
    SetLEDModeCommand,
    ToggleLEDCommand,
    ToggleSegmentCommand,
    SelectZoneCommand,
    ToggleZoneCommand,
    SetZoneModeCommand,
    SetZoneColorCommand,
    SetZoneBrightnessCommand,
    SetZoneSyncCommand,
    SetZoneSyncZoneCommand,
    SetZoneSyncIntervalCommand,
    SetClockFormatCommand,
    SetWeekStartCommand,
    SetDiskIndexCommand,
    SetMemoryRatioCommand,
    SetTestModeCommand,
    SetTempUnitLEDCommand,
    UpdateMetricsLEDCommand,
)


def build_led_bus(led: LEDDevice) -> CommandBus:
    """Build a CommandBus for CLI/API LED operations.

    Logging + timing middleware. One handler instance auto-registered
    across all command types in LEDCommandHandler.handles.
    """
    log.debug("build_led_bus: led=%r", led)
    h = LEDCommandHandler(led)
    bus = (CommandBus()
           .add_middleware(LoggingMiddleware())
           .add_middleware(TimingMiddleware(threshold_ms=200.0)))
    for cmd_type in LEDCommandHandler.handles:
        bus.register(cmd_type, h)
    return bus


def build_led_gui_bus(led: LEDDevice) -> CommandBus:
    """Build a CommandBus for GUI LED operations.

    Uses LEDGuiCommandHandler (state-only update_* calls) with RateLimitMiddleware
    to prevent USB saturation from rapid slider movement. Covers all LED commands
    so every GUI mutation goes through the same bus as CLI/API.
    """
    log.debug("build_led_gui_bus: led=%r", led)
    h = LEDGuiCommandHandler(led)
    bus = (CommandBus()
           .add_middleware(LoggingMiddleware())
           .add_middleware(TimingMiddleware(threshold_ms=200.0))
           .add_middleware(RateLimitMiddleware(min_interval_ms=50.0)))
    for cmd_type in _GUI_COMMANDS:
        bus.register(cmd_type, h)
    return bus
