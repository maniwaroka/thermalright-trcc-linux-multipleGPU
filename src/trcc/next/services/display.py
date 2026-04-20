"""DisplayService — orchestrates theme + sensors → device-ready frame bytes.

Pipeline (matches the C# ground truth: fit → overlay → dim → rotate → encode):

    1. Choose the *visual* canvas size — native resolution, or swapped
       if the user rotated 90°/270°.
    2. Load background (image OR first video frame) and fit it onto
       the canvas per DeviceSettings.fit_mode (WIDTH/HEIGHT/STRETCH).
    3. Composite overlay elements (text, metrics) on top.
    4. Apply brightness dim if < 100 %.
    5. Rotate the canvas back to native-buffer dimensions.
    6. Encode for the wire (RGB565 for SCSI / HID / Bulk / LY; JPEG
       variant lands with Type 2 HID).

Static-image themes use ImageService-equivalent logic directly; video
themes pull the current frame from MediaService.  Callers (commands)
pass a `bytes` result straight to Device.send().
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from ..core.models import FitMode, ProductInfo, RawFrame, Theme, Wire
from ..core.ports import Renderer
from .media import MediaService
from .overlay import OverlayService
from .settings import Settings
from .theme import ThemeService

log = logging.getLogger(__name__)


# Wire → encoding name.  Type 2 HID can swap to JPEG per device_type later.
_ENCODING_BY_WIRE: Dict[Wire, str] = {
    Wire.SCSI: "rgb565",
    Wire.HID: "rgb565",
    Wire.BULK: "rgb565",
    Wire.LY: "rgb565",
}

# Background file extensions recognised as video vs still image.
_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".avi"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}


class DisplayService:
    """Build device-ready frame bytes from a theme + live sensors."""

    def __init__(
        self,
        renderer: Renderer,
        themes: ThemeService,
        overlay: OverlayService,
        settings: Settings,
        media: MediaService,
    ) -> None:
        self._r = renderer
        self._themes = themes
        self._overlay = overlay
        self._settings = settings
        self._media = media

    # ── Top-level pipeline ────────────────────────────────────────────

    def build_frame(
        self,
        info: ProductInfo,
        theme: Theme,
        sensors: Dict[str, float],
    ) -> bytes:
        """One pass through the render pipeline for a single frame."""
        s = self._settings.for_device(info.key)
        visual_w, visual_h = self._visual_size(info.native_resolution, s.orientation)

        # 1. Background — image or video frame, fitted onto visual canvas
        surface = self._build_background(info, theme, s.fit_mode, (visual_w, visual_h))

        # 2. Overlay (text / metrics) on top
        surface = self._overlay.render(surface, theme.config, sensors)

        # 3. Brightness dim (before rotation, same order as the C# code)
        if s.brightness != 100:
            surface = self._r.apply_brightness(surface, s.brightness)

        # 4. Rotate content so the native buffer ends up right-side-up
        #    in the device's memory.  Rotating by -orientation swaps dims
        #    back to native; for square devices it's a no-op visually.
        if s.orientation:
            surface = self._r.rotate(surface, 360 - s.orientation)

        # 5. Encode for the wire.  Caller hands `bytes` to Device.send().
        return self._encode_for_wire(surface, info)

    # ── Background construction ───────────────────────────────────────

    def _build_background(
        self,
        info: ProductInfo,
        theme: Theme,
        fit_mode: FitMode,
        visual_size: Tuple[int, int],
    ) -> Any:
        """Produce a visual-sized canvas with the theme's background painted in."""
        canvas = self._r.create_surface(*visual_size, color=(0, 0, 0, 255))

        source = self._resolve_background(info, theme, visual_size)
        if source is None:
            return canvas

        src_w, src_h = self._r.surface_size(source)
        dst_w, dst_h = visual_size

        # Compute fitted size + position per FitMode.
        fit_w, fit_h, off_x, off_y = _fit(
            fit_mode, src_w, src_h, dst_w, dst_h,
        )
        fitted = self._r.resize(source, fit_w, fit_h)
        return self._r.composite(canvas, fitted, position=(off_x, off_y))

    def _resolve_background(
        self,
        info: ProductInfo,
        theme: Theme,
        visual_size: Tuple[int, int],
    ) -> Optional[Any]:
        """Find the theme's background and return a Renderer surface.

        Priority: active MediaService playback → theme background file
        (image or video).  For video files with no pre-loaded playback,
        decodes and caches the first frame here.
        """
        # Active playback (e.g. LoadTheme kicked off a video) wins
        playback = self._media.playback(info.key)
        if playback is not None and playback.frames:
            frame = playback.advance()
            if frame is not None:
                return self._r.from_raw_rgb24(frame)

        path = self._themes.background_path(theme)
        if path is None:
            return None

        ext = path.suffix.lower()
        if ext in _VIDEO_EXTS:
            # No pre-loaded playback; decode + cache first frame only.
            # Full-video playback is driven by the caller via MediaService
            # (LoadVideoBackground command in a later phase).
            try:
                pb = self._media.load_video(
                    device_key=info.key, path=path,
                    size=visual_size,
                )
            except Exception as e:
                log.warning("Video decode failed (%s) — falling back to black: %s",
                            path.name, e)
                return None
            frame: Optional[RawFrame] = pb.current
            return self._r.from_raw_rgb24(frame) if frame else None

        if ext in _IMAGE_EXTS:
            return self._r.open_image(path)

        log.debug("Unrecognised background extension %r; skipping", ext)
        return None

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _visual_size(native: Tuple[int, int], orientation: int) -> Tuple[int, int]:
        """Visual canvas dimensions — swapped for 90°/270° rotation."""
        w, h = native
        return (h, w) if orientation in (90, 270) else (w, h)

    def _encode_for_wire(self, surface: Any, info: ProductInfo) -> bytes:
        encoding = _ENCODING_BY_WIRE.get(info.wire, "rgb565")
        if encoding == "jpeg":
            return self._r.encode_jpeg(surface)
        return self._r.encode_rgb565(surface)


# =========================================================================
# Pure-Python fit algorithm (no rendering deps, easy to unit-test)
# =========================================================================


def _fit(
    mode: FitMode,
    src_w: int, src_h: int,
    dst_w: int, dst_h: int,
) -> Tuple[int, int, int, int]:
    """Compute (width, height, x_offset, y_offset) for *src* on *dst*.

    WIDTH   — match *dst_w*, letterbox top/bottom.
    HEIGHT  — match *dst_h*, pillarbox left/right.
    STRETCH — match both axes; no offset, aspect lost.
    """
    if mode is FitMode.STRETCH or src_w == 0 or src_h == 0:
        return dst_w, dst_h, 0, 0

    if mode is FitMode.WIDTH:
        fit_w = dst_w
        fit_h = max(1, (src_h * dst_w) // src_w)
        off_x = 0
        off_y = (dst_h - fit_h) // 2
        return fit_w, fit_h, off_x, off_y

    # FitMode.HEIGHT
    fit_h = dst_h
    fit_w = max(1, (src_w * dst_h) // src_h)
    off_x = (dst_w - fit_w) // 2
    off_y = 0
    return fit_w, fit_h, off_x, off_y


# Re-exported for unit tests
fit = _fit
