"""Command bus handler modules — driving port boundary.

Three modules, one per device family:
  lcd  — LCDCommandHandler, build_lcd_bus, build_lcd_gui_bus
  led  — LEDCommandHandler, LEDGuiCommandHandler, build_led_bus, build_led_gui_bus
  os   — OSCommandHandler, build_os_bus

TrccApp delegates all bus construction here. Callers (CLI, API, GUI) never
import these modules directly — they go through TrccApp.build_*_bus().
"""
from .lcd import build_lcd_bus, build_lcd_gui_bus
from .led import build_led_bus, build_led_gui_bus
from .os import build_os_bus

__all__ = [
    "build_lcd_bus",
    "build_lcd_gui_bus",
    "build_led_bus",
    "build_led_gui_bus",
    "build_os_bus",
]
