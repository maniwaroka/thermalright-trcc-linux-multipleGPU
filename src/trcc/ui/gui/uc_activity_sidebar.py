"""
PyQt6 UCActivitySidebar - Activity sidebar with live sensor values.

Shows real-time hardware sensor values that can be clicked to add to overlay.
Matches Windows TRCC right-side Activity panel.
"""

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from ...core.models import (
    SENSOR_TO_OVERLAY,
    SENSORS,
    OverlayElementConfig,
    OverlayMode,
)

log = logging.getLogger(__name__)

# Category colors for sidebar display — view-local (Qt layer, will be replaced)
CATEGORY_COLORS = {
    'cpu': '#32C5FF',
    'gpu': '#44D7B6',
    'memory': '#6DD401',
    'hdd': '#F7B501',
    'network': '#FA6401',
    'fan': '#E02020',
}


class SensorItem(QFrame):
    """Single sensor row — clickable to add to overlay."""

    clicked = Signal(object)  # OverlayElementConfig

    def __init__(self, category, key_suffix, label, unit, metric_key, color, parent=None):
        super().__init__(parent)
        self.category = category
        self.key_suffix = key_suffix
        self.metric_key = metric_key
        self.unit = unit
        self.color = color

        # Extract GPU index from key_suffix (e.g. 'gpu_0_temp' \u2192 0)
        self._gpu_index = 0
        if key_suffix.startswith('gpu_') and '_' in key_suffix[4:]:
            try:
                idx_str = key_suffix[4:key_suffix.index('_', 4)]
                self._gpu_index = int(idx_str)
            except (ValueError, IndexError):
                pass

        self.setFixedHeight(22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 5, 0)
        layout.setSpacing(4)

        # Color indicator
        indicator = QLabel('\u25c6')
        indicator.setFixedWidth(12)
        indicator.setStyleSheet(f"color: {color}; font-size: 6px; background: transparent;")
        layout.addWidget(indicator)

        # Sensor name
        name_lbl = QLabel(label)
        name_lbl.setStyleSheet("color: #AAAAAA; font-size: 9px; background: transparent;")
        name_lbl.setFixedWidth(70)
        layout.addWidget(name_lbl)

        layout.addStretch()

        # Sensor value
        self.value_label = QLabel('--')
        self.value_label.setStyleSheet(f"color: {color}; font-size: 9px; font-weight: bold; background: transparent;")
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.value_label.setFixedWidth(80)
        layout.addWidget(self.value_label)

        # Overlay config for click-to-add
        # Use metric_key directly (e.g. 'gpu_0_temp') instead of
        # constructing 'gpu_gpu_0_temp' which doesn't exist in SENSOR_TO_OVERLAY.
        # For indexed GPU metrics not in the mapping, derive from base metric.
        pair = SENSOR_TO_OVERLAY.get(self.metric_key)
        if pair is None:
            # Indexed GPU metric (e.g. 'gpu_0_temp') → strip index
            if self.metric_key.startswith('gpu_') and '_' in self.metric_key[4:]:
                base = 'gpu_' + self.metric_key[self.metric_key.index('_', 4) + 1:]
                pair = SENSOR_TO_OVERLAY.get(base, (0, 1))
            else:
                pair = (0, 1)
        main_count, sub_count = pair
        self._overlay_config = OverlayElementConfig(
            mode=OverlayMode.HARDWARE,
            main_count=main_count,
            sub_count=sub_count,
            gpu_index=self._gpu_index,
            color=color,
        )

    def update_value(self, metrics):
        """Update displayed value from HardwareMetrics DTO."""
        if (value := getattr(metrics, self.metric_key, None)) is not None:
            if isinstance(value, float):
                if value >= 1000:
                    self.value_label.setText(f"{int(value)}{self.unit}")
                else:
                    self.value_label.setText(f"{value:.1f}{self.unit}")
            else:
                self.value_label.setText(f"{value}{self.unit}")
        else:
            self.value_label.setText(f"--{self.unit}")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._overlay_config)

    def enterEvent(self, event):
        self.setStyleSheet("background-color: #2A2A2A;")

    def leaveEvent(self, event):
        self.setStyleSheet("")


class UCActivitySidebar(QWidget):
    """Activity sidebar — scrollable list of live hardware sensor values.

    Click a sensor to add it to the overlay grid.
    """

    sensor_clicked = Signal(object)  # OverlayElementConfig

    def __init__(self, parent=None):
        super().__init__(parent)

        self._sensor_items: list = []
        self._setup_ui()

    def _setup_ui(self):
        # Dark background via palette (not stylesheet — children use QPalette)
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor('#1E1E1E'))
        self.setPalette(palette)
        self.setAutoFillBackground(True)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 5, 0, 0)
        main_layout.setSpacing(0)

        # Title
        title = QLabel("Activity")
        title.setStyleSheet(
            "color: white; font-size: 10px; font-weight: bold; "
            "background: transparent; padding-left: 8px;"
        )
        main_layout.addWidget(title)

        # Scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
            "QScrollBar:vertical { background: transparent; width: 8px; }"
            "QScrollBar::handle:vertical { background: #555; border-radius: 4px; min-height: 20px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
        )
        main_layout.addWidget(scroll)

        # Inner widget
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.setSpacing(0)

        for category, sensors in SENSORS.items():
            color = CATEGORY_COLORS.get(category, '#FFFFFF')

            # Category header
            header = QLabel(f"  \u25aa {category.upper()}")
            header.setFixedHeight(24)
            header.setStyleSheet(
                f"color: {color}; font-size: 9px; font-weight: bold; "
                f"background-color: #2A2A2A; padding-top: 3px;"
            )
            inner_layout.addWidget(header)

            # Sensor items
            for key_suffix, label, unit, metric_key in sensors:
                item = SensorItem(category, key_suffix, label, unit, metric_key, color)
                item.clicked.connect(self._on_sensor_clicked)
                inner_layout.addWidget(item)
                self._sensor_items.append(item)

        inner_layout.addStretch()
        scroll.setWidget(inner)

    def _on_sensor_clicked(self, config):
        self.sensor_clicked.emit(config)

    def update_from_metrics(self, metrics) -> None:
        """Accept pre-polled metrics from MetricsMediator."""
        try:
            for item in self._sensor_items:
                item.update_value(metrics)
        except Exception as e:
            log.error("Activity sidebar update error: %s", e)

    def stop_updates(self) -> None:
        """No-op — retained for cleanup compatibility."""
