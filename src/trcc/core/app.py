"""TrccApp — application singleton / DI container.

Single object all interfaces (CLI, GUI, API) observe and send messages to.
Initialized once via TrccApp.init(). Scans for devices, classifies them as
LCD or LED using PROTOCOL_TRAITS, builds the correct device object, and
hands it to callers. Composition roots import only TrccApp and Device types.

Observer pattern: interfaces register via register(observer). State changes
(device found/lost) notify all registered observers automatically.
"""
from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from ..services.system import SystemService
    from .builder import ControllerBuilder
    from .command_bus import CommandBus
    from .lcd_device import LCDDevice
    from .led_device import LEDDevice
    from .models import DetectedDevice
    from .ports import AutostartManager, Device, GetDiskInfoFn, GetMemoryInfoFn, PlatformSetup

log = logging.getLogger(__name__)


# ── Observer contract ────────────────────────────────────────────────────────

class AppEvent(Enum):
    DEVICES_CHANGED   = auto()  # device list rescanned
    DEVICE_CONNECTED  = auto()  # single device came online
    DEVICE_LOST       = auto()  # single device went offline
    METRICS_UPDATED   = auto()  # metrics polled — data is SystemMetrics


class AppObserver(ABC):
    """Implement and register with TrccApp to receive device/state events."""

    @abstractmethod
    def on_app_event(self, event: AppEvent, data: Any) -> None: ...


# ── Singleton / DI container ─────────────────────────────────────────────────

