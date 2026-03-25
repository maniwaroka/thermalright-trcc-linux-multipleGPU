"""Command dataclasses for all TRCC operations.

Three layers — import from the appropriate module:
    from trcc.core.commands.initialize import DiscoverDevicesCommand
    from trcc.core.commands.lcd import SendImageCommand
    from trcc.core.commands.led import SetLEDColorCommand

Or import everything from here:
    from trcc.core.commands import DiscoverDevicesCommand, SendImageCommand
"""
from .initialize import (
    DiscoverDevicesCommand,
    DownloadThemesCommand,
    InitPlatformCommand,
    InstallDesktopCommand,
    SetLanguageCommand,
    SetupPlatformCommand,
    SetupPolkitCommand,
    SetupSelinuxCommand,
    SetupUdevCommand,
    SetupWinUsbCommand,
)
from .lcd import (
    EnableOverlayCommand,
    EnsureDataCommand,
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
from .led import (
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

__all__ = [
    # OS / initialize
    "InitPlatformCommand",
    "DiscoverDevicesCommand",
    "SetLanguageCommand",
    "SetupPlatformCommand",
    "SetupUdevCommand",
    "SetupSelinuxCommand",
    "SetupPolkitCommand",
    "InstallDesktopCommand",
    "SetupWinUsbCommand",
    "DownloadThemesCommand",
    # LCD
    "EnableOverlayCommand",
    "EnsureDataCommand",
    "ExportThemeCommand",
    "ImportThemeCommand",
    "LoadMaskCommand",
    "LoadThemeByNameCommand",
    "PlayVideoLoopCommand",
    "RenderOverlayFromDCCommand",
    "ResetDisplayCommand",
    "SaveThemeCommand",
    "SelectThemeCommand",
    "SendColorCommand",
    "SendImageCommand",
    "SetBrightnessCommand",
    "SetOverlayConfigCommand",
    "SetResolutionCommand",
    "SetRotationCommand",
    "SetSplitModeCommand",
    "UpdateMetricsLCDCommand",
    # LED
    "SetClockFormatCommand",
    "SetLEDBrightnessCommand",
    "SetLEDColorCommand",
    "SetLEDModeCommand",
    "SetLEDSensorSourceCommand",
    "SetTempUnitLEDCommand",
    "SetZoneBrightnessCommand",
    "SetZoneColorCommand",
    "SetZoneModeCommand",
    "SetZoneSyncCommand",
    "ToggleLEDCommand",
    "ToggleSegmentCommand",
    "ToggleZoneCommand",
    "UpdateMetricsLEDCommand",
]
