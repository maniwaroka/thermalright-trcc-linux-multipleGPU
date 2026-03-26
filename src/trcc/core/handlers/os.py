"""OS command handler — driving port boundary for platform/OS operations.

OSCommandHandler receives injected callables instead of TrccApp or
ControllerBuilder — true DIP. The handler knows nothing about the
application container; it only calls the operations it was given.

TrccApp.build_os_bus() injects its own methods:
    build_os_bus(
        bootstrap_fn=self._builder.bootstrap,
        scan_fn=lambda: self.scan(),
        ensure_data_fn=lambda: self._ensure_data_blocking(),
        set_renderer_fn=self.set_renderer,
        has_device_fn=lambda path: path in self._devices,
        build_setup_fn=self._builder.build_setup,
        list_themes_fn=list_available,
        download_pack_fn=download_pack,
    )
"""
from __future__ import annotations

from typing import Any, Callable

import trcc.conf as _conf

from ..command_bus import (
    Command,
    CommandBus,
    CommandResult,
    LoggingMiddleware,
    TimingMiddleware,
)
from ..commands.initialize import (
    DiscoverDevicesCommand,
    DownloadThemesCommand,
    InitPlatformCommand,
    InstallDesktopCommand,
    SetLanguageCommand,
    SetupPlatformCommand,
    SetupPolkitCommand,
    SetupSelinuxCommand,
    SetupUdevCommand,
    SetupWinUsbCommand,
)
from ..i18n import LANGUAGE_NAMES


class OSCommandHandler:
    """Handles all OS/platform commands — one __call__, one match statement.

    Closes over injected callables only — never imports or holds TrccApp.
    This keeps the driving port boundary clean: the handler depends on
    abstractions (callables) not on the concrete application container.
    """

    __slots__ = (
        '_bootstrap',
        '_set_renderer',
        '_scan',
        '_ensure_data',
        '_has_device',
        '_build_setup',
        '_list_themes',
        '_download_pack',
    )

    def __init__(
        self,
        bootstrap_fn: Callable[[int], None],
        set_renderer_fn: Callable[[Any], None],
        scan_fn: Callable[[], list[Any]],
        ensure_data_fn: Callable[[], None],
        has_device_fn: Callable[[str], bool],
        build_setup_fn: Callable[[], Any],
        list_themes_fn: Callable[[], None],
        download_pack_fn: Callable[[str, bool], int],
    ) -> None:
        self._bootstrap = bootstrap_fn
        self._set_renderer = set_renderer_fn
        self._scan = scan_fn
        self._ensure_data = ensure_data_fn
        self._has_device = has_device_fn
        self._build_setup = build_setup_fn
        self._list_themes = list_themes_fn
        self._download_pack = download_pack_fn

    def __call__(self, cmd: Command) -> CommandResult:
        match cmd:
            case InitPlatformCommand(verbosity=verbosity, renderer_factory=rf):
                self._bootstrap(verbosity)
                if rf is not None:
                    self._set_renderer(rf())
                return CommandResult.ok(message="platform ready")

            case DiscoverDevicesCommand(path=path):
                devices = self._scan()
                if path and not self._has_device(path):
                    return CommandResult.fail(f"Device not found: {path}")
                self._ensure_data()
                return CommandResult.ok(
                    message=f"{len(devices)} device(s) found",
                    devices=[getattr(d, 'device_path', str(d)) for d in devices],
                )

            case SetLanguageCommand(code=code):
                if code not in LANGUAGE_NAMES:
                    return CommandResult.fail(f"Unknown language code: {code}")
                _conf.settings.lang = code
                return CommandResult.ok(message=f"Language set to {code}")

            case SetupPlatformCommand(auto_yes=auto_yes):
                rc = self._build_setup().run(auto_yes=auto_yes)
                return CommandResult.ok() if rc == 0 else CommandResult.fail(
                    "Setup failed")

            case SetupUdevCommand(dry_run=dry_run):
                rc = self._build_setup().setup_udev(dry_run=dry_run)
                return CommandResult.ok() if rc == 0 else CommandResult.fail(
                    "udev setup failed")

            case SetupSelinuxCommand():
                rc = self._build_setup().setup_selinux()
                return CommandResult.ok() if rc == 0 else CommandResult.fail(
                    "SELinux setup failed")

            case SetupPolkitCommand():
                rc = self._build_setup().setup_polkit()
                return CommandResult.ok() if rc == 0 else CommandResult.fail(
                    "polkit setup failed")

            case InstallDesktopCommand():
                rc = self._build_setup().install_desktop()
                return CommandResult.ok() if rc == 0 else CommandResult.fail(
                    "Desktop install failed")

            case SetupWinUsbCommand():
                rc = self._build_setup().setup_winusb()
                return CommandResult.ok() if rc == 0 else CommandResult.fail(
                    "WinUSB setup failed")

            case DownloadThemesCommand(pack=pack, force=force):
                if not pack:
                    self._list_themes()
                    return CommandResult.ok(message="Listed available theme packs")
                rc = self._download_pack(pack, force)
                return CommandResult.ok() if rc == 0 else CommandResult.fail(
                    f"Download failed: {pack}")

            case _:
                return CommandResult.fail(
                    f"BUG: unhandled OS command {type(cmd).__name__}")

    def __repr__(self) -> str:
        return "OSCommandHandler()"


def build_os_bus(
    bootstrap_fn: Callable[[int], None],
    set_renderer_fn: Callable[[Any], None],
    scan_fn: Callable[[], list[Any]],
    ensure_data_fn: Callable[[], None],
    has_device_fn: Callable[[str], bool],
    build_setup_fn: Callable[[], Any],
    list_themes_fn: Callable[[], None],
    download_pack_fn: Callable[[str, bool], int],
) -> CommandBus:
    """Build a CommandBus for OS/platform operations.

    All three UI adapters (CLI, API, GUI) dispatch OS commands here.
    The handler delegates to injected callables — callers are blind to the OS.
    """
    h = OSCommandHandler(
        bootstrap_fn=bootstrap_fn,
        set_renderer_fn=set_renderer_fn,
        scan_fn=scan_fn,
        ensure_data_fn=ensure_data_fn,
        has_device_fn=has_device_fn,
        build_setup_fn=build_setup_fn,
        list_themes_fn=list_themes_fn,
        download_pack_fn=download_pack_fn,
    )
    return (CommandBus()
            .add_middleware(LoggingMiddleware())
            .add_middleware(TimingMiddleware(threshold_ms=5000.0))
            .register(InitPlatformCommand, h)
            .register(DiscoverDevicesCommand, h)
            .register(SetLanguageCommand, h)
            .register(SetupPlatformCommand, h)
            .register(SetupUdevCommand, h)
            .register(SetupSelinuxCommand, h)
            .register(SetupPolkitCommand, h)
            .register(InstallDesktopCommand, h)
            .register(SetupWinUsbCommand, h)
            .register(DownloadThemesCommand, h))
