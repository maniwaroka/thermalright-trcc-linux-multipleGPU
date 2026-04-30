"""CLI composition root — builds a Trcc ready for one-shot use.

The CLI is an adapter; this module wires concrete dependencies
(offscreen Qt renderer, device discovery, sensor metrics) into the
framework-neutral Trcc. Every CLI subcommand calls `trcc()` once and
exits.

Composition pattern (production):
    from trcc.ui.cli._boot import trcc
    result = trcc().lcd.set_brightness(0, 50)
    typer.echo(result.format())
    return result.exit_code

Test/dev injection — pass a Platform explicitly:
    from tests.mock_platform import MockPlatform
    from trcc.ui.cli._boot import trcc
    app = trcc(MockPlatform(specs))   # subsequent trcc() calls reuse it
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from trcc.core.trcc import Trcc

if TYPE_CHECKING:
    from trcc.core.ports import Platform

# Cached per-process Trcc — built on first call (production: from
# `make_platform()`; test/dev: from the injected platform). Subsequent
# calls return the cached instance so commands within one CLI invocation
# share state and devices stay open for the duration.
_cached: Trcc | None = None


def trcc(platform: Platform | None = None) -> Trcc:
    """Build + bootstrap + attach offscreen Qt renderer + discover devices.

    Caches per process — first call builds, subsequent calls reuse. Pass
    ``platform`` to inject a specific Platform (dev/mock_cli, tests);
    omit for production where ``make_platform()`` handles OS detection
    and ``TRCC_MOCK``.

    If a ``TrccApp`` has already been initialised (production CLI/GUI
    composition root path), return its composed inner ``Trcc`` — single
    source of truth for connected devices, no duplicate registries.

    Also seeds each connected device with one fresh metrics snapshot so
    sensor-linked LED modes (temp_linked, load_linked) and overlay
    sensors render real values when the CLI is the only UI running on a
    headless box (issue #130).
    """
    global _cached
    if _cached is not None:
        return _cached

    # Production path: TrccApp.init() ran first via cli/__init__.py::main().
    # Reuse its composed Trcc so callers see the same connected devices.
    from trcc.core.app import TrccApp
    if TrccApp._instance is not None and platform is None:
        inner = TrccApp._instance._trcc
        # If TrccApp hasn't scanned yet (init_platform without scan), do it
        # now so callers get connected devices on first access.
        if not inner:
            TrccApp._instance.scan()
        _cached = inner
        return _cached

    from trcc.core.builder import ControllerBuilder
    from trcc.services.system import set_instance
    from trcc.ui.cli import _make_cli_renderer

    app = Trcc(platform) if platform is not None else Trcc.for_current_os()
    app.bootstrap()
    app.with_renderer(_make_cli_renderer())
    app.discover()

    sys_svc = ControllerBuilder(app._platform).build_system()
    set_instance(sys_svc)
    metrics = sys_svc.all_metrics
    for device in app:
        device.update_metrics(metrics)

    _cached = app
    return _cached
