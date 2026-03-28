"""Tests for core/handlers/os.py — OSCommandHandler with injected callables."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from trcc.core.commands.initialize import (
    DiscoverDevicesCommand,
    DownloadThemesCommand,
    InitPlatformCommand,
    InstallDesktopCommand,
    SetLanguageCommand,
    SetMetricsRefreshCommand,
    SetupPlatformCommand,
    SetupPolkitCommand,
    SetupSelinuxCommand,
    SetupUdevCommand,
    SetupWinUsbCommand,
)
from trcc.core.handlers.os import OSCommandHandler, build_os_bus


def _defaults() -> dict:
    return dict(
        bootstrap_fn=MagicMock(),
        set_renderer_fn=MagicMock(),
        scan_fn=MagicMock(return_value=[]),
        ensure_data_fn=MagicMock(),
        has_device_fn=MagicMock(return_value=True),
        build_setup_fn=MagicMock(return_value=MagicMock(
            run=MagicMock(return_value=0),
            setup_udev=MagicMock(return_value=0),
            setup_selinux=MagicMock(return_value=0),
            setup_polkit=MagicMock(return_value=0),
            install_desktop=MagicMock(return_value=0),
            setup_winusb=MagicMock(return_value=0),
        )),
        list_themes_fn=MagicMock(),
        download_pack_fn=MagicMock(return_value=0),
        wake_metrics_fn=MagicMock(),
    )


def _make_handler(**overrides) -> OSCommandHandler:
    return OSCommandHandler(**{**_defaults(), **overrides})


def _make_bus(**overrides):
    return build_os_bus(**{**_defaults(), **overrides})


# ── InitPlatformCommand ───────────────────────────────────────────────────────

class TestInitPlatformHandler:
    def test_calls_bootstrap_with_verbosity(self):
        bootstrap = MagicMock()
        h = _make_handler(bootstrap_fn=bootstrap)
        h(InitPlatformCommand(verbosity=3))
        bootstrap.assert_called_once_with(3)

    def test_calls_set_renderer_when_factory_provided(self):
        set_renderer = MagicMock()
        renderer = MagicMock()
        factory = MagicMock(return_value=renderer)
        h = _make_handler(set_renderer_fn=set_renderer)
        h(InitPlatformCommand(renderer_factory=factory))
        factory.assert_called_once()
        set_renderer.assert_called_once_with(renderer)

    def test_no_factory_skips_set_renderer(self):
        set_renderer = MagicMock()
        h = _make_handler(set_renderer_fn=set_renderer)
        h(InitPlatformCommand())
        set_renderer.assert_not_called()

    def test_returns_ok(self):
        h = _make_handler()
        result = h(InitPlatformCommand())
        assert result


# ── DiscoverDevicesCommand ────────────────────────────────────────────────────

class TestDiscoverHandler:
    def test_calls_scan(self):
        scan = MagicMock(return_value=[])
        h = _make_handler(scan_fn=scan)
        h(DiscoverDevicesCommand())
        scan.assert_called_once()

    def test_calls_ensure_data(self):
        ensure = MagicMock()
        h = _make_handler(ensure_data_fn=ensure)
        h(DiscoverDevicesCommand())
        ensure.assert_called_once()

    def test_path_not_found_returns_fail(self):
        h = _make_handler(has_device_fn=lambda p: False)
        result = h(DiscoverDevicesCommand(path="/dev/sg99"))
        assert not result

    def test_no_path_returns_ok(self):
        h = _make_handler()
        result = h(DiscoverDevicesCommand())
        assert result


# ── SetLanguageCommand ────────────────────────────────────────────────────────

class TestSetLanguageHandler:
    def test_valid_code_updates_settings(self):
        h = _make_handler()
        with patch('trcc.conf.settings') as mock_settings:
            h(SetLanguageCommand(code='en'))
        assert mock_settings.lang == 'en'

    def test_unknown_code_returns_fail(self):
        h = _make_handler()
        result = h(SetLanguageCommand(code='xx_unknown'))
        assert not result

    def test_unknown_code_does_not_update_settings(self):
        h = _make_handler()
        with patch('trcc.conf.settings') as mock_settings:
            mock_settings.lang = 'en'
            h(SetLanguageCommand(code='xx_unknown'))
            assert mock_settings.lang == 'en'


# ── Setup commands ────────────────────────────────────────────────────────────

class TestSetupCommandHandlers:
    def _setup(self, rc: int = 0) -> tuple[OSCommandHandler, MagicMock]:
        setup = MagicMock(
            run=MagicMock(return_value=rc),
            setup_udev=MagicMock(return_value=rc),
            setup_selinux=MagicMock(return_value=rc),
            setup_polkit=MagicMock(return_value=rc),
            install_desktop=MagicMock(return_value=rc),
            setup_winusb=MagicMock(return_value=rc),
        )
        return _make_handler(build_setup_fn=lambda: setup), setup

    def test_setup_platform_calls_run(self):
        h, setup = self._setup()
        h(SetupPlatformCommand(auto_yes=True))
        setup.run.assert_called_once_with(auto_yes=True)

    def test_setup_udev_calls_setup_udev(self):
        h, setup = self._setup()
        h(SetupUdevCommand(dry_run=True))
        setup.setup_udev.assert_called_once_with(dry_run=True)

    def test_setup_selinux_calls_setup_selinux(self):
        h, setup = self._setup()
        h(SetupSelinuxCommand())
        setup.setup_selinux.assert_called_once()

    def test_setup_polkit_calls_setup_polkit(self):
        h, setup = self._setup()
        h(SetupPolkitCommand())
        setup.setup_polkit.assert_called_once()

    def test_install_desktop_calls_install_desktop(self):
        h, setup = self._setup()
        h(InstallDesktopCommand())
        setup.install_desktop.assert_called_once()

    def test_setup_winusb_calls_setup_winusb(self):
        h, setup = self._setup()
        h(SetupWinUsbCommand())
        setup.setup_winusb.assert_called_once()

    def test_fail_rc_returns_fail(self):
        h, _ = self._setup(rc=1)
        for cmd in (SetupPlatformCommand(), SetupUdevCommand(),
                    SetupSelinuxCommand(), SetupPolkitCommand(),
                    InstallDesktopCommand(), SetupWinUsbCommand()):
            result = h(cmd)
            assert not result, f"{cmd} should return fail on rc=1"


# ── DownloadThemesCommand ─────────────────────────────────────────────────────

class TestDownloadThemesHandler:
    def test_no_pack_calls_list_themes(self):
        list_fn = MagicMock()
        h = _make_handler(list_themes_fn=list_fn)
        h(DownloadThemesCommand(pack=''))
        list_fn.assert_called_once()

    def test_pack_calls_download_pack(self):
        dl = MagicMock(return_value=0)
        h = _make_handler(download_pack_fn=dl)
        h(DownloadThemesCommand(pack='320x320'))
        dl.assert_called_once_with('320x320', False)

    def test_pack_force_passed_through(self):
        dl = MagicMock(return_value=0)
        h = _make_handler(download_pack_fn=dl)
        h(DownloadThemesCommand(pack='320x320', force=True))
        dl.assert_called_once_with('320x320', True)

    def test_download_fail_returns_fail(self):
        h = _make_handler(download_pack_fn=MagicMock(return_value=1))
        result = h(DownloadThemesCommand(pack='320x320'))
        assert not result


# ── SetMetricsRefreshCommand ──────────────────────────────────────────────────

class TestSetMetricsRefreshHandler:
    def test_saves_interval_to_settings(self, tmp_config):
        import trcc.conf as _conf
        wake = MagicMock()
        h = _make_handler(wake_metrics_fn=wake)
        h(SetMetricsRefreshCommand(interval=5))
        assert _conf.settings.refresh_interval == 5

    def test_wakes_metrics_loop(self):
        wake = MagicMock()
        h = _make_handler(wake_metrics_fn=wake)
        h(SetMetricsRefreshCommand(interval=3))
        wake.assert_called_once()

    def test_clamps_below_min(self, tmp_config):
        import trcc.conf as _conf
        h = _make_handler()
        h(SetMetricsRefreshCommand(interval=0))
        assert _conf.settings.refresh_interval == 1

    def test_clamps_above_max(self, tmp_config):
        import trcc.conf as _conf
        h = _make_handler()
        h(SetMetricsRefreshCommand(interval=999))
        assert _conf.settings.refresh_interval == 100

    def test_returns_ok(self):
        h = _make_handler()
        result = h(SetMetricsRefreshCommand(interval=10))
        assert result


# ── build_os_bus ──────────────────────────────────────────────────────────────

class TestBuildOsBus:
    def test_returns_command_bus(self):
        from trcc.core.command_bus import CommandBus
        assert isinstance(_make_bus(), CommandBus)

    def test_dispatches_init_platform(self):
        bootstrap = MagicMock()
        bus = _make_bus(bootstrap_fn=bootstrap)
        bus.dispatch(InitPlatformCommand(verbosity=2))
        bootstrap.assert_called_once_with(2)


# ── repr ──────────────────────────────────────────────────────────────────────

class TestOSHandlerRepr:
    def test_repr(self):
        assert "OSCommandHandler" in repr(_make_handler())
