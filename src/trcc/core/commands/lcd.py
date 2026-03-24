"""LCD command dataclasses.

Each command corresponds to one LCDDevice operation.
All are frozen — value objects with no behaviour.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..command_bus import LCDCommand


@dataclass(frozen=True)
class ConnectLCDCommand(LCDCommand):
    """Connect to a specific device or auto-select."""
    detected: Any = None  # DetectedDevice | str | None


@dataclass(frozen=True)
class SendImageCommand(LCDCommand):
    """Send a static image file to the LCD."""
    image_path: str = ""


@dataclass(frozen=True)
class SendColorCommand(LCDCommand):
    """Fill the LCD with a solid RGB colour."""
    r: int = 0
    g: int = 0
    b: int = 0


@dataclass(frozen=True)
class SetBrightnessCommand(LCDCommand):
    """Set display brightness (1–3 levels or 0–100 percent)."""
    level: int = 3


@dataclass(frozen=True)
class SetRotationCommand(LCDCommand):
    """Rotate the display (0 | 90 | 180 | 270 degrees)."""
    degrees: int = 0


@dataclass(frozen=True)
class SetSplitModeCommand(LCDCommand):
    """Set split-screen mode (0–3)."""
    mode: int = 0


@dataclass(frozen=True)
class LoadThemeByNameCommand(LCDCommand):
    """Load a named theme, optionally at a specific resolution."""
    name: str = ""
    width: int = 0
    height: int = 0


@dataclass(frozen=True)
class PlayVideoLoopCommand(LCDCommand):
    """Start video/GIF/ZT playback."""
    video_path: str = ""
    loop: bool = True
    duration: float = 0.0


@dataclass(frozen=True)
class EnableOverlayCommand(LCDCommand):
    """Enable or disable the overlay pipeline."""
    on: bool = True


@dataclass(frozen=True)
class UpdateMetricsLCDCommand(LCDCommand):
    """Push fresh sensor metrics to the LCD overlay renderer."""
    metrics: Any = field(default=None, hash=False, compare=False)
