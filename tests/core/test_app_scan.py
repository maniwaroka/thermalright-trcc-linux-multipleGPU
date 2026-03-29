"""Tests for TrccApp.scan(), bootstrap(), and _wire_bus().

Coverage targets:
  - core/app.py: scan(), bootstrap(), _wire_bus(), device_connected(), device_lost()
  - Parallel connect: all devices attempted, failures isolated, buses wired
  - EnsureDataCommand fired when LCD has a known resolution
  - Observer notified with correct events
"""
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from trcc.core.app import AppEvent, TrccApp
from trcc.core.models import DetectedDevice

# ── Helpers ──────────────────────────────────────────────────────────────────

def _detected(usb_path: str = "2-1", protocol: str = "scsi") -> DetectedDevice:
    return DetectedDevice(
        vid=0x87CD, pid=0x70DB,
        vendor_name="Thermalright", product_name="LCD",
        usb_path=usb_path, protocol=protocol,
    )


def _detected_led(usb_path: str = "2-2") -> DetectedDevice:
    return DetectedDevice(
        vid=0x0416, pid=0x8001,
        vendor_name="Winbond", product_name="LED",
        usb_path=usb_path, protocol="hid",
    )


def _mock_lcd_device(path: str = "2-1", resolution: tuple = (320, 320)):
    from trcc.core.lcd_device import LCDDevice
    dev = MagicMock(spec=LCDDevice)
    dev.is_lcd = True
    dev.is_led = False
    dev.device_path = path
    dev.device_info = MagicMock()
    dev.device_info.resolution = resolution
    dev.connect.return_value = {"success": True}
    return dev


def _mock_led_device(path: str = "2-2"):
    from trcc.core.led_device import LEDDevice
    dev = MagicMock(spec=LEDDevice)
    dev.is_lcd = False
    dev.is_led = True
    dev.device_path = path
    dev.connect.return_value = {"success": True}
    return dev


@pytest.fixture()
def app():
    """Fresh TrccApp backed by a fully-mocked builder."""
    TrccApp.reset()
    builder = MagicMock()
    builder.build_detect_fn.return_value = lambda: []  # empty by default
    inst = TrccApp(builder)
    TrccApp._instance = inst
    yield inst
    TrccApp.reset()


# ── scan() — no devices ───────────────────────────────────────────────────────

class TestScanEmpty:
    def test_returns_empty_list(self, app):
        assert app.scan() == []

    def test_devices_property_empty(self, app):
        app.scan()
        assert app.devices == []

    def test_notifies_devices_changed(self, app):
        observer = MagicMock()
        observer.on_app_event = MagicMock()
        app.register(observer)
        app.scan()
        observer.on_app_event.assert_called_once_with(AppEvent.DEVICES_CHANGED, [])

    def test_no_lcd_bus(self, app):
        app.scan()
        assert not app.has_lcd

    def test_no_led_bus(self, app):
        app.scan()
        assert not app.has_led


# ── scan() — single LCD device ───────────────────────────────────────────────

