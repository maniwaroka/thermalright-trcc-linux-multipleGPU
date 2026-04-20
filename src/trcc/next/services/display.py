"""DisplayService — orchestrates theme + sensors → device-ready frame bytes.

The top of the rendering pipeline.  Flow:

    Theme (path + config) ─┐
    sensor readings ───────┼── OverlayService.render ── Renderer.rotate
    DeviceSettings ────────┘             │
                                          ├── apply_brightness (if ≠ 100)
                                          │
                                          └── Renderer.encode_* for the wire
                                              (RGB565 for SCSI, JPEG for some HID)
                                              → bytes

Depends on: Renderer, ThemeService, OverlayService, Settings.  Knows
nothing about devices or USB — the bytes it produces get handed to a
Device.send(bytes) by the caller (a Command).
"""
from __future__ import annotations

import logging
from typing import Dict

from ..core.models import ProductInfo, Theme, Wire
from ..core.ports import Renderer
from .overlay import OverlayService
from .settings import Settings
from .theme import ThemeService

log = logging.getLogger(__name__)


# Byte-wire format per Wire protocol.
# SCSI wants RGB565; HID Type 2 supports JPEG; Type 3 is RGB565 for now.
_ENCODING_BY_WIRE: Dict[Wire, str] = {
    Wire.SCSI: "rgb565",
    Wire.HID: "rgb565",    # Type 2 can also be "jpeg" — selected per device_type later
    Wire.BULK: "rgb565",
    Wire.LY: "rgb565",
}


class DisplayService:
    """Build device-ready frame bytes from a theme + live sensors."""

    def __init__(
        self,
        renderer: Renderer,
        themes: ThemeService,
        overlay: OverlayService,
        settings: Settings,
    ) -> None:
        self._r = renderer
        self._themes = themes
        self._overlay = overlay
        self._settings = settings

    # ── Top-level pipeline ────────────────────────────────────────────

    def build_frame(
        self,
        info: ProductInfo,
        theme: Theme,
        sensors: Dict[str, float],
    ) -> bytes:
        """Produce one frame of device-native bytes for a given product + theme."""
        # 1. Load background from theme
        bg_path = self._themes.background_path(theme)
        if bg_path is None:
            log.warning("Theme %r has no background image; using blank surface",
                        theme.name)
            surface = self._r.create_surface(
                *info.native_resolution, color=(0, 0, 0, 255),
            )
        else:
            surface = self._r.open_image(bg_path)
            # Resize to the device's native resolution if the theme differs
            if self._r.surface_size(surface) != info.native_resolution:
                surface = self._r.resize(surface, *info.native_resolution)

        # 2. Composite overlay elements (text, metrics) onto the background
        surface = self._overlay.render(surface, theme.config, sensors)

        # 3. Apply user rotation
        device_settings = self._settings.for_device(info.key)
        if device_settings.orientation:
            surface = self._r.rotate(surface, device_settings.orientation)

        # 4. Apply brightness (if not 100%)
        if device_settings.brightness != 100:
            surface = self._r.apply_brightness(surface, device_settings.brightness)

        # 5. Encode for the wire
        return self._encode_for_wire(surface, info)

    # ── internals ─────────────────────────────────────────────────────

    def _encode_for_wire(self, surface, info: ProductInfo) -> bytes:
        encoding = _ENCODING_BY_WIRE.get(info.wire, "rgb565")
        if encoding == "jpeg":
            return self._r.encode_jpeg(surface)
        return self._r.encode_rgb565(surface)
