"""Commands — the universal UI contract.

Every user action is one Command class.  UIs build Commands, hand them
to App.dispatch, and render the returned Result.  Adding a new UI = new
adapter over the same Command classes.  Adding a new action = new
Command class.

Commands own their orchestration: they call services, talk to devices,
publish events, return a Result.  They are the business-logic layer
between UIs and the domain.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, List, Tuple

from .errors import (
    DeviceNotConnectedError,
    DeviceNotFoundError,
    HandshakeError,
    ThemeError,
    TransportError,
)
from .events import (
    BrightnessChanged,
    DeviceConnected,
    DeviceDisconnected,
    DeviceDiscovered,
    ErrorOccurred,
    FrameSent,
    OrientationChanged,
    ThemeLoaded,
)
from .registry import find_product
from .results import (
    BrightnessResult,
    ConnectResult,
    DisconnectResult,
    DiscoverResult,
    LedColorsResult,
    OrientationResult,
    Result,
    SendResult,
    SensorsResult,
    SetupResult,
    ThemeResult,
)

if TYPE_CHECKING:
    from ..app import App


# =========================================================================
# Base
# =========================================================================


class Command(ABC):
    """A user action.  Exactly one execute method; returns one Result."""

    @abstractmethod
    def execute(self, app: "App") -> Result: ...


# =========================================================================
# Discovery / connection
# =========================================================================


@dataclass(frozen=True, slots=True)
class DiscoverDevices(Command):
    """List attached devices that match the product registry."""

    def execute(self, app: "App") -> DiscoverResult:
        live = app.platform.scan_devices()
        products = []
        for info in live:
            product = find_product(info.vid, info.pid)
            if product is not None:
                products.append(product)
                app.events.publish(DeviceDiscovered(
                    key=info.key, product_name=product.product,
                ))
        return DiscoverResult(
            ok=True,
            message=f"{len(products)} device(s) found",
            products=products,
            devices=live,
        )


@dataclass(frozen=True, slots=True)
class ConnectDevice(Command):
    """Attach + handshake with a discovered device."""
    key: str

    def execute(self, app: "App") -> ConnectResult:
        try:
            vid_str, pid_str = self.key.split(":")
            vid, pid = int(vid_str, 16), int(pid_str, 16)
        except ValueError:
            return ConnectResult(
                ok=False, key=self.key,
                message=f"Invalid device key: {self.key!r} (expected 'vvvv:pppp')",
            )

        try:
            device = app.attach(vid, pid)
        except DeviceNotFoundError as e:
            app.events.publish(ErrorOccurred(message=str(e), kind="not_found",
                                             key=self.key))
            return ConnectResult(ok=False, key=self.key, message=str(e))

        try:
            handshake = device.connect()
        except (HandshakeError, TransportError) as e:
            app.detach(self.key)
            app.events.publish(ErrorOccurred(message=str(e), kind="handshake",
                                             key=self.key))
            return ConnectResult(ok=False, key=self.key, message=str(e))

        app.events.publish(DeviceConnected(
            key=self.key, resolution=handshake.resolution,
        ))
        return ConnectResult(
            ok=True, key=self.key,
            message=f"Connected: {handshake.resolution}",
            handshake=handshake,
        )


@dataclass(frozen=True, slots=True)
class DisconnectDevice(Command):
    """Close the transport and drop the device."""
    key: str

    def execute(self, app: "App") -> DisconnectResult:
        if self.key not in app.devices:
            return DisconnectResult(
                ok=False, key=self.key,
                message=f"Not attached: {self.key}",
            )
        app.detach(self.key)
        app.events.publish(DeviceDisconnected(key=self.key))
        return DisconnectResult(ok=True, key=self.key, message="Disconnected")


# =========================================================================
# LCD — frames, orientation, brightness
# =========================================================================


@dataclass(frozen=True, slots=True)
class SendFrame(Command):
    """Push already-built frame bytes to the device.

    Bypasses the theme/render pipeline (Phase 5+) — useful for scripts
    and end-to-end smoke tests.
    """
    key: str
    data: bytes

    def execute(self, app: "App") -> SendResult:
        try:
            device = app.get(self.key)
        except DeviceNotFoundError as e:
            return SendResult(ok=False, key=self.key, message=str(e))
        if not device.is_connected:
            raise DeviceNotConnectedError(
                f"{self.key} not connected — dispatch ConnectDevice first"
            )
        try:
            ok = device.send(self.data)
        except TransportError as e:
            app.events.publish(ErrorOccurred(message=str(e), kind="transport",
                                             key=self.key))
            return SendResult(ok=False, key=self.key, message=str(e))
        bytes_sent = len(self.data) if ok else 0
        if ok:
            app.events.publish(FrameSent(key=self.key, bytes_sent=bytes_sent))
        return SendResult(
            ok=ok, key=self.key,
            message=f"Sent {bytes_sent} bytes" if ok else "Send returned False",
            bytes_sent=bytes_sent,
        )


@dataclass(frozen=True, slots=True)
class LoadTheme(Command):
    """Parse a theme directory and remember it as the device's current theme.

    Actual frame rendering + send lands in Phase 6 (Renderer + DisplayService).
    This command validates the theme and persists the selection so it
    survives app restarts.
    """
    key: str
    path: Path

    def execute(self, app: "App") -> ThemeResult:
        try:
            theme = app.themes.load(self.path)
        except ThemeError as e:
            app.events.publish(ErrorOccurred(message=str(e), kind="theme",
                                             key=self.key))
            return ThemeResult(ok=False, key=self.key, message=str(e))

        app.settings.set_current_theme(self.key, theme.name)
        app.events.publish(ThemeLoaded(key=self.key, theme_name=theme.name))
        return ThemeResult(
            ok=True, key=self.key, theme_name=theme.name,
            message=f"Theme '{theme.name}' loaded "
                    f"(render+send lands with Phase 6)",
        )


@dataclass(frozen=True, slots=True)
class SetOrientation(Command):
    """Set per-device rotation (0 / 90 / 180 / 270).

    Validates against the product registry — device need not be
    connected yet (users often configure before plugging in).
    """
    key: str
    degrees: int

    def execute(self, app: "App") -> OrientationResult:
        try:
            vid_str, pid_str = self.key.split(":")
            vid, pid = int(vid_str, 16), int(pid_str, 16)
        except ValueError:
            return OrientationResult(
                ok=False, key=self.key, degrees=self.degrees,
                message=f"Invalid device key: {self.key!r}",
            )
        info = find_product(vid, pid)
        if info is None:
            return OrientationResult(
                ok=False, key=self.key, degrees=self.degrees,
                message=f"Unknown device: {self.key}",
            )
        if self.degrees not in info.orientations:
            return OrientationResult(
                ok=False, key=self.key, degrees=self.degrees,
                message=f"Unsupported orientation for {self.key}: {self.degrees}",
            )
        app.settings.set_orientation(self.key, self.degrees)
        app.events.publish(OrientationChanged(key=self.key, degrees=self.degrees))
        return OrientationResult(
            ok=True, key=self.key, degrees=self.degrees,
            message=f"Orientation set to {self.degrees}°",
        )


@dataclass(frozen=True, slots=True)
class SetBrightness(Command):
    """Set per-device display brightness (0–100)."""
    key: str
    percent: int

    def execute(self, app: "App") -> BrightnessResult:
        if not 0 <= self.percent <= 100:
            return BrightnessResult(
                ok=False, key=self.key, percent=self.percent,
                message="Brightness out of range (0–100)",
            )
        app.settings.set_brightness(self.key, self.percent)
        app.events.publish(BrightnessChanged(key=self.key, percent=self.percent))
        return BrightnessResult(
            ok=True, key=self.key, percent=self.percent,
            message=f"Brightness set to {self.percent}%",
        )


# =========================================================================
# LED
# =========================================================================


@dataclass(frozen=True, slots=True)
class SetLedColors(Command):
    """Set LED color array + on/off + brightness.  Stubbed until Led lands."""
    key: str
    colors: List[Tuple[int, int, int]]
    global_on: bool = True
    brightness: int = 100

    def execute(self, app: "App") -> LedColorsResult:
        return LedColorsResult(
            ok=False, key=self.key, colors=list(self.colors),
            message="SetLedColors pending Led Device implementation",
        )


# =========================================================================
# Sensors
# =========================================================================


@dataclass(frozen=True, slots=True)
class ReadSensors(Command):
    """Return current sensor readings.  Uses Platform's SensorEnumerator."""

    def execute(self, app: "App") -> SensorsResult:
        enum = app.platform.sensors()
        readings = enum.discover()
        return SensorsResult(
            ok=True,
            message=f"{len(readings)} sensor(s)",
            readings=list(readings),
        )


# =========================================================================
# System
# =========================================================================


@dataclass(frozen=True, slots=True)
class RunSetup(Command):
    """OS-specific one-time setup (udev, WinUSB guide, etc.)."""
    interactive: bool = True

    def execute(self, app: "App") -> SetupResult:
        warnings = app.platform.check_permissions()
        code = app.platform.setup(interactive=self.interactive)
        return SetupResult(
            ok=code == 0,
            message=f"Setup completed with exit code {code}",
            exit_code=code,
            warnings=warnings,
        )