class TestScanSingleLcd:
    @pytest.fixture()
    def lcd_app(self, app):
        detected = _detected("2-1")
        lcd_dev = _mock_lcd_device("2-1")
        app._builder.build_detect_fn.return_value = lambda: [detected]
        app._builder.build_device.return_value = lcd_dev
        # _wire_device stores the device on TrccApp

        return app, lcd_dev

    def test_returns_one_device(self, lcd_app):
        app, _ = lcd_app
        result = app.scan()
        assert len(result) == 1

    def test_lcd_bus_wired(self, lcd_app):
        app, _ = lcd_app
        app.scan()
        assert app.has_lcd

    def test_lcd_device_stored(self, lcd_app):
        app, lcd_dev = lcd_app
        app.scan()
        assert app._lcd_device is lcd_dev

    def test_connect_called(self, lcd_app):
        app, lcd_dev = lcd_app
        detected = _detected("2-1")
        app._builder.build_detect_fn.return_value = lambda: [detected]
        app.scan()
        lcd_dev.connect.assert_called_once_with(detected)

    def test_ensure_data_called_by_bootstrap(self, lcd_app):
        """bootstrap() must call ensure_data_fn for each connected LCD resolution."""
        app, _ = lcd_app
        called_with = []
        app._ensure_data_fn = lambda w, h, progress_fn=None: called_with.append((w, h))

        with patch.object(app, 'init_platform'):
            app.bootstrap()

        assert (320, 320) in called_with

    def test_ensure_data_skipped_when_resolution_zero(self, app):
        """_ensure_data_blocking must not call ensure_fn when resolution is (0, 0)."""
        detected = _detected("2-1")
        lcd_dev = _mock_lcd_device("2-1", resolution=(0, 0))
        app._builder.build_detect_fn.return_value = lambda: [detected]
        app._builder.build_device.return_value = lcd_dev

        called_with = []
        app._ensure_data_fn = lambda w, h, progress_fn=None: called_with.append((w, h))

        with patch.object(app, 'init_platform'):
            app.bootstrap()

        assert called_with == []


# ── scan() — single LED device ───────────────────────────────────────────────

class TestScanSingleLed:
    @pytest.fixture()
    def led_app(self, app):
        detected = _detected_led("2-2")
        led_dev = _mock_led_device("2-2")
        app._builder.build_detect_fn.return_value = lambda: [detected]
        app._builder.build_device.return_value = led_dev

        return app, led_dev

    def test_led_bus_wired(self, led_app):
        app, _ = led_app
        app.scan()
        assert app.has_led

    def test_led_device_stored(self, led_app):
        app, led_dev = led_app
        app.scan()
        assert app._led_device is led_dev

    def test_no_lcd_bus(self, led_app):
        app, _ = led_app
        app.scan()
        assert not app.has_lcd


# ── scan() — parallel connect ────────────────────────────────────────────────

class TestScanParallel:
    def test_all_devices_connected(self, app):
        """All detected devices must be connected regardless of count."""
        detected_list = [_detected(f"2-{i}") for i in range(4)]
        devices = [_mock_lcd_device(f"2-{i}") for i in range(4)]
        app._builder.build_detect_fn.return_value = lambda: detected_list
        app._builder.build_device.side_effect = devices


        result = app.scan()
        assert len(result) == 4

    def test_connect_calls_happen_concurrently(self, app):
        """With 3 devices each taking 50ms, parallel finishes faster than serial."""
        barrier = threading.Barrier(3)
        connect_times: list[float] = []
        lock = threading.Lock()

        def slow_connect(detected):
            barrier.wait(timeout=2)
            with lock:
                connect_times.append(time.monotonic())
            return {"success": True}

        detected_list = [_detected(f"2-{i}") for i in range(3)]
        devices = []
        for i in range(3):
            d = _mock_lcd_device(f"2-{i}")
            d.connect.side_effect = slow_connect
            devices.append(d)

        app._builder.build_detect_fn.return_value = lambda: detected_list
        app._builder.build_device.side_effect = devices


        app.scan()
        # 3 serial 50ms connects = 150ms; parallel with barrier = ~50ms
        # We used a barrier (not sleep), so elapsed just needs all 3 to complete
        assert len(connect_times) == 3

    def test_failed_connect_does_not_block_others(self, app):
        """One device failing connect must not prevent others from wiring."""
        d0 = _detected("2-0")
        d1 = _detected("2-1")
        d2 = _detected("2-2")

        dev0 = _mock_lcd_device("2-0")
        dev0.connect.side_effect = RuntimeError("USB error")
        dev1 = _mock_lcd_device("2-1")
        dev2 = _mock_lcd_device("2-2")

        app._builder.build_detect_fn.return_value = lambda: [d0, d1, d2]
        app._builder.build_device.side_effect = [dev0, dev1, dev2]


        result = app.scan()
        assert len(result) == 2
        assert "2-0" not in app._devices

    def test_all_fail_returns_empty(self, app):
        detected_list = [_detected(f"2-{i}") for i in range(3)]
        devices = []
        for i in range(3):
            d = _mock_lcd_device(f"2-{i}")
            d.connect.side_effect = RuntimeError("fail")
            devices.append(d)

        app._builder.build_detect_fn.return_value = lambda: detected_list
        app._builder.build_device.side_effect = devices

        result = app.scan()
        assert result == []
        assert not app.has_lcd


