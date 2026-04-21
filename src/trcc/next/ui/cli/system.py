"""CLI `system` group — setup, sensors, diagnostics."""
from __future__ import annotations

import typer

from ...core.commands import GetPlatformInfo, ReadSensors, RunSetup
from ._ctx import get_app

app = typer.Typer(help="System-level operations (setup, sensors, info).",
                  no_args_is_help=True)


@app.command("setup")
def setup(
    yes: bool = typer.Option(False, "--yes", "-y",
                             help="Non-interactive (assume yes to prompts)"),
) -> None:
    """Run the OS-specific setup (udev rules on Linux, WinUSB guide on Windows)."""
    result = get_app().dispatch(RunSetup(interactive=not yes))
    typer.echo(result.message)
    for warning in result.warnings:
        typer.echo(f"  warning: {warning}", err=True)
    raise typer.Exit(code=result.exit_code)


@app.command("sensors")
def sensors() -> None:
    """Print current sensor readings."""
    result = get_app().dispatch(ReadSensors())
    if not result.readings:
        typer.echo("No sensor readings available.")
        return
    for reading in result.readings:
        typer.echo(
            f"  {reading.sensor_id}  {reading.value:.2f} {reading.unit}"
            f"  ({reading.category})"
        )


@app.command("info")
def info() -> None:
    """Show platform info (distro, install method, config dir, permissions)."""
    r = get_app().dispatch(GetPlatformInfo())
    typer.echo(f"Distro:   {r.distro_name}")
    typer.echo(f"Install:  {r.install_method}")
    typer.echo(f"Config:   {r.config_dir}")
    typer.echo(f"Data:     {r.data_dir}")
    typer.echo(f"Logs:     {r.log_file}")
    if r.permission_warnings:
        typer.echo("\nWarnings:")
        for w in r.permission_warnings:
            typer.echo(f"  {w}")
    else:
        typer.echo("\nPermissions: OK")
