"""OS/platform initialization command dataclasses.

These commands cross the OS boundary — the handler decides which platform
adapter executes them. Callers (CLI, API, GUI) are completely blind to the
underlying OS.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..command_bus import OSCommand


@dataclass(frozen=True)
class InitPlatformCommand(OSCommand):
    """Bootstrap the platform: logging, OS detection, settings, renderer.

    Dispatched once at startup by every composition root (CLI, API, GUI).
    After dispatch: logging configured, settings ready, renderer wired.

    renderer_factory: callable that returns a Renderer — called once inside
    the handler. CLI passes an offscreen QtRenderer factory; GUI passes a
    full QtRenderer factory; API does the same. Core never imports Qt.
    """
    verbosity: int = 0
    renderer_factory: Callable[[], Any] | None = field(
        default=None, hash=False, compare=False,
    )


@dataclass(frozen=True)
class DiscoverDevicesCommand(OSCommand):
    """Scan for all connected TRCC devices, classify, connect, and wire buses.

    OS → scan() → classify by VID:PID/protocol → connect → _wire_bus()
    sets lcd_bus or led_bus.  After dispatch, check app.has_lcd / app.has_led.

    Optional path: restrict to a specific device path (e.g. '/dev/sg2').
    """
    path: str | None = None


@dataclass(frozen=True)
class SetLanguageCommand(OSCommand):
    """Set the application language by ISO 639-1 code.

    App-wide — all UI adapters (CLI, API, GUI) dispatch this to change language.
    Handler validates the code and persists via settings.
    """
    code: str = "en"


@dataclass(frozen=True)
class SetupPlatformCommand(OSCommand):
    """Run the interactive platform setup wizard.

    Delegates to the platform-specific PlatformSetup adapter.
    Works on all OSes — each adapter handles its own setup steps.
    """
    auto_yes: bool = False


@dataclass(frozen=True)
class SetupUdevCommand(OSCommand):
    """Install udev rules for LCD device access (Linux only)."""
    dry_run: bool = False


@dataclass(frozen=True)
class SetupSelinuxCommand(OSCommand):
    """Install SELinux policy module for USB device access (Linux only)."""


@dataclass(frozen=True)
class SetupPolkitCommand(OSCommand):
    """Install polkit policy for passwordless dmidecode/smartctl (Linux only)."""


@dataclass(frozen=True)
class InstallDesktopCommand(OSCommand):
    """Install .desktop menu entry and icon (Linux only)."""


@dataclass(frozen=True)
class SetupWinUsbCommand(OSCommand):
    """Guide WinUSB driver installation for Thermalright USB devices (Windows only)."""


@dataclass(frozen=True)
class DownloadThemesCommand(OSCommand):
    """Download theme packs from GitHub.

    pack: named pack (e.g. '320x320') or empty string to list available packs.
    force: re-download even if already cached.
    """
    pack: str = ""
    force: bool = False