class TrccApp:
    """Application-wide DI container and singleton.

    One per process. Detects devices, classifies them via PROTOCOL_TRAITS,
    builds LCDDevice or LEDDevice, and hands them to callers. Composition
    roots import only TrccApp — they never import builder, services, or
    adapters directly.

    Typical usage in a composition root::

        app = TrccApp.init(verbosity=verbosity)
        devices = app.scan()           # list[Device] — LCD or LED, ready to use
        lcd = devices[0]               # LCDDevice or LEDDevice, caller checks type
    """

    _instance: TrccApp | None = None

    def __init__(self, builder: ControllerBuilder) -> None:
        self._builder = builder
        # path → Device (LCDDevice or LEDDevice, keyed by USB path)
        self._devices: dict[str, Device] = {}
        self._observers: list[AppObserver] = []
        self._system_svc: SystemService | None = None
        self._metrics_thread: threading.Thread | None = None
        self._metrics_stop: threading.Event = threading.Event()

    # ── Lifecycle ────────────────────────────────────────────────────────────

    @classmethod
    def init(cls, verbosity: int = 0) -> TrccApp:
        """Bootstrap the singleton: logging → OS adapter → settings.

        Safe to call multiple times — returns the existing instance after
        first call. Every composition root (CLI, GUI, API) calls this first.
        """
        if cls._instance is None:
            from .builder import ControllerBuilder
            builder = ControllerBuilder.bootstrap(verbosity)
            cls._instance = cls(builder)
            log.debug("TrccApp initialized")
        return cls._instance

    @classmethod
    def get(cls) -> TrccApp:
        """Return the singleton. Raises if init() was never called."""
        if cls._instance is None:
            raise RuntimeError(
                "TrccApp not initialized — call TrccApp.init() from a composition root.")
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton (tests only)."""
        cls._instance = None

    # ── Device scanning ──────────────────────────────────────────────────────

    def scan(self) -> list[Device]:
        """Detect hardware, build LCDDevice/LEDDevice per device, return all.

        Uses PROTOCOL_TRAITS.is_led to classify each DetectedDevice — no
        string checks in callers. Notifies observers with DEVICES_CHANGED.
        """
        detect_fn = self._builder.build_detect_fn()
        found: list[DetectedDevice] = detect_fn()

        self._devices = {}
        for detected in found:
            device = self._builder.build_device(detected)
            try:
                device.connect(detected)
            except Exception:
                log.warning("scan: connect failed for %s — skipping", detected.path)
                continue
            self._devices[detected.path] = device

        log.debug("scan: %d device(s) found", len(self._devices))
        self._notify(AppEvent.DEVICES_CHANGED, list(self._devices.values()))
        return list(self._devices.values())

    def device_connected(self, detected: DetectedDevice) -> None:
        """Build and register a newly connected device, notify observers."""
        device = self._builder.build_device(detected)
        self._devices[detected.path] = device
        self._notify(AppEvent.DEVICE_CONNECTED, device)

    def device_lost(self, path: str) -> None:
        """Remove a device by path and notify observers."""
        device = self._devices.pop(path, None)
        if device is not None:
            self._notify(AppEvent.DEVICE_LOST, device)

    @property
    def devices(self) -> list[Device]:
        """All currently known devices (snapshot). Call scan() first."""
        return list(self._devices.values())

    # ── DI: device construction ──────────────────────────────────────────────

    def build_lcd(self) -> LCDDevice:
        """Build an unconnected LCDDevice (auto-detects on connect())."""
        return self._builder.build_lcd()

    def build_led(self) -> LEDDevice:
        """Build an unconnected LEDDevice (auto-detects on connect())."""
        return self._builder.build_led()

    def lcd_from_service(self, device_svc: Any) -> LCDDevice:
        """Build an LCDDevice from an existing DeviceService (API standalone mode)."""
        return self._builder.lcd_from_service(device_svc)

    # ── System service + metrics loop ────────────────────────────────────────

    def set_system(self, system_svc: SystemService) -> None:
        """Inject the SystemService. Call before start_metrics_loop()."""
        self._system_svc = system_svc

    def start_metrics_loop(self, interval: float = 1.0) -> None:
        """Start background loop: poll metrics → push to all devices via tick().

        OS-blind — metrics come from SystemService (wraps OS SensorEnumerator).
        Composition roots call this once after scan().
        """
        if self._system_svc is None:
            raise RuntimeError(
                "TrccApp.set_system() must be called before start_metrics_loop().")
        self.stop_metrics_loop()
        self._metrics_stop.clear()

        def _loop() -> None:
            while not self._metrics_stop.is_set():
                try:
                    metrics = self._system_svc.all_metrics  # type: ignore[union-attr]
                    for device in list(self._devices.values()):
                        try:
                            device.update_metrics(metrics)
                            device.tick()
                        except Exception:
                            log.exception("Device update error: %s", device)
                    self._notify(AppEvent.METRICS_UPDATED, metrics)
                except Exception:
                    log.exception("Metrics poll error")
                self._metrics_stop.wait(interval)

        self._metrics_thread = threading.Thread(
            target=_loop, daemon=True, name="trcc-metrics")
        self._metrics_thread.start()
        log.debug("Metrics loop started (interval=%.1fs)", interval)

    def stop_metrics_loop(self) -> None:
        """Stop the background metrics loop."""
        self._metrics_stop.set()
        if self._metrics_thread and self._metrics_thread.is_alive():
            self._metrics_thread.join(timeout=3)
        self._metrics_thread = None
        self._metrics_stop.clear()

    # ── DI: infrastructure ───────────────────────────────────────────────────

    def build_system(self) -> SystemService:
        """Build a SystemService wired with OS-appropriate sensor enumerator."""
        return self._builder.build_system()

    def build_setup(self) -> PlatformSetup:
        """Return the OS-appropriate setup wizard."""
        return self._builder.build_setup()

    def build_autostart(self) -> AutostartManager:
        """Return the OS-appropriate autostart manager."""
        return self._builder.build_autostart()

    def build_hardware_fns(self) -> tuple[GetMemoryInfoFn, GetDiskInfoFn]:
        """Return (get_memory_info, get_disk_info) for the current OS."""
        return self._builder.build_hardware_fns()

    def set_renderer(self, renderer: Any) -> None:
        """Inject the renderer (QtRenderer) before building LCD devices."""
        self._builder.with_renderer(renderer)

    # ── CommandBus factories ─────────────────────────────────────────────────

    def build_lcd_bus(self, lcd: LCDDevice) -> CommandBus:
        """Return a CommandBus wired to lcd — logging + timing middleware.

        Suitable for CLI and API (commands arrive one at a time, no rate limit).
        """
        from .command_bus import (
            Command,
            CommandBus,
            CommandResult,
            LoggingMiddleware,
            TimingMiddleware,
        )
        from .commands.lcd import (
            EnableOverlayCommand,
            LoadThemeByNameCommand,
            SendColorCommand,
            SendImageCommand,
            SetBrightnessCommand,
            SetRotationCommand,
            SetSplitModeCommand,
            UpdateMetricsLCDCommand,
        )

        def _set_brightness(cmd: Command) -> CommandResult:
            return CommandResult.from_dict(lcd.set_brightness(cast(SetBrightnessCommand, cmd).level))

        def _set_rotation(cmd: Command) -> CommandResult:
            c = cast(SetRotationCommand, cmd)
            return CommandResult.from_dict(lcd.set_rotation(c.degrees))

        def _send_color(cmd: Command) -> CommandResult:
            c = cast(SendColorCommand, cmd)
            return CommandResult.from_dict(lcd.send_color(c.r, c.g, c.b))

        def _send_image(cmd: Command) -> CommandResult:
            return CommandResult.from_dict(lcd.send_image(cast(SendImageCommand, cmd).image_path))

        def _load_theme(cmd: Command) -> CommandResult:
            c = cast(LoadThemeByNameCommand, cmd)
            return CommandResult.from_dict(lcd.load_theme_by_name(c.name, c.width, c.height))

        def _set_split_mode(cmd: Command) -> CommandResult:
            return CommandResult.from_dict(lcd.set_split_mode(cast(SetSplitModeCommand, cmd).mode))

        def _enable_overlay(cmd: Command) -> CommandResult:
            return CommandResult.from_dict(lcd.enable_overlay(cast(EnableOverlayCommand, cmd).on))

        def _update_metrics(cmd: Command) -> CommandResult:
            return CommandResult.from_dict(lcd.update_metrics(cast(UpdateMetricsLCDCommand, cmd).metrics))

        return (CommandBus()
                .add_middleware(LoggingMiddleware())
                .add_middleware(TimingMiddleware(threshold_ms=200.0))
                .register(SetBrightnessCommand, _set_brightness)
                .register(SetRotationCommand, _set_rotation)
                .register(SendColorCommand, _send_color)
                .register(SendImageCommand, _send_image)
                .register(LoadThemeByNameCommand, _load_theme)
                .register(SetSplitModeCommand, _set_split_mode)
                .register(EnableOverlayCommand, _enable_overlay)
                .register(UpdateMetricsLCDCommand, _update_metrics))

    def build_lcd_gui_bus(self, lcd: LCDDevice) -> CommandBus:
        """Return a CommandBus wired for GUI — adds RateLimitMiddleware.

        GUI slider events fire continuously; rate limiting prevents USB saturation.
        The rate limit middleware is appended after logging/timing so skipped
        commands are still counted in timing but do not reach the handler.
        """
        from .command_bus import RateLimitMiddleware
        return self.build_lcd_bus(lcd).add_middleware(RateLimitMiddleware(min_interval_ms=50.0))

    def build_led_bus(self, led: LEDDevice) -> CommandBus:
        """Return a CommandBus wired to led — logging + timing middleware."""
        from .command_bus import (
            Command,
            CommandBus,
            CommandResult,
            LoggingMiddleware,
            TimingMiddleware,
        )
        from .commands.led import (
            SetLEDBrightnessCommand,
            SetLEDColorCommand,
            SetLEDModeCommand,
            SetLEDSensorSourceCommand,
            SetZoneColorCommand,
            ToggleLEDCommand,
            UpdateMetricsLEDCommand,
        )

        def _set_color(cmd: Command) -> CommandResult:
            c = cast(SetLEDColorCommand, cmd)
            return CommandResult.from_dict(led.set_color(c.r, c.g, c.b))

        def _set_mode(cmd: Command) -> CommandResult:
            return CommandResult.from_dict(led.set_mode(cast(SetLEDModeCommand, cmd).mode))

        def _set_brightness(cmd: Command) -> CommandResult:
            return CommandResult.from_dict(led.set_brightness(cast(SetLEDBrightnessCommand, cmd).level))

        def _toggle(cmd: Command) -> CommandResult:
            return CommandResult.from_dict(led.toggle_global(cast(ToggleLEDCommand, cmd).on))

        def _set_zone_color(cmd: Command) -> CommandResult:
            c = cast(SetZoneColorCommand, cmd)
            return CommandResult.from_dict(led.set_zone_color(c.zone, c.r, c.g, c.b))

        def _set_sensor_source(cmd: Command) -> CommandResult:
            return CommandResult.from_dict(led.set_sensor_source(cast(SetLEDSensorSourceCommand, cmd).source))

        def _update_metrics(cmd: Command) -> CommandResult:
            return CommandResult.from_dict(led.update_metrics(cast(UpdateMetricsLEDCommand, cmd).metrics))

        return (CommandBus()
                .add_middleware(LoggingMiddleware())
                .add_middleware(TimingMiddleware(threshold_ms=200.0))
                .register(SetLEDColorCommand, _set_color)
                .register(SetLEDModeCommand, _set_mode)
                .register(SetLEDBrightnessCommand, _set_brightness)
                .register(ToggleLEDCommand, _toggle)
                .register(SetZoneColorCommand, _set_zone_color)
                .register(SetLEDSensorSourceCommand, _set_sensor_source)
                .register(UpdateMetricsLEDCommand, _update_metrics))

    def build_led_gui_bus(self, led: LEDDevice) -> CommandBus:
        """Return a CommandBus wired for GUI LED — adds RateLimitMiddleware."""
        from .command_bus import RateLimitMiddleware
        return self.build_led_bus(led).add_middleware(RateLimitMiddleware(min_interval_ms=50.0))

    # ── Observer registration ────────────────────────────────────────────────

    def register(self, observer: AppObserver) -> None:
        self._observers.append(observer)

    def unregister(self, observer: AppObserver) -> None:
        self._observers = [o for o in self._observers if o is not observer]

    def _notify(self, event: AppEvent, data: Any) -> None:
        for obs in self._observers:
            try:
                obs.on_app_event(event, data)
            except Exception:
                log.exception("Observer %s raised on %s", obs, event)