# ── bootstrap() ──────────────────────────────────────────────────────────────

class TestBootstrap:
    def test_calls_init_platform_then_scans(self, app):
        """bootstrap() must call init_platform then scan()."""
        scan_called = []
        original_scan = app.scan

        def tracking_scan():
            scan_called.append(True)
            return original_scan()

        app.scan = tracking_scan

        with patch.object(app, 'init_platform') as mock_init:
            app.bootstrap()

        mock_init.assert_called_once()
        assert len(scan_called) == 1

    def test_passes_renderer_factory_to_init_platform(self, app):
        renderer_factory = MagicMock()
        with patch.object(app, 'init_platform') as mock_init:
            app.bootstrap(renderer_factory=renderer_factory)

        mock_init.assert_called_once()
        assert mock_init.call_args[1].get('renderer_factory') is renderer_factory

    def test_returns_scan_results(self, app):
        detected = _detected("2-1")
        lcd_dev = _mock_lcd_device("2-1")
        app._builder.build_detect_fn.return_value = lambda: [detected]
        app._builder.build_device.return_value = lcd_dev

        with patch.object(app, 'init_platform'):
            result = app.bootstrap()

        assert len(result) == 1

    def test_no_renderer_factory_still_works(self, app):
        with patch.object(app, 'init_platform') as mock_init:
            app.bootstrap()

        assert mock_init.call_args[1].get('renderer_factory') is None

    def test_bootstrap_calls_ensure_data_blocking(self, app):
        """bootstrap() must call _ensure_data_blocking after scan()."""
        with patch.object(app, 'init_platform'), \
             patch.object(app, '_ensure_data_blocking') as mock_ensure:
            app.bootstrap()
        mock_ensure.assert_called_once_with()


# ── _ensure_data_blocking() ───────────────────────────────────────────────────

class TestEnsureDataBlocking:
    @pytest.fixture()
    def lcd_app_with_ensure(self, app):
        detected = _detected("2-1")
        lcd_dev = _mock_lcd_device("2-1", resolution=(320, 320))
        app._builder.build_detect_fn.return_value = lambda: [detected]
        app._builder.build_device.return_value = lcd_dev

        app.scan()
        return app, lcd_dev

    def test_calls_ensure_fn_with_resolution(self, lcd_app_with_ensure):
        app, _ = lcd_app_with_ensure
        called = []
        app._ensure_data_fn = lambda w, h, progress_fn=None: called.append((w, h))
        app._ensure_data_blocking()
        assert (320, 320) in called

    def test_no_ensure_fn_is_noop(self, lcd_app_with_ensure):
        app, _ = lcd_app_with_ensure
        app._ensure_data_fn = None
        app._ensure_data_blocking()  # must not raise

    def test_deduplicates_same_resolution(self, app):
        """Two LCD devices at the same resolution → ensure_fn called once."""
        detected_list = [_detected("2-1"), _detected("2-2")]
        devices = [_mock_lcd_device("2-1"), _mock_lcd_device("2-2")]
        app._builder.build_detect_fn.return_value = lambda: detected_list
        app._builder.build_device.side_effect = devices

        app.scan()

        called = []
        app._ensure_data_fn = lambda w, h, progress_fn=None: called.append((w, h))
        app._ensure_data_blocking()
        assert called.count((320, 320)) == 1

    def test_ensure_fn_receives_progress_callback(self, lcd_app_with_ensure):
        """ensure_fn must receive a progress_fn kwarg that fires BOOTSTRAP_PROGRESS."""
        from trcc.core.app import AppEvent, AppObserver
        app, _ = lcd_app_with_ensure
        events: list[str] = []

        class _Obs(AppObserver):
            def on_app_event(self, event: AppEvent, data: object) -> None:
                if event == AppEvent.BOOTSTRAP_PROGRESS:
                    events.append(str(data))

        app.register(_Obs())
        app._ensure_data_fn = lambda w, h, progress_fn=None: (
            progress_fn(f"Downloading {w}x{h}...") if progress_fn else None
        )
        app._ensure_data_blocking()
        assert any("320" in m for m in events)


