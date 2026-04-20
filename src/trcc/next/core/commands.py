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
    def execute(self, app: "App") -> DiscoverResult: ...


@dataclass(frozen=True, slots=True)
class ConnectDevice(Command):
    """Attach + handshake with a discovered device."""
    key: str

    def execute(self, app: "App") -> ConnectResult: ...


@dataclass(frozen=True, slots=True)
class DisconnectDevice(Command):
    """Close the transport and drop the device."""
    key: str

    def execute(self, app: "App") -> DisconnectResult: ...


# =========================================================================
# LCD — themes, frames, orientation, brightness
# =========================================================================


@dataclass(frozen=True, slots=True)
class LoadTheme(Command):
    """Apply a theme to a device.  Builds the first frame and sends it."""
    key: str
    path: Path

    def execute(self, app: "App") -> ThemeResult: ...


@dataclass(frozen=True, slots=True)
class SendFrame(Command):
    """Push an already-built frame to the device.  Mostly for scripts."""
    key: str
    data: bytes

    def execute(self, app: "App") -> SendResult: ...


@dataclass(frozen=True, slots=True)
class SetOrientation(Command):
    """Set per-device rotation (0 / 90 / 180 / 270)."""
    key: str
    degrees: int

    def execute(self, app: "App") -> OrientationResult: ...


@dataclass(frozen=True, slots=True)
class SetBrightness(Command):
    """Set per-device display brightness (0–100)."""
    key: str
    percent: int

    def execute(self, app: "App") -> BrightnessResult: ...


# =========================================================================
# LED
# =========================================================================


@dataclass(frozen=True, slots=True)
class SetLedColors(Command):
    """Set LED color array + on/off state + brightness."""
    key: str
    colors: List[Tuple[int, int, int]]
    global_on: bool = True
    brightness: int = 100

    def execute(self, app: "App") -> LedColorsResult: ...


# =========================================================================
# Sensors
# =========================================================================


@dataclass(frozen=True, slots=True)
class ReadSensors(Command):
    """Return current sensor readings."""
    def execute(self, app: "App") -> SensorsResult: ...


# =========================================================================
# System
# =========================================================================


@dataclass(frozen=True, slots=True)
class RunSetup(Command):
    """OS-specific one-time setup (udev rules, WinUSB guide, etc.)."""
    interactive: bool = True

    def execute(self, app: "App") -> SetupResult: ...
