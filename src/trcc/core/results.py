"""Result + Info dataclasses for the universal TRCC command layer.

Every Trcc command returns one of these. All frozen, JSON-serializable via
`dataclasses.asdict`. UI adapters render the result their own way:
GUI paints widgets, CLI prints, API serializes to JSON.

Reuses existing domain types from `core.models` — ThemeInfo, MaskInfo,
SensorInfo, OverlayElement, DeviceInfo — instead of redefining.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from .models.device import DeviceInfo


# =============================================================================
# Frame — framework-neutral image container.
# Qt renderer produces this; GUI widgets convert to QPixmap at the edge;
# API base64-encodes pixels/encoded; CLI writes to disk via a helper.
# =============================================================================

@dataclass(frozen=True, slots=True)
class Frame:
    width: int
    height: int
    pixels: bytes                      # raw RGBA
    encoded: bytes | None = None       # RGB565 pre-encoded for device


# =============================================================================
# Base result types — every command returns one of these.
# =============================================================================

@dataclass(frozen=True, slots=True)
class OpResult:
    """Generic success/failure outcome with a message."""
    success: bool
    message: str = ''
    error: str | None = None


@dataclass(frozen=True, slots=True)
class FrameResult:
    """Command that renders a frame (brightness, rotation, overlay edit, …)."""
    success: bool
    message: str = ''
    error: str | None = None
    frame: Frame | None = None


@dataclass(frozen=True, slots=True)
class ThemeResult:
    """Theme load — may carry overlay config + animation metadata."""
    success: bool
    message: str = ''
    error: str | None = None
    frame: Frame | None = None
    is_animated: bool = False
    interval_ms: int = 0
    overlay_config: dict | None = None
    overlay_enabled: bool = False


@dataclass(frozen=True, slots=True)
class VideoTickResult:
    """One frame from the video playback tick."""
    frame: Frame | None
    frame_index: int
    progress_percent: float
    current_time: str
    total_time: str


@dataclass(frozen=True, slots=True)
class LEDResult:
    """LED command result — includes current display colors for preview."""
    success: bool
    message: str = ''
    error: str | None = None
    display_colors: list[tuple[int, int, int]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    """Device enumeration result — LCDs and LEDs detected."""
    success: bool
    message: str = ''
    error: str | None = None
    lcd_devices: list[DeviceInfo] = field(default_factory=list)
    led_devices: list[DeviceInfo] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class UpdateResult:
    """Update check result — latest version + per-package-manager download URLs."""
    success: bool
    message: str = ''
    error: str | None = None
    current_version: str = ''
    latest_version: str | None = None
    update_available: bool = False
    assets: dict[str, str] = field(default_factory=dict)   # {pkg_mgr: url}


# =============================================================================
# Snapshots — read-only state bundles.
# GUI uses on first paint / reactivation; CLI `trcc status`; API `GET /status`.
# =============================================================================

@dataclass(frozen=True, slots=True)
class LCDSnapshot:
    connected: bool
    playing: bool
    auto_send: bool
    overlay_enabled: bool
    brightness: int
    rotation: int
    split_mode: int
    fit_mode: str
    resolution: tuple[int, int]
    current_theme: str | None


@dataclass(frozen=True, slots=True)
class LEDSnapshot:
    connected: bool
    style_id: int
    mode: int
    color: tuple[int, int, int]
    brightness: int
    global_on: bool
    zones: list[dict]
    zone_sync: bool
    zone_sync_interval: int
    selected_zone: int
    segment_on: list[bool]
    clock_24h: bool
    week_sunday: bool
    memory_ratio: int
    disk_index: int
    test_mode: bool


@dataclass(frozen=True, slots=True)
class AppSnapshot:
    version: str
    autostart: bool
    temp_unit: str                     # 'C' | 'F'
    language: str
    hdd_enabled: bool
    refresh_interval: int
    gpu_device: str | None
    gpu_list: list[tuple[str, str]]
    install_method: str                # 'pipx' | 'pip' | 'pacman' | 'dnf' | 'apt'
    distro: str


# =============================================================================
# Info dataclasses — list-result element types not already in core.models.
# =============================================================================

@dataclass(frozen=True, slots=True)
class BackgroundInfo:
    """User-uploaded background image available on the LCD device."""
    name: str
    path: Path


@dataclass(frozen=True, slots=True)
class LEDStyleInfo:
    """Describes a supported LED product style — segments, zones, modes."""
    style_id: int
    name: str
    segment_count: int
    zone_count: int
    supported_modes: list[str]


@dataclass(frozen=True, slots=True)
class DiskInfo:
    """Physical disk available for the LF11 disk-select dropdown."""
    index: int
    name: str
    path: str
