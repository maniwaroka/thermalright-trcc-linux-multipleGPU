#!/usr/bin/env python3
"""
Visual test harness for LCD devices — real themes, live metrics, mocked handshakes.

Same pattern as test_led_panel_visual.py:
- Device buttons at top (all known LCD devices from detector registries)
- Theme buttons to load real local themes
- Live metrics overlay (1s timer)
- Mocked handshake: each device button sets FBL → resolution + encoding
- Preview shows theme + overlay as the device would see it

Usage:
    PYTHONPATH=src python3 tests/test_lcd_panel_visual.py          # real metrics
    PYTHONPATH=src python3 tests/test_lcd_panel_visual.py --fake    # fake cycling
"""

import os
import sys

os.environ.setdefault('QT_QPA_PLATFORM', '')  # use real display

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


from PIL import Image as PILImage
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPalette, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from trcc.conf import settings
from trcc.core.models import (
    FBL_TO_RESOLUTION,
    JPEG_MODE_FBLS,
    HardwareMetrics,
    ThemeInfo,
)
from trcc.services.device import DeviceService
from trcc.services.display import DisplayService
from trcc.services.image import ImageService
from trcc.services.media import MediaService
from trcc.services.overlay import OverlayService

# ── Metrics source ────────────────────────────────────────────────
_use_fake = '--fake' in sys.argv
_tick = 0


def _get_metrics() -> HardwareMetrics:
    if not _use_fake:
        from trcc.services.system import get_all_metrics
        return get_all_metrics()

    global _tick
    _tick += 1
    phase = (_tick % 100) / 100.0
    return HardwareMetrics(
        cpu_temp=30 + phase * 60,
        cpu_freq=800 + phase * 4200,
        cpu_percent=phase * 100,
        cpu_power=30 + phase * 120,
        gpu_temp=25 + phase * 70,
        gpu_clock=300 + phase * 1700,
        gpu_usage=phase * 100,
        gpu_power=50 + phase * 250,
        mem_temp=35 + phase * 30,
        mem_clock=1600 + phase * 1200,
        mem_percent=20 + phase * 60,
        mem_available=16000,
        disk_temp=30 + phase * 30,
        disk_activity=phase * 100,
        disk_read=phase * 500,
        disk_write=phase * 300,
    )


# ── Mock LCD device registry ─────────────────────────────────────
# (label, vid, pid, protocol, device_type, fbl, notes)

_LCD_DEVICES = [
    # SCSI
    ("87CD:70DB\nSCSI 320x320",  0x87CD, 0x70DB, "scsi", 1, 100, "Thermalright LCD v1"),
    ("87CD:70DB\nSCSI 320x240",  0x87CD, 0x70DB, "scsi", 1, 50,  "Thermalright 320x240"),
    ("0416:5406\nSCSI 240x240",  0x0416, 0x5406, "scsi", 1, 36,  "Winbond SCSI"),
    ("0402:3922\nSCSI 480x480",  0x0402, 0x3922, "scsi", 1, 72,  "Frozen Warframe"),
    ("0402:3922\nSCSI 640x480",  0x0402, 0x3922, "scsi", 1, 64,  "Elite Vision 360"),
    # HID Type 2
    ("0416:5302\nHID2 320x320",  0x0416, 0x5302, "hid", 2, 100, "HID Type 2"),
    ("0416:5302\nHID2 360x360",  0x0416, 0x5302, "hid", 2, 54,  "HID2 JPEG"),
    ("0416:5302\nHID2 1280x480", 0x0416, 0x5302, "hid", 2, 128, "HID2 widescreen"),
    ("0416:5302\nHID2 1600x720", 0x0416, 0x5302, "hid", 2, 114, "HID2 ultrawide"),
    ("0416:5302\nHID2 1920x462", 0x0416, 0x5302, "hid", 2, 192, "HID2 bar"),
    ("0416:5302\nHID2 854x480",  0x0416, 0x5302, "hid", 2, 224, "HID2 wide"),
    # HID Type 3
    ("0418:5303\nHID3 320x320",  0x0418, 0x5303, "hid", 3, 101, "ALi Type 3"),
    ("0418:5304\nHID3 320x320",  0x0418, 0x5304, "hid", 3, 102, "ALi Type 3"),
    # Bulk
    ("87AD:70DB\nBulk 480x480",  0x87AD, 0x70DB, "bulk", 4, 72,  "GrandVision 360"),
    # LY
    ("0416:5408\nLY 1280x480",   0x0416, 0x5408, "ly", 5, 128, "Trofeo Vision"),
    ("0416:5409\nLY1 1280x480",  0x0416, 0x5409, "ly", 5, 128, "Trofeo Vision LY1"),
]


