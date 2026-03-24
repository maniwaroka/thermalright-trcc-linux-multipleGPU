"""LED command dataclasses.

Each command corresponds to one LEDDevice operation.
All are frozen — value objects with no behaviour.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..command_bus import LEDCommand
from ..models import LEDMode


@dataclass(frozen=True)
class ConnectLEDCommand(LEDCommand):
    """Connect to the LED device."""


@dataclass(frozen=True)
class SetLEDColorCommand(LEDCommand):
    """Set all zones to a solid RGB colour."""
    r: int = 0
    g: int = 0
    b: int = 0


@dataclass(frozen=True)
class SetLEDModeCommand(LEDCommand):
    """Set the active LED effect mode."""
    mode: LEDMode = LEDMode.STATIC


@dataclass(frozen=True)
class SetLEDBrightnessCommand(LEDCommand):
    """Set LED brightness (0–100)."""
    level: int = 100


@dataclass(frozen=True)
class ToggleLEDCommand(LEDCommand):
    """Turn all LED zones on or off."""
    on: bool = True


@dataclass(frozen=True)
class SetZoneColorCommand(LEDCommand):
    """Set a specific zone to an RGB colour."""
    zone: int = 0
    r: int = 0
    g: int = 0
    b: int = 0


@dataclass(frozen=True)
class SetLEDSensorSourceCommand(LEDCommand):
    """Set the sensor source for temperature-linked LED modes."""
    source: str = "cpu"  # "cpu" | "gpu"


@dataclass(frozen=True)
class UpdateMetricsLEDCommand(LEDCommand):
    """Push fresh sensor metrics to the LED controller."""
    metrics: Any = field(default=None, hash=False, compare=False)
