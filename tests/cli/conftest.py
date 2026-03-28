"""CLI layer test fixtures.

Fixtures inherit from each other following the same DI chain as production code:

    mock_device_info → mock_device_svc ──┐
    mock_display_svc ────────────────────┼→ lcd → mock_connect_lcd
    mock_theme_svc ──────────────────────┘

    mock_led_svc → led → led_no_zones / led_no_segments
                 └→ led_empty (no svc)

Patch-path constants are exported so individual test files never hardcode
canonical module paths.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trcc.core.lcd_device import LCDDevice
from trcc.core.led_device import LEDDevice

# ── Canonical patch targets ──────────────────────────────────────────────────
PATCH_SETTINGS = "trcc.conf.settings"
PATCH_SETTINGS_CLS = "trcc.conf.Settings"
PATCH_DATA_MANAGER = "trcc.adapters.infra.data_repository.DataManager"
PATCH_THEME_SVC = "trcc.services.ThemeService"
PATCH_IMAGE_SVC = "trcc.services.ImageService"
PATCH_CONNECT_LCD = "trcc.cli._display._connect_or_fail"
PATCH_CONNECT_LED = "trcc.cli._led._connect_or_fail"


# ── LCD device chain ─────────────────────────────────────────────────────────

@pytest.fixture
def mock_device_info():
    """Mock DeviceInfo for the selected device."""
    dev = MagicMock()
    dev.resolution = (320, 320)
    dev.path = "/dev/sg0"
    dev.vid = 0x0402
    dev.pid = 0x3922
    dev.device_index = 0
    return dev


@pytest.fixture
def mock_device_svc(mock_device_info):
    """Mock DeviceService with pre-selected device.

    Inherits mock_device_info so selected.path / .vid / .pid are consistent.
    """
    svc = MagicMock()
    svc.selected = mock_device_info
    svc.send_frame.return_value = True
    svc.is_busy = False
    return svc


@pytest.fixture
def mock_display_svc():
    """Mock DisplayService."""
    svc = MagicMock()
    svc.lcd_width = 320
    svc.lcd_height = 320
    svc.overlay = MagicMock()
    svc.overlay.enabled = False
    svc.media = MagicMock()
    svc.media.has_frames = False
    svc.current_image = None
    svc.auto_send = False
    return svc


@pytest.fixture
def mock_theme_svc():
    """Mock ThemeService."""
    return MagicMock()


@pytest.fixture
def lcd(mock_device_svc, mock_display_svc, mock_theme_svc):
    """Fully wired LCDDevice with mock services."""
    return LCDDevice(
        device_svc=mock_device_svc,
        display_svc=mock_display_svc,
        theme_svc=mock_theme_svc,
    )


@pytest.fixture
def lcd_empty():
    """LCDDevice with no services (not connected)."""
    return LCDDevice()


# ── Connect fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def mock_connect_lcd(lcd):
    """Patch LCD _connect_or_fail → 0 and wire TrccApp with a real lcd_bus.

    lcd_bus is a REAL CommandBus wired to the mock lcd so tests can verify
    bus dispatch reaches the right service methods.
    """
    from trcc.core.app import TrccApp
    mock_app = TrccApp._instance
    mock_app.lcd_device = lcd
    mock_app.lcd_bus = TrccApp.build_lcd_bus(mock_app, lcd)  # type: ignore[arg-type]
    with patch(PATCH_CONNECT_LCD, return_value=0):
        yield lcd


@pytest.fixture
def mock_connect_led(led):
    """Patch LED _connect_or_fail → 0 and wire TrccApp with a real led_bus."""
    from trcc.core.app import TrccApp
    mock_app = TrccApp._instance
    mock_app.led_device = led
    mock_app.led_bus = TrccApp.build_led_bus(mock_app, led)  # type: ignore[arg-type]
    with patch(PATCH_CONNECT_LED, return_value=0):
        yield led


@pytest.fixture
def mock_connect_fail():
    """Patch both LCD and LED _connect_or_fail → 1 (no device)."""
    with patch(PATCH_CONNECT_LCD, return_value=1), \
         patch(PATCH_CONNECT_LED, return_value=1):
        yield


# ── LED device chain ─────────────────────────────────────────────────────────

@pytest.fixture
def mock_led_svc():
    """Fully mocked LEDService — no hardware required."""
    svc = MagicMock()
    svc.state = MagicMock()
    svc.state.zones = [MagicMock(), MagicMock(), MagicMock()]
    svc.state.segment_on = [True, False, True, False]
    svc.tick.return_value = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
    svc.apply_mask.return_value = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
    svc.has_protocol = True
    return svc


@pytest.fixture
def led(mock_led_svc):
    """LEDDevice wired to mock service."""
    return LEDDevice(svc=mock_led_svc)


@pytest.fixture
def led_empty():
    """LEDDevice with no service (not connected)."""
    return LEDDevice()


@pytest.fixture
def led_no_zones(mock_led_svc):
    """LEDDevice with empty zone list."""
    mock_led_svc.state.zones = []
    return LEDDevice(svc=mock_led_svc)


@pytest.fixture
def led_no_segments(mock_led_svc):
    """LEDDevice with empty segment list."""
    mock_led_svc.state.segment_on = []
    return LEDDevice(svc=mock_led_svc)


# ── Theme factories ───────────────────────────────────────────────────────────

@pytest.fixture
def make_local_theme():
    """Factory: build a mock ThemeInfo for a local theme."""
    def _factory(
        name: str = "MyTheme",
        is_animated: bool = False,
        animation_path=None,
        bg_exists: bool = True,
        is_user: bool = False,
        theme_path: str = "/themes/MyTheme",
    ) -> MagicMock:
        t = MagicMock()
        t.name = name if not is_user else f"Custom_{name}"
        t.is_animated = is_animated
        t.animation_path = animation_path
        t.background_path = MagicMock()
        t.background_path.exists.return_value = bg_exists
        t.path = Path(theme_path)
        t.category = None
        return t
    return _factory


@pytest.fixture
def make_cloud_theme():
    """Factory: build a mock ThemeInfo for a cloud theme."""
    def _factory(name: str = "CloudTheme", category: str = "a") -> MagicMock:
        t = MagicMock()
        t.name = name
        t.category = category
        return t
    return _factory


@pytest.fixture
def mock_theme_dir():
    """Mock settings.theme_dir that exists with a valid path."""
    td = MagicMock()
    td.exists.return_value = True
    td.path = Path("/themes/320x320")
    return td


@pytest.fixture
def mock_web_dir():
    """Mock settings.web_dir that exists."""
    wd = MagicMock()
    wd.exists.return_value = True
    return wd


# ── Detection factory ─────────────────────────────────────────────────────────

@pytest.fixture
def make_detected_device():
    """Factory: build a mock detected device (DetectedDevice-like MagicMock)."""
    def _factory(
        scsi_device: str | None = "/dev/sg0",
        product_name: str = "Frost Commander 360",
        vid: int = 0x87CD,
        pid: int = 0x70DB,
        protocol: str = "scsi",
        implementation: str = "generic",
        device_type: int = 1,
        path: str = "/dev/sg0",
        usb_path: str = "1-2",
        resolution: tuple[int, int] = (0, 0),
    ) -> MagicMock:
        dev = MagicMock()
        dev.scsi_device = scsi_device
        dev.product_name = product_name
        dev.vid = vid
        dev.pid = pid
        dev.protocol = protocol
        dev.implementation = implementation
        dev.device_type = device_type
        dev.path = path
        dev.usb_path = usb_path
        dev.resolution = resolution
        return dev
    return _factory


# ── System test helpers ───────────────────────────────────────────────────────

@pytest.fixture
def completed_process():
    """Factory: build a minimal subprocess.CompletedProcess mock."""
    def _factory(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
        m = MagicMock()
        m.returncode = returncode
        m.stdout = stdout
        m.stderr = stderr
        return m
    return _factory


@pytest.fixture
def hardware_metrics():
    """Factory: build a HardwareMetrics-like mock with sensible defaults."""
    def _factory(**overrides) -> MagicMock:
        m = MagicMock()
        defaults: dict = {
            "cpu_temp": 42.0, "cpu_percent": 15.0, "cpu_freq": 3600.0, "cpu_power": 45.0,
            "gpu_temp": 0.0, "gpu_usage": 0.0, "gpu_clock": 0.0, "gpu_power": 0.0,
            "mem_temp": 0.0, "mem_percent": 60.0, "mem_clock": 0.0, "mem_available": 8.0,
            "disk_temp": 0.0, "disk_activity": 0.0, "disk_read": 0.0, "disk_write": 0.0,
            "net_up": 0.0, "net_down": 0.0, "net_total_up": 0.0, "net_total_down": 0.0,
            "fan_cpu": 0.0, "fan_gpu": 0.0, "fan_ssd": 0.0, "fan_sys2": 0.0,
            "date": "2026-02-28", "time": "12:00", "weekday": "Saturday",
        }
        defaults.update(overrides)
        m.__class__.__name__ = "HardwareMetrics"
        for k, v in defaults.items():
            setattr(m, k, v)
        return m
    return _factory
