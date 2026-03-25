"""LCD command dataclasses.

Each command corresponds to one LCDDevice operation.
All are frozen — value objects with no behaviour.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..command_bus import LCDCommand


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


@dataclass(frozen=True)
class SelectThemeCommand(LCDCommand):
    """Select and load a ThemeInfo object (GUI — caller already has the object)."""
    theme: Any = field(default=None, hash=False, compare=False)


@dataclass(frozen=True)
class SaveThemeCommand(LCDCommand):
    """Save current display state as a named theme."""
    name: str = ""
    data_dir: str = ""


@dataclass(frozen=True)
class ExportThemeCommand(LCDCommand):
    """Export a theme directory to a .tr archive."""
    path: str = ""


@dataclass(frozen=True)
class ImportThemeCommand(LCDCommand):
    """Import a .tr theme archive into the theme directory."""
    path: str = ""
    data_dir: str = ""


@dataclass(frozen=True)
class LoadMaskCommand(LCDCommand):
    """Load a mask overlay and composite with current background."""
    mask_path: str = ""


@dataclass(frozen=True)
class RenderOverlayFromDCCommand(LCDCommand):
    """Render overlay from a DC config file (CLI/API standalone)."""
    dc_path: str = ""
    send: bool = False
    output: str = ""


@dataclass(frozen=True)
class SetOverlayConfigCommand(LCDCommand):
    """Apply an overlay element config dict to the overlay service."""
    config: Any = field(default=None, hash=False, compare=False)


@dataclass(frozen=True)
class ResetDisplayCommand(LCDCommand):
    """Reset the LCD display to a solid red frame (device test)."""


@dataclass(frozen=True)
class SetResolutionCommand(LCDCommand):
    """Set the active display resolution."""
    width: int = 320
    height: int = 320


@dataclass(frozen=True)
class EnsureDataCommand(LCDCommand):
    """Download and extract theme/web/mask archives for a resolution.

    Fired automatically by SetResolutionCommand once the device resolution
    is known. Safe to dispatch multiple times — no-op if already cached.
    All three adapters (CLI, API, GUI) get data via this single path.
    """
    width: int = 320
    height: int = 320
