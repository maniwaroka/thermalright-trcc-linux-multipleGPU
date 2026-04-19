"""Device package — LCD and LED device facades.

Concrete classes live in their submodules; import them directly:

    from trcc.core.device.lcd import LCDDevice
    from trcc.core.device.led import LEDDevice

`Device` is exposed here as a type-alias for collections that hold
either flavor. It is the only name re-exported from this package.
"""
from __future__ import annotations

from .lcd import LCDDevice
from .led import LEDDevice

Device = LCDDevice | LEDDevice

__all__ = ['Device']
