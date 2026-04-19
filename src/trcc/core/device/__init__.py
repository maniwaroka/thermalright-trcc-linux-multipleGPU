"""Device package — LCD and LED device facades.

Two concrete classes live in their own modules:
  trcc.core.device.lcd::LCDDevice — pixel frames, themes, overlays, video
  trcc.core.device.led::LEDDevice — RGB color arrays, zones, segments

`Device` is the Union alias for code that accepts either flavor (builder
return type, Trcc facade lists). Callers that are LCD-only or LED-only
should import the concrete class directly.
"""
from __future__ import annotations

from ._logging import tagged_logger
from .lcd import LCDDevice
from .led import LEDDevice

Device = LCDDevice | LEDDevice

__all__ = ['Device', 'LCDDevice', 'LEDDevice', 'tagged_logger']