def _pil_to_qpixmap(img: PILImage.Image, max_dim: int = 400) -> QPixmap:
    scale = min(max_dim / img.width, max_dim / img.height, 1.0)
    if scale < 1.0:
        img = img.resize((int(img.width * scale), int(img.height * scale)),
                         PILImage.Resampling.NEAREST)
    img = img.convert('RGBA')
    data = img.tobytes('raw', 'RGBA')
    qimg = QImage(data, img.width, img.height, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(qimg.copy())


class LCDPanelTestHarness(QWidget):
    """LCD visual test: device buttons, theme buttons, live metrics overlay."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("LCD Visual Test — All Devices + Themes")
        self.setMinimumSize(1300, 900)

        self._current_device_idx = 0
        self._themes: list[ThemeInfo] = []

        # ── Services (real, no device needed) ──────────────────────
        self._device_svc = DeviceService()
        self._overlay_svc = OverlayService()
        self._media_svc = MediaService()
        self._display_svc = DisplayService(
            self._device_svc, self._overlay_svc, self._media_svc)

        # Dark background
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(50, 50, 50))
        self.setPalette(pal)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ── Device buttons (scrollable) ────────────────────────────
        dev_scroll = QScrollArea()
        dev_scroll.setWidgetResizable(True)
        dev_scroll.setMaximumHeight(70)
        dev_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        dev_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        dev_bar = QWidget()
        dev_layout = QHBoxLayout(dev_bar)
        dev_layout.setContentsMargins(0, 0, 0, 0)
        dev_layout.setSpacing(2)

        self._device_buttons: list[QPushButton] = []
        for i, (label, *_) in enumerate(_LCD_DEVICES):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setMinimumHeight(58)
            btn.setMinimumWidth(85)
            btn.setStyleSheet(
                "QPushButton { background: #333; color: #ccc; border: 1px solid #555; "
                "border-radius: 4px; font-size: 9px; padding: 2px 4px; }"
                "QPushButton:checked { background: #1565C0; color: white; "
                "border: 2px solid #42A5F5; }"
                "QPushButton:hover { background: #444; }"
            )
            btn.clicked.connect(lambda _, idx=i: self._switch_device(idx))
            dev_layout.addWidget(btn)
            self._device_buttons.append(btn)

        dev_scroll.setWidget(dev_bar)
        layout.addWidget(dev_scroll)

        # ── Theme buttons (scrollable) ─────────────────────────────
        theme_scroll = QScrollArea()
        theme_scroll.setWidgetResizable(True)
        theme_scroll.setMaximumHeight(45)
        theme_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        theme_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self._theme_bar = QWidget()
        self._theme_layout = QHBoxLayout(self._theme_bar)
        self._theme_layout.setContentsMargins(0, 0, 0, 0)
        self._theme_layout.setSpacing(2)

        theme_scroll.setWidget(self._theme_bar)
        layout.addWidget(theme_scroll)

        self._theme_buttons: list[QPushButton] = []

        # ── Device info ────────────────────────────────────────────
        self._device_label = QLabel()
        self._device_label.setStyleSheet(
            "color: #8cf; font-size: 12px; font-family: monospace; "
            "background: #1a1a2a; padding: 6px; border-radius: 3px;"
        )
        self._device_label.setWordWrap(True)
        layout.addWidget(self._device_label)

        # ── Metrics label ──────────────────────────────────────────
        self._metrics_label = QLabel()
        self._metrics_label.setStyleSheet(
            "color: #aaa; font-size: 11px; font-family: monospace; "
            "background: #222; padding: 4px; border-radius: 3px;"
        )
        self._metrics_label.setWordWrap(True)
        layout.addWidget(self._metrics_label)

        # ── Pipeline status ────────────────────────────────────────
        self._pipeline_label = QLabel()
        self._pipeline_label.setStyleSheet(
            "color: #ff8; font-size: 11px; font-family: monospace; "
            "background: #222; padding: 6px; border-radius: 3px;"
        )
        self._pipeline_label.setWordWrap(True)
        layout.addWidget(self._pipeline_label)

        # ── Preview area ───────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { background: #808080; border: none; }")

        preview_container = QWidget()
        preview_layout = QHBoxLayout(preview_container)
        preview_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_layout.setSpacing(20)

        # Theme background
        bg_col = QVBoxLayout()
        bg_col.addWidget(self._make_title("THEME BG"))
        self._bg_preview = QLabel()
        self._bg_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._bg_preview.setStyleSheet("background: #404040; border: 2px solid #666;")
        bg_col.addWidget(self._bg_preview)
        preview_layout.addLayout(bg_col)

        # Arrow
        preview_layout.addWidget(self._make_arrow())

        # With overlay
        ov_col = QVBoxLayout()
        ov_col.addWidget(self._make_title("+ OVERLAY"))
        self._overlay_preview = QLabel()
        self._overlay_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._overlay_preview.setStyleSheet("background: #404040; border: 2px solid #666;")
        ov_col.addWidget(self._overlay_preview)
        preview_layout.addLayout(ov_col)

        # Arrow
        preview_layout.addWidget(self._make_arrow())

        # Final (brightness/rotation applied)
        final_col = QVBoxLayout()
        final_col.addWidget(self._make_title("FINAL (send)"))
        self._final_preview = QLabel()
        self._final_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._final_preview.setStyleSheet("background: #404040; border: 2px solid #666;")
        final_col.addWidget(self._final_preview)
        preview_layout.addLayout(final_col)

        scroll.setWidget(preview_container)
        layout.addWidget(scroll, 1)

        # ── Start ──────────────────────────────────────────────────
        self._switch_device(0)

        # ── Timer: metrics every 1s ────────────────────────────────
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

    @staticmethod
    def _make_title(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("color: #aaa; font-size: 11px; font-weight: bold;")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return lbl

    @staticmethod
    def _make_arrow() -> QLabel:
        lbl = QLabel(">>>")
        lbl.setStyleSheet("color: #aaa; font-size: 18px; font-weight: bold;")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return lbl

    # ── Device switching ───────────────────────────────────────────

    def _switch_device(self, idx: int) -> None:
        self._current_device_idx = idx
        for i, btn in enumerate(self._device_buttons):
            btn.setChecked(i == idx)

        _, vid, pid, protocol, dtype, fbl, notes = _LCD_DEVICES[idx]
        w, h = FBL_TO_RESOLUTION.get(fbl, (320, 320))
        is_jpeg = fbl in JPEG_MODE_FBLS or protocol in ('bulk', 'ly')
        byte_order = ImageService.byte_order_for(protocol, (w, h), fbl)
        needs_rotation = (w, h) not in ImageService._SQUARE_NO_ROTATE and w != h

        bo_str = "BE" if byte_order == '>' else "LE"
        enc_str = "JPEG" if is_jpeg else f"RGB565 {bo_str}"
        rot_str = "90 CW" if needs_rotation else "none"

        self._device_label.setText(
            f"Device: {vid:04X}:{pid:04X} | Protocol: {protocol} | "
            f"Type: {dtype} | FBL: {fbl} | {notes}\n"
            f"Handshake -> FBL {fbl} -> {w}x{h} | "
            f"Encoding: {enc_str} | Rotation: {rot_str}"
        )

        # Reconfigure resolution via settings (lcd_size is a read-only property)
        settings.set_resolution(w, h, persist=False)

        # Reload themes for this resolution
        self._load_themes((w, h))

        self._render()

    # ── Theme loading ──────────────────────────────────────────────

    def _load_themes(self, resolution: tuple[int, int]) -> None:
        """Discover local themes and create buttons."""
        # Clear old buttons
        for btn in self._theme_buttons:
            self._theme_layout.removeWidget(btn)
            btn.deleteLater()
        self._theme_buttons.clear()

        # Find theme dir for this resolution
        w, h = resolution
        theme_dir = settings.user_data_dir / f"theme{w}{h}"
        if not theme_dir.exists():
            self._themes = []
            return

        from trcc.services.theme import ThemeService
        self._themes = ThemeService.discover_local(theme_dir, resolution)

        for i, theme in enumerate(self._themes):
            btn = QPushButton(theme.name)
            btn.setCheckable(True)
            btn.setMinimumHeight(32)
            btn.setStyleSheet(
                "QPushButton { background: #2a2a3a; color: #ccc; border: 1px solid #555; "
                "border-radius: 3px; font-size: 10px; padding: 2px 8px; }"
                "QPushButton:checked { background: #7B1FA2; color: white; "
                "border: 2px solid #CE93D8; }"
                "QPushButton:hover { background: #3a3a4a; }"
            )
            btn.clicked.connect(lambda _, idx=i: self._load_theme(idx))
            self._theme_layout.addWidget(btn)
            self._theme_buttons.append(btn)

        # Auto-load first theme
        if self._themes:
            self._load_theme(0)

    def _load_theme(self, idx: int) -> None:
        for i, btn in enumerate(self._theme_buttons):
            btn.setChecked(i == idx)

        theme = self._themes[idx]
        self._display_svc.load_local_theme(theme)
        self._render()

    # ── Render ─────────────────────────────────────────────────────

    def _render(self) -> None:
        _, vid, pid, protocol, dtype, fbl, notes = _LCD_DEVICES[self._current_device_idx]
        w, h = FBL_TO_RESOLUTION.get(fbl, (320, 320))
        is_jpeg = fbl in JPEG_MODE_FBLS or protocol in ('bulk', 'ly')

        # Background
        bg = self._display_svc.current_image
        if bg is not None:
            bg_pil = PILImage.fromarray(bg) if not isinstance(bg, PILImage.Image) else bg
            self._bg_preview.setPixmap(_pil_to_qpixmap(bg_pil))
        else:
            self._bg_preview.setText("No image")

        # With overlay
        overlay_img = self._display_svc.render_overlay()
        if overlay_img is not None:
            ov_pil = PILImage.fromarray(overlay_img) if not isinstance(
                overlay_img, PILImage.Image) else overlay_img
            self._overlay_preview.setPixmap(_pil_to_qpixmap(ov_pil))
        elif bg is not None:
            bg_pil = PILImage.fromarray(bg) if not isinstance(bg, PILImage.Image) else bg
            self._overlay_preview.setPixmap(_pil_to_qpixmap(bg_pil))

        # Final (what send_current_image returns)
        final = self._display_svc.send_current_image()
        if final is not None:
            final_pil = PILImage.fromarray(final) if not isinstance(
                final, PILImage.Image) else final
            self._final_preview.setPixmap(_pil_to_qpixmap(final_pil))

            # Encode metrics
            encoded = ImageService.encode_for_device(
                final_pil, protocol, (w, h), fbl, is_jpeg)
            enc_type = "JPEG" if is_jpeg else "RGB565"
            self._pipeline_label.setText(
                f"VID:PID {vid:04X}:{pid:04X} -> handshake() -> "
                f"FBL {fbl} -> {w}x{h} -> "
                f"{enc_type} encode -> {len(encoded):,} bytes -> send()"
            )

    # ── Metrics tick ───────────────────────────────────────────────

    def _tick(self) -> None:
        m = _get_metrics()

        # Update overlay with metrics
        self._overlay_svc.update_metrics(m)

        self._metrics_label.setText(
            f"CPU: {m.cpu_temp:.0f}C {m.cpu_freq:.0f}MHz {m.cpu_percent:.0f}% | "
            f"GPU: {m.gpu_temp:.0f}C {m.gpu_clock:.0f}MHz {m.gpu_usage:.0f}% | "
            f"MEM: {m.mem_temp:.0f}C {m.mem_percent:.0f}% | "
            f"DISK: {m.disk_temp:.0f}C {m.disk_activity:.0f}%"
        )

        self._render()


def main():
    argv = [a for a in sys.argv if a != '--fake']
    app = QApplication(argv)
    app.setStyle('Fusion')

    dark = QPalette()
    dark.setColor(QPalette.ColorRole.Window, QColor(53, 53, 53))
    dark.setColor(QPalette.ColorRole.WindowText, QColor(200, 200, 200))
    dark.setColor(QPalette.ColorRole.Base, QColor(35, 35, 35))
    dark.setColor(QPalette.ColorRole.Text, QColor(200, 200, 200))
    dark.setColor(QPalette.ColorRole.Button, QColor(53, 53, 53))
    dark.setColor(QPalette.ColorRole.ButtonText, QColor(200, 200, 200))
    app.setPalette(dark)

    window = LCDPanelTestHarness()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
