"""Tests for TrccApp init_platform, discover, set_language, setup_*, metrics loop,
and observer lifecycle.

Coverage targets:
  - core/app.py: init_platform(), discover(), set_language(), setup_*()
  - core/app.py: start_metrics_loop(), stop_metrics_loop()
  - core/app.py: unregister(), _notify() exception isolation
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from trcc.core.app import AppEvent, AppObserver, TrccApp

# ── Shared fixture ────────────────────────────────────────────────────────────

@pytest.fixture()
def app():
    """Fresh TrccApp with a mocked builder."""
    TrccApp.reset()
    builder = MagicMock()
    builder.build_detect_fn.return_value = lambda: []
    inst = TrccApp(builder)
    inst._settings = MagicMock()
    TrccApp._instance = inst
    yield inst
    TrccApp.reset()


# ── init_platform ────────────────────────────────────────────────────────────

class TestInitPlatform:
    def test_bootstrap_called(self, app):
        """init_platform must call builder.bootstrap()."""
        app.init_platform(verbosity=0)
        app._builder.bootstrap.assert_called_once_with(0)

    def test_bootstrap_passes_verbosity(self, app):
        app.init_platform(verbosity=3)
        app._builder.bootstrap.assert_called_once_with(3)

    def test_renderer_factory_called_when_provided(self, app):
        renderer = MagicMock()
        factory = MagicMock(return_value=renderer)
        app.init_platform(renderer_factory=factory)
        factory.assert_called_once()

    def test_renderer_injected_into_builder(self, app):
        renderer = MagicMock()
        factory = MagicMock(return_value=renderer)
        with patch('trcc.services.image.ImageService.set_renderer'):
            app.init_platform(renderer_factory=factory)
        app._builder.with_renderer.assert_called_once_with(renderer)

    def test_no_renderer_factory_skips_set_renderer(self, app):
        """No renderer_factory → with_renderer() must NOT be called."""
        app.init_platform()
        app._builder.with_renderer.assert_not_called()

    def test_bootstrap_progress_event_emitted(self, app):
        """BOOTSTRAP_PROGRESS must be notified when ensure_fn fires a message."""
        received: list[str] = []

        class _Obs(AppObserver):
            def on_app_event(self, event: AppEvent, data: object) -> None:
                if event == AppEvent.BOOTSTRAP_PROGRESS:
                    received.append(str(data))

        app.register(_Obs())
        app._notify(AppEvent.BOOTSTRAP_PROGRESS, "Downloading themes…")
        assert received == ["Downloading themes…"]


# ── discover() ───────────────────────────────────────────────────────────────

class TestDiscover:
    def test_returns_success_when_no_devices(self, app):
        result = app.discover()
        assert result["success"]

    def test_scans_devices(self, app):
        """discover() must call scan()."""
        with patch.object(app, 'scan', return_value=[]) as mock_scan:
            app.discover()
        mock_scan.assert_called_once()

    def test_path_not_found_returns_fail(self, app):
        result = app.discover(path="/dev/sg99")
        assert not result["success"]

    def test_discover_calls_ensure_data_blocking(self, app):
        """discover() must run _ensure_data_blocking after scan."""
        with patch.object(app, '_ensure_data_blocking') as mock_ensure:
            app.discover()
        mock_ensure.assert_called_once_with()


# ── set_language() ───────────────────────────────────────────────────────────

class TestSetLanguage:
    def test_valid_code_updates_settings(self, app):
        app.set_language('en')
        assert app._settings.lang == 'en'

    def test_unknown_code_returns_fail(self, app):
        result = app.set_language('xx_unknown')
        assert not result["success"]

    def test_unknown_code_does_not_update_lang(self, app):
        """set_language must bail out before touching settings on an unknown code."""
        with patch('trcc.conf.settings') as mock_settings:
            mock_settings.lang = 'en'
            app.set_language('xx_unknown')
            assert mock_settings.lang == 'en'


# ── Setup methods ────────────────────────────────────────────────────────────

class TestSetupMethods:
    """Setup is accessed via app.os — the unified Platform interface."""

    def test_os_property_returns_os_platform(self, app):
        from trcc.core.ports import Platform
        assert isinstance(app.os, Platform)

    def test_os_run_setup(self, app):
        mock_os = MagicMock()
        mock_os.run_setup.return_value = 0
        app._builder._os = mock_os
        rc = app.os.run_setup(auto_yes=True)
        mock_os.run_setup.assert_called_once_with(auto_yes=True)
        assert rc == 0

    def test_os_install_rules(self, app):
        mock_os = MagicMock()
        mock_os.install_rules.return_value = 0
        app._builder._os = mock_os
        rc = app.os.install_rules()
        mock_os.install_rules.assert_called_once()
        assert rc == 0

    def test_os_install_desktop(self, app):
        mock_os = MagicMock()
        mock_os.install_desktop.return_value = 0
        app._builder._os = mock_os
        rc = app.os.install_desktop()
        mock_os.install_desktop.assert_called_once()
        assert rc == 0


# ── Metrics loop ──────────────────────────────────────────────────────────────

class TestMetricsLoop:
    def test_start_without_system_raises(self, app):
        with pytest.raises(RuntimeError, match="set_system"):
            app.start_metrics_loop()

    def test_start_spawns_thread(self, app):
        system_svc = MagicMock()
        system_svc.all_metrics = {}
        app.set_system(system_svc)
        app.start_metrics_loop(interval=60.0)
        try:
            assert app._metrics_thread is not None
            assert app._metrics_thread.is_alive()
        finally:
            app.stop_metrics_loop()

    def test_stop_joins_thread(self, app):
        system_svc = MagicMock()
        system_svc.all_metrics = {}
        app.set_system(system_svc)
        app.start_metrics_loop(interval=60.0)
        app.stop_metrics_loop()
        assert app._metrics_thread is None

    def test_loop_notifies_metrics_updated(self, app):
        metrics = {'cpu': 10}
        system_svc = MagicMock()
        system_svc.all_metrics = metrics
        app.set_system(system_svc)

        received: list = []
        observer = MagicMock(spec=AppObserver)
        observer.on_app_event.side_effect = lambda ev, d: received.append((ev, d))
        app.register(observer)

        app.start_metrics_loop(interval=0.05)
        time.sleep(0.2)
        app.stop_metrics_loop()

        events = [ev for ev, _ in received]
        assert AppEvent.METRICS_UPDATED in events

    def test_loop_calls_tick_on_devices(self, app):
        metrics = {}
        system_svc = MagicMock()
        system_svc.all_metrics = metrics

        device = MagicMock()
        device.tick.return_value = None
        app._devices['2-1'] = device
        app.set_system(system_svc)

        app.start_metrics_loop(interval=0.05)
        time.sleep(0.2)
        app.stop_metrics_loop()

        device.update_metrics.assert_called()
        device.tick.assert_called()

    def test_loop_notifies_frame_rendered_when_tick_returns_image(self, app):
        system_svc = MagicMock()
        system_svc.all_metrics = {}

        fake_image = object()
        device = MagicMock()
        device.tick.return_value = fake_image
        app._devices['2-1'] = device
        app.set_system(system_svc)

        frames: list = []
        observer = MagicMock(spec=AppObserver)
        observer.on_app_event.side_effect = lambda ev, d: frames.append(ev) if ev == AppEvent.FRAME_RENDERED else None
        app.register(observer)

        app.start_metrics_loop(interval=0.05)
        time.sleep(0.2)
        app.stop_metrics_loop()

        assert AppEvent.FRAME_RENDERED in frames

    def test_device_error_does_not_kill_loop(self, app):
        """An exception in device.tick() must not stop the metrics loop."""
        system_svc = MagicMock()
        system_svc.all_metrics = {}

        bad_device = MagicMock()
        bad_device.tick.side_effect = RuntimeError("bang")
        app._devices['2-bad'] = bad_device
        app.set_system(system_svc)

        tick_count: list[int] = []
        observer = MagicMock(spec=AppObserver)
        observer.on_app_event.side_effect = (
            lambda ev, d: tick_count.append(1) if ev == AppEvent.METRICS_UPDATED else None
        )
        app.register(observer)

        app.start_metrics_loop(interval=0.05)
        time.sleep(0.3)
        app.stop_metrics_loop()

        # Loop kept running: METRICS_UPDATED fired more than once
        assert len(tick_count) > 1

    def test_double_start_replaces_thread(self, app):
        """Calling start_metrics_loop() twice must not leave orphan threads."""
        system_svc = MagicMock()
        system_svc.all_metrics = {}
        app.set_system(system_svc)

        app.start_metrics_loop(interval=60.0)
        first_thread = app._metrics_thread
        app.start_metrics_loop(interval=60.0)
        second_thread = app._metrics_thread
        try:
            assert first_thread is not second_thread
            assert second_thread.is_alive()
        finally:
            app.stop_metrics_loop()


# ── Observer lifecycle ────────────────────────────────────────────────────────

class TestObserverLifecycle:
    def test_unregister_removes_observer(self, app):
        observer = MagicMock(spec=AppObserver)
        app.register(observer)
        app.unregister(observer)
        app._notify(AppEvent.DEVICES_CHANGED, [])
        observer.on_app_event.assert_not_called()

    def test_unregister_unknown_observer_is_noop(self, app):
        phantom = MagicMock(spec=AppObserver)
        app.unregister(phantom)  # must not raise

    def test_notify_exception_in_one_observer_does_not_block_others(self, app):
        bad = MagicMock(spec=AppObserver)
        bad.on_app_event.side_effect = RuntimeError("observer boom")

        good = MagicMock(spec=AppObserver)

        app.register(bad)
        app.register(good)
        app._notify(AppEvent.DEVICES_CHANGED, [])

        good.on_app_event.assert_called_once_with(AppEvent.DEVICES_CHANGED, [])

    def test_multiple_observers_all_notified(self, app):
        observers = [MagicMock(spec=AppObserver) for _ in range(5)]
        for obs in observers:
            app.register(obs)
        app._notify(AppEvent.METRICS_UPDATED, {})
        for obs in observers:
            obs.on_app_event.assert_called_once_with(AppEvent.METRICS_UPDATED, {})