# ── device_connected() / device_lost() ───────────────────────────────────────

class TestHotPlug:
    def test_device_connected_wires_lcd_bus(self, app):
        detected = _detected("2-1")
        lcd_dev = _mock_lcd_device("2-1")
        app._builder.build_device.return_value = lcd_dev


        app.device_connected(detected)
        assert app.has_lcd
        assert "2-1" in app._devices

    def test_device_connected_notifies_observer(self, app):
        detected = _detected("2-1")
        lcd_dev = _mock_lcd_device("2-1")
        app._builder.build_device.return_value = lcd_dev


        observer = MagicMock()
        app.register(observer)
        app.device_connected(detected)
        observer.on_app_event.assert_called_with(AppEvent.DEVICE_CONNECTED, lcd_dev)

    def test_device_connected_connect_failure_not_added(self, app):
        detected = _detected("2-1")
        lcd_dev = _mock_lcd_device("2-1")
        lcd_dev.connect.side_effect = RuntimeError("fail")
        app._builder.build_device.return_value = lcd_dev

        app.device_connected(detected)
        assert "2-1" not in app._devices

    def test_device_lost_clears_lcd_bus(self, app):
        detected = _detected("2-1")
        lcd_dev = _mock_lcd_device("2-1")
        app._builder.build_device.return_value = lcd_dev

        app.device_connected(detected)
        assert app.has_lcd

        app.device_lost("2-1")
        assert not app.has_lcd
        assert "2-1" not in app._devices

    def test_device_lost_unknown_path_is_noop(self, app):
        app.device_lost("9-99")  # must not raise

    def test_device_lost_notifies_observer(self, app):
        detected = _detected("2-1")
        lcd_dev = _mock_lcd_device("2-1")
        app._builder.build_device.return_value = lcd_dev

        app.device_connected(detected)

        observer = MagicMock()
        app.register(observer)
        app.device_lost("2-1")
        observer.on_app_event.assert_called_with(AppEvent.DEVICE_LOST, lcd_dev)

    def test_device_lost_clears_led_bus(self, app):
        detected = _detected_led("2-2")
        led_dev = _mock_led_device("2-2")
        app._builder.build_device.return_value = led_dev

        app.device_connected(detected)
        assert app.has_led

        app.device_lost("2-2")
        assert not app.has_led


# ── IPC handler injection via _wire_bus ───────────────────────────────────────

class TestWireBusIpcInjection:
    def test_find_active_fn_injected_into_lcd_device(self, app):
        find_fn = MagicMock()
        app._find_active_fn = find_fn

        detected = _detected("2-1")
        lcd_dev = _mock_lcd_device("2-1")
        app._builder.build_device.return_value = lcd_dev

        app.device_connected(detected)

        assert lcd_dev._find_active_fn is find_fn

    def test_proxy_factory_fn_injected_into_lcd_device(self, app):
        proxy_fn = MagicMock()
        app._proxy_factory_fn = proxy_fn

        detected = _detected("2-1")
        lcd_dev = _mock_lcd_device("2-1")
        app._builder.build_device.return_value = lcd_dev

        app.device_connected(detected)

        assert lcd_dev._proxy_factory_fn is proxy_fn
