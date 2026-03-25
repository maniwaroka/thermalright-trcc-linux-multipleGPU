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
    from .ports import (
        AutostartManager,
        Device,
        FindActiveFn,
        GetDiskInfoFn,
        GetMemoryInfoFn,
        PlatformSetup,
        ProxyFactoryFn,
    )

log = logging.getLogger(__name__)


# ── Observer contract ────────────────────────────────────────────────────────

class AppEvent(Enum):
    DEVICES_CHANGED   = auto()  # device list rescanned
    DEVICE_CONNECTED  = auto()  # single device came online
    FRAME_RENDERED    = auto()  # overlay frame rendered — data is {'path': str, 'image': Any}
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
        # Active buses — built when a device connects, cleared when it disconnects
        self._os_bus: CommandBus | None = None
        self._lcd_bus: CommandBus | None = None
        self._led_bus: CommandBus | None = None
        self._lcd_device: LCDDevice | None = None
        self._led_device: LEDDevice | None = None
        # IPC handlers — injected by composition roots (CLI/API/GUI entry points)
        self._find_active_fn: FindActiveFn | None = None
        self._proxy_factory_fn: ProxyFactoryFn | None = None

    # ── Lifecycle ────────────────────────────────────────────────────────────

    @classmethod
    def init(cls) -> TrccApp:
        """Create the singleton and wire the OS command bus.

        Intentionally minimal — no platform bootstrapping here. Composition
        roots dispatch InitPlatformCommand immediately after to do real init:
            logging → OS setup → settings → renderer.
        Then DiscoverDevicesCommand to find hardware.
        """
        if cls._instance is None:
            from .builder import ControllerBuilder
            builder = ControllerBuilder.for_current_os()
            cls._instance = cls(builder)
            cls._instance._os_bus = cls._instance.build_os_bus()
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
        """Detect hardware, build and connect LCDDevice/LEDDevice, store buses.

        init() → OS → scan() is the correct sequence:
          - LCD found → lcd_bus built and stored
          - LED found → led_bus built and stored
          - Notifies observers with DEVICES_CHANGED
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
            self._wire_bus(device)

        log.debug("scan: %d device(s) found", len(self._devices))
        self._notify(AppEvent.DEVICES_CHANGED, list(self._devices.values()))
        return list(self._devices.values())

    def device_connected(self, detected: DetectedDevice) -> None:
        """Build, connect, and register a newly discovered device, notify observers."""
        device = self._builder.build_device(detected)
        try:
            device.connect(detected)
        except Exception:
            log.warning("device_connected: connect failed for %s", detected.path)
            return
        self._devices[detected.path] = device
        self._wire_bus(device)
        self._notify(AppEvent.DEVICE_CONNECTED, device)

    def device_lost(self, path: str) -> None:
        """Remove a device by path, clear its bus, and notify observers."""
        device = self._devices.pop(path, None)
        if device is not None:
            if device.is_lcd:
                self._lcd_bus = None
                self._lcd_device = None
            elif device.is_led:
                self._led_bus = None
                self._led_device = None
            self._notify(AppEvent.DEVICE_LOST, device)

    def _wire_bus(self, device: Device) -> None:
        """Build and store the appropriate bus for a newly connected device.

        IPC handlers (set via set_ipc_handlers) are injected here so that
        devices built by scan() can proxy to a running GUI/API instance.
        """
        from .lcd_device import LCDDevice as _LCD
        from .led_device import LEDDevice as _LED
        if device.is_lcd and isinstance(device, _LCD):
            if self._find_active_fn is not None:
                device._find_active_fn = self._find_active_fn
            if self._proxy_factory_fn is not None:
                device._proxy_factory_fn = self._proxy_factory_fn
            self._lcd_device = device
            self._lcd_bus = self.build_lcd_bus(device)
            log.debug("lcd_bus ready for %s", getattr(device, 'device_path', '?'))
        elif device.is_led and isinstance(device, _LED):
            self._led_device = device
            self._led_bus = self.build_led_bus(device)
            log.debug("led_bus ready for %s", getattr(device, 'device_path', '?'))

    @property
    def devices(self) -> list[Device]:
        """All currently known devices (snapshot). Call scan() first."""
        return list(self._devices.values())

    @property
    def has_lcd(self) -> bool:
        """True if an LCD device is connected and its bus is ready."""
        return self._lcd_bus is not None

    @property
    def has_led(self) -> bool:
        """True if an LED device is connected and its bus is ready."""
        return self._led_bus is not None

    @property
    def lcd_bus(self) -> CommandBus:
        """CommandBus for LCD operations. Raises if no LCD device connected.

        Sequence: TrccApp.init() → scan() → lcd_bus.dispatch(command)
        """
        if self._lcd_bus is None:
            raise RuntimeError(
                "No LCD device connected. Call scan() first.")
        return self._lcd_bus

    @property
    def led_bus(self) -> CommandBus:
        """CommandBus for LED operations. Raises if no LED device connected.

        Sequence: TrccApp.init() → scan() → led_bus.dispatch(command)
        """
        if self._led_bus is None:
            raise RuntimeError(
                "No LED device connected. Call scan() first.")
        return self._led_bus

    @property
    def os_bus(self) -> CommandBus:
        """CommandBus for OS/platform operations (connect, discover, init).

        Available immediately after TrccApp.init() — no device needed.
        """
        if self._os_bus is None:
            self._os_bus = self.build_os_bus()
        return self._os_bus

    def build_os_bus(self) -> CommandBus:
        """Return a CommandBus wired to OS/platform handlers.

        Handles: InitPlatformCommand, DiscoverDevicesCommand.

        DiscoverDevicesCommand is the single OS command — it calls scan()
        which detects hardware, classifies by VID:PID/protocol, connects each
        device, and wires lcd_bus or led_bus.  After dispatch, check has_lcd /
        has_led.  Optional path field restricts connection to one device path.
        """
        from .command_bus import (
            Command,
            CommandBus,
            CommandResult,
            LoggingMiddleware,
            TimingMiddleware,
        )
        from .commands.initialize import DiscoverDevicesCommand, InitPlatformCommand

        def _init_platform(cmd: Command) -> CommandResult:
            verbosity = getattr(cmd, 'verbosity', 0)
            renderer_factory = getattr(cmd, 'renderer_factory', None)
            self._builder.bootstrap(verbosity)
            if renderer_factory is not None:
                self.set_renderer(renderer_factory())
            return CommandResult.ok(message="platform ready")

        def _discover(cmd: Command) -> CommandResult:
            path = getattr(cmd, 'path', None)
            devices = self.scan()
            if path and path not in self._devices:
                return CommandResult.fail(f"Device not found: {path}")
            return CommandResult.ok(
                message=f"{len(devices)} device(s) found",
                devices=[getattr(d, 'device_path', str(d)) for d in devices],
            )

        return (CommandBus()
                .add_middleware(LoggingMiddleware())
                .add_middleware(TimingMiddleware(threshold_ms=5000.0))
                .register(InitPlatformCommand, _init_platform)
                .register(DiscoverDevicesCommand, _discover))

    # ── DI: device construction ──────────────────────────────────────────────

    def set_ipc_handlers(
        self,
        find_active_fn: FindActiveFn,
        proxy_factory_fn: ProxyFactoryFn,
    ) -> None:
        """Inject IPC handlers for multi-instance routing.

        Call from composition roots (CLI, API, GUI) before dispatching
        DiscoverDevicesCommand.  Handlers are injected into each device in
        _wire_bus() so devices can detect a running instance and proxy commands.
        """
        self._find_active_fn = find_active_fn
        self._proxy_factory_fn = proxy_factory_fn

    @property
    def lcd_device(self) -> LCDDevice | None:
        """The active LCD device, or None if not connected."""
        return self._lcd_device

    @property
    def led_device(self) -> LEDDevice | None:
        """The active LED device, or None if not connected."""
        return self._led_device

    def build_led(self) -> LEDDevice:
        """Build an unconnected LEDDevice (for IPC server use only).

        Composition roots that need a direct device reference (e.g. IPC server
        wiring) may call this.  Normal device access goes through scan() →
        DiscoverDevicesCommand → led_device.
        """
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
                    for path, device in list(self._devices.items()):
                        try:
                            device.update_metrics(metrics)
                            image = device.tick()
                            if image is not None:
                                self._notify(AppEvent.FRAME_RENDERED,
                                             {'path': path, 'image': image})
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
        """Inject the renderer into the builder and ImageService.

        Called by InitPlatformCommand handler. Must wire ImageService immediately
        so that services (ThemeLoader, DisplayService) can use it before build_lcd()
        is called — e.g. CLI theme-load goes through DeviceService, not LCDDevice.
        """
        from ..services.image import ImageService
        self._builder.with_renderer(renderer)
        ImageService.set_renderer(renderer)

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
            ExportThemeCommand,
            ImportThemeCommand,
            LoadMaskCommand,
            LoadThemeByNameCommand,
            PlayVideoLoopCommand,
            RenderOverlayFromDCCommand,
            ResetDisplayCommand,
            SaveThemeCommand,
            SelectThemeCommand,
            SendColorCommand,
            SendImageCommand,
            SetBrightnessCommand,
            SetOverlayConfigCommand,
            SetResolutionCommand,
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

        def _select_theme(cmd: Command) -> CommandResult:
            return CommandResult.from_dict(lcd.select(cast(SelectThemeCommand, cmd).theme))

        def _save_theme(cmd: Command) -> CommandResult:
            c = cast(SaveThemeCommand, cmd)
            return CommandResult.from_dict(lcd.save(c.name, c.data_dir))

        def _export_theme(cmd: Command) -> CommandResult:
            return CommandResult.from_dict(lcd.export_config(cast(ExportThemeCommand, cmd).path))

        def _import_theme(cmd: Command) -> CommandResult:
            c = cast(ImportThemeCommand, cmd)
            return CommandResult.from_dict(lcd.import_config(c.path, c.data_dir))

        def _load_mask(cmd: Command) -> CommandResult:
            return CommandResult.from_dict(lcd.load_mask_standalone(cast(LoadMaskCommand, cmd).mask_path))

        def _render_overlay(cmd: Command) -> CommandResult:
            c = cast(RenderOverlayFromDCCommand, cmd)
            return CommandResult.from_dict(
                lcd.render_overlay_from_dc(c.dc_path, send=c.send, output=c.output or None))

        def _set_overlay_config(cmd: Command) -> CommandResult:
            return CommandResult.from_dict(lcd.set_config(cast(SetOverlayConfigCommand, cmd).config))

        def _reset_display(cmd: Command) -> CommandResult:
            return CommandResult.from_dict(lcd.reset())

        def _set_resolution(cmd: Command) -> CommandResult:
            c = cast(SetResolutionCommand, cmd)
            return CommandResult.from_dict(lcd.set_resolution(c.width, c.height))

        def _play_video_loop(cmd: Command) -> CommandResult:
            c = cast(PlayVideoLoopCommand, cmd)
            return CommandResult.from_dict(
                lcd.play_video_loop(c.video_path, loop=c.loop, duration=c.duration))

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
                .register(SelectThemeCommand, _select_theme)
                .register(SaveThemeCommand, _save_theme)
                .register(ExportThemeCommand, _export_theme)
                .register(ImportThemeCommand, _import_theme)
                .register(LoadMaskCommand, _load_mask)
                .register(RenderOverlayFromDCCommand, _render_overlay)
                .register(SetOverlayConfigCommand, _set_overlay_config)
                .register(ResetDisplayCommand, _reset_display)
                .register(SetResolutionCommand, _set_resolution)
                .register(PlayVideoLoopCommand, _play_video_loop)
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

        def _set_zone_mode(cmd: Command) -> CommandResult:
            c = cast(SetZoneModeCommand, cmd)
            return CommandResult.from_dict(led.set_zone_mode(c.zone, c.mode))

        def _set_zone_brightness(cmd: Command) -> CommandResult:
            c = cast(SetZoneBrightnessCommand, cmd)
            return CommandResult.from_dict(led.set_zone_brightness(c.zone, c.level))

        def _toggle_zone(cmd: Command) -> CommandResult:
            c = cast(ToggleZoneCommand, cmd)
            return CommandResult.from_dict(led.toggle_zone(c.zone, c.on))

        def _set_zone_sync(cmd: Command) -> CommandResult:
            c = cast(SetZoneSyncCommand, cmd)
            return CommandResult.from_dict(led.set_zone_sync(c.enabled, c.interval))

        def _toggle_segment(cmd: Command) -> CommandResult:
            c = cast(ToggleSegmentCommand, cmd)
            return CommandResult.from_dict(led.toggle_segment(c.index, c.on))

        def _set_clock_format(cmd: Command) -> CommandResult:
            return CommandResult.from_dict(led.set_clock_format(cast(SetClockFormatCommand, cmd).is_24h))

        def _set_temp_unit(cmd: Command) -> CommandResult:
            return CommandResult.from_dict(led.set_temp_unit(cast(SetTempUnitLEDCommand, cmd).unit))

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
                .register(SetZoneModeCommand, _set_zone_mode)
                .register(SetZoneBrightnessCommand, _set_zone_brightness)
                .register(ToggleZoneCommand, _toggle_zone)
                .register(SetZoneSyncCommand, _set_zone_sync)
                .register(ToggleSegmentCommand, _toggle_segment)
                .register(SetClockFormatCommand, _set_clock_format)
                .register(SetTempUnitLEDCommand, _set_temp_unit)
                .register(SetLEDSensorSourceCommand, _set_sensor_source)
                .register(UpdateMetricsLEDCommand, _update_metrics))

    def build_led_gui_bus(self, led: LEDDevice) -> CommandBus:
        """Return a CommandBus wired for GUI LED sliders.

        GUI signal handlers update device state only — the 150ms animation tick
        handles sending.  Handlers here call update_* (state-only) instead of
        set_* (immediate send), matching the tick-based LED architecture.
        RateLimitMiddleware prevents USB saturation when sliders move rapidly.
        """
        from .command_bus import (
            Command,
            CommandBus,
            CommandResult,
            LoggingMiddleware,
            RateLimitMiddleware,
            TimingMiddleware,
        )
        from .commands.led import (
            SetLEDBrightnessCommand,
            SetLEDColorCommand,
            SetLEDModeCommand,
        )

        def _update_color(cmd: Command) -> CommandResult:
            c = cast(SetLEDColorCommand, cmd)
            led.update_color(c.r, c.g, c.b)
            return CommandResult.ok(message="color updated")

        def _update_brightness(cmd: Command) -> CommandResult:
            led.update_brightness(cast(SetLEDBrightnessCommand, cmd).level)
            return CommandResult.ok(message="brightness updated")

        def _update_mode(cmd: Command) -> CommandResult:
            led.update_mode(cast(SetLEDModeCommand, cmd).mode)
            return CommandResult.ok(message="mode updated")

        return (CommandBus()
                .add_middleware(LoggingMiddleware())
                .add_middleware(TimingMiddleware(threshold_ms=200.0))
                .add_middleware(RateLimitMiddleware(min_interval_ms=50.0))
                .register(SetLEDColorCommand, _update_color)
                .register(SetLEDBrightnessCommand, _update_brightness)
                .register(SetLEDModeCommand, _update_mode))

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
