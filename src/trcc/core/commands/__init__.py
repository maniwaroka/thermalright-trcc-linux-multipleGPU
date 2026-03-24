"""Command dataclasses for all LCD and LED operations.

Import from here:
    from trcc.core.commands import SetBrightnessCommand, SetLEDModeCommand
"""
from .lcd import (
    ConnectLCDCommand,
    EnableOverlayCommand,
    LoadThemeByNameCommand,
    PlayVideoLoopCommand,
    SendColorCommand,
    SendImageCommand,
    SetBrightnessCommand,
    SetRotationCommand,
    SetSplitModeCommand,
    UpdateMetricsLCDCommand,
)
from .led import (
    ConnectLEDCommand,
    SetLEDBrightnessCommand,
    SetLEDColorCommand,
    SetLEDModeCommand,
    SetLEDSensorSourceCommand,
    SetZoneColorCommand,
    ToggleLEDCommand,
    UpdateMetricsLEDCommand,
)

__all__ = [
    # LCD
    "ConnectLCDCommand",
    "EnableOverlayCommand",
    "LoadThemeByNameCommand",
    "PlayVideoLoopCommand",
    "SendColorCommand",
    "SendImageCommand",
    "SetBrightnessCommand",
    "SetRotationCommand",
    "SetSplitModeCommand",
    "UpdateMetricsLCDCommand",
    # LED
    "ConnectLEDCommand",
    "SetLEDBrightnessCommand",
    "SetLEDColorCommand",
    "SetLEDModeCommand",
    "SetLEDSensorSourceCommand",
    "SetZoneColorCommand",
    "ToggleLEDCommand",
    "UpdateMetricsLEDCommand",
]
