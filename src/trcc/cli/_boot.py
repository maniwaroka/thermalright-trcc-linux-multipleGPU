"""CLI composition root — builds a Trcc ready for one-shot use.

The CLI is an adapter; this module wires concrete dependencies
(offscreen Qt renderer, device discovery) into the framework-neutral
Trcc. Every CLI subcommand calls `trcc()` once and exits.

Composition pattern:
    from trcc.cli._boot import trcc
    result = trcc().lcd.set_brightness(0, 50)
    typer.echo(result.format())
    return result.exit_code
"""
from __future__ import annotations

from trcc.core.trcc import Trcc


def trcc() -> Trcc:
    """Build + bootstrap + attach offscreen Qt renderer + discover devices.

    Returns a ready Trcc for the current CLI invocation. One-shot — the
    process exits after the subcommand runs, so no cleanup needed here.
    """
    from trcc.cli import _make_cli_renderer
    app = Trcc.for_current_os()
    app.bootstrap()
    app.with_renderer(_make_cli_renderer())
    app.discover()
    return app
