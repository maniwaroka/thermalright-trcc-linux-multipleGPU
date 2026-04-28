"""CLI composition root — builds a Trcc ready for one-shot use.

The CLI is an adapter; this module wires concrete dependencies
(offscreen Qt renderer, device discovery, sensor metrics) into the
framework-neutral Trcc. Every CLI subcommand calls `trcc()` once and
exits.

Composition pattern:
    from trcc.ui.cli._boot import trcc
    result = trcc().lcd.set_brightness(0, 50)
    typer.echo(result.format())
    return result.exit_code
"""
from __future__ import annotations

from trcc.core.trcc import Trcc


def trcc() -> Trcc:
    """Build + bootstrap + attach offscreen Qt renderer + discover devices.

    Also seeds each connected device with one fresh metrics snapshot so
    sensor-linked LED modes (temp_linked, load_linked) and overlay
    sensors render real values when the CLI is the only UI running on a
    headless box (issue #130). One-shot — the process exits after the
    subcommand runs, so no cleanup needed here.
    """
    from trcc.core.builder import ControllerBuilder
    from trcc.services.system import set_instance
    from trcc.ui.cli import _make_cli_renderer

    app = Trcc.for_current_os()
    app.bootstrap()
    app.with_renderer(_make_cli_renderer())
    app.discover()

    sys_svc = ControllerBuilder.for_current_os().build_system()
    set_instance(sys_svc)
    metrics = sys_svc.all_metrics
    for device in app:
        device.update_metrics(metrics)

    return app
