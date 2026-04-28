"""Trcc — the unified command facade for GUI, CLI, and API.

The one class every UI talks to. Composes LCDCommands, LEDCommands,
ControlCenterCommands, and an EventBus. Holds discovered devices.

Parity rule: every method reachable from one UI is reachable from all
three. No shortcuts, no UI-specific extensions. See TRCC_CONTRACT.md.

Usage:
    trcc = Trcc.for_current_os()
    trcc.bootstrap()
    trcc.discover()

    # Then, from any UI:
    trcc.lcd.set_brightness(0, 50)
    trcc.led.set_color(0, 255, 0, 0)
    trcc.control_center.set_temp_unit('F')
"""
from __future__ import annotations

import logging
from collections.abc import Iterator
from itertools import chain
from typing import TYPE_CHECKING

from .control_center_commands import ControlCenterCommands
from .events import EventBus
from .lcd_commands import LCDCommands
from .led_commands import LEDCommands
from .results import DiscoveryResult

if TYPE_CHECKING:
    from .device.lcd import LCDDevice
    from .device.led import LEDDevice
    from .ports import Platform, Renderer

log = logging.getLogger(__name__)


class Trcc:
    """Universal command facade.

    Construction is explicit (takes Platform). Use `for_current_os()` for
    the normal entry point.
    """

    def __init__(
        self,
        platform: Platform,
        *,
        renderer: Renderer | None = None,
    ) -> None:
        self._platform = platform
        self._renderer = renderer

        self._lcd_devices: list[LCDDevice] = []
        self._led_devices: list[LEDDevice] = []

        self.events = EventBus()
        self.lcd = LCDCommands(self._lcd_devices, self.events)
        self.led = LEDCommands(self._led_devices, self.events)
        self.control_center = ControlCenterCommands(platform, self.events)

    # ── Factory entry points ─────────────────────────────────────────

    @classmethod
    def for_current_os(cls) -> Trcc:
        """Build a Trcc wired with the OS-appropriate Platform.

        Low-level factory. UI adapters (CLI, API, GUI) extend this with
        their own bootstrap flow — see `cli/_boot.py`, `api/_boot.py`,
        and `Trcc.for_gui`.
        """
        from .builder import ControllerBuilder
        builder = ControllerBuilder.for_current_os()
        return cls(builder.os)

    @classmethod
    def for_gui(cls, renderer: Renderer) -> Trcc:
        """Trcc wired for the GUI.

        GUI owns its own `QApplication` and provides its own renderer.
        Discovery is deferred — the GUI calls `discover()` when the user
        triggers a device scan. Pure factory (Renderer is a port, not an
        adapter import).
        """
        trcc = cls.for_current_os()
        trcc.bootstrap()
        trcc.with_renderer(renderer)
        return trcc

    # ── Lifecycle ────────────────────────────────────────────────────

    def bootstrap(self, verbosity: int = 0) -> None:
        """Bootstrap logging + settings via the platform. Idempotent."""
        from .builder import ControllerBuilder
        ControllerBuilder(self._platform).bootstrap(verbosity=verbosity)

    def with_renderer(self, renderer: Renderer) -> Trcc:
        """Set the renderer used when building LCD devices during discover()."""
        self._renderer = renderer
        return self

    def register_lcd(self, device: LCDDevice) -> int:
        """Register an already-built+connected LCD device with the command layer.

        Returns the index the device got.  UI adapters that manage their own
        device lifecycle (e.g. the GUI's per-handler detection flow) call
        this instead of `discover()` so their devices show up in
        `Trcc.lcd._devices` and command dispatch resolves the index.

        Idempotent: if the same device is already registered, returns its
        existing index.
        """
        if device in self._lcd_devices:
            return self._lcd_devices.index(device)
        self._lcd_devices.append(device)
        return len(self._lcd_devices) - 1

    def register_led(self, device: LEDDevice) -> int:
        """Register an already-built+connected LED device.  Same contract as
        `register_lcd`."""
        if device in self._led_devices:
            return self._led_devices.index(device)
        self._led_devices.append(device)
        return len(self._led_devices) - 1

    def discover(self) -> DiscoveryResult:
        """Enumerate connected LCD and LED devices, build Device objects,
        register them with the command classes."""
        from .builder import ControllerBuilder
        from .models import PROTOCOL_TRAITS

        builder = ControllerBuilder(self._platform)
        if self._renderer is not None:
            builder = builder.with_renderer(self._renderer)

        try:
            detect_fn = builder.build_detect_fn()
            detected = detect_fn()
        except Exception as e:
            log.exception('discover: detect failed')
            return DiscoveryResult(success=False, error=str(e))

        lcd_infos = []
        led_infos = []
        self._lcd_devices.clear()
        self._led_devices.clear()

        for d in detected:
            traits = PROTOCOL_TRAITS.get(
                getattr(d, 'protocol', 'scsi'), PROTOCOL_TRAITS['scsi'])
            try:
                device = builder.build_device(d)
                connect_result = device.connect(d)
                if not connect_result.get('success'):
                    log.warning('discover: connect failed for %s: %s',
                                d, connect_result.get('error'))
                    continue
            except Exception:
                log.exception('discover: failed to build/connect device %s', d)
                continue
            from .device.lcd import LCDDevice as _LCD
            from .device.led import LEDDevice as _LED
            if traits.is_lcd and isinstance(device, _LCD):
                self._lcd_devices.append(device)
                lcd_infos.append(device.device_info)
            elif traits.is_led and isinstance(device, _LED):
                self._led_devices.append(device)
                led_infos.append(device.device_info)
            self.events.publish('device.connected', device.device_info)

        log.info('discover: found %d LCD, %d LED', len(lcd_infos), len(led_infos))
        return DiscoveryResult(
            success=True,
            message=f'Found {len(lcd_infos)} LCD(s), {len(led_infos)} LED(s)',
            lcd_devices=lcd_infos,
            led_devices=led_infos,
        )

    def cleanup(self) -> None:
        """Release every device and clear subscribers."""
        for dev in self:
            try:
                dev.cleanup()
            except Exception:
                log.exception('cleanup failed for %s', dev)
        self._lcd_devices.clear()
        self._led_devices.clear()
        self.events.clear()

    # ── Container protocol ───────────────────────────────────────────
    # Trcc IS the registry of connected devices — `for d in trcc` walks
    # every LCD then every LED, `len(trcc)` is total device count, and
    # `bool(trcc)` is True iff anything is connected.

    def __iter__(self) -> Iterator[LCDDevice | LEDDevice]:
        return chain(self._lcd_devices, self._led_devices)

    def __len__(self) -> int:
        return len(self._lcd_devices) + len(self._led_devices)
