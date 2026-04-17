"""`trcc status` — unified overview via the Trcc command layer.

Shows app state + every connected LCD/LED device in one call. Built
against `Trcc` — same methods that GUI and API use. Proves the universal
command layer works from CLI.
"""
from __future__ import annotations

from dataclasses import asdict

import typer


def status(json_output: bool = False) -> int:
    """Print a full overview: app state + every connected device."""
    from trcc.cli._boot import trcc as boot

    trcc = boot()
    app_snap = trcc.control_center.snapshot()
    discovery = trcc.discover()
    lcd_snaps = [trcc.lcd.snapshot(i) for i in range(len(discovery.lcd_devices))]
    led_snaps = [trcc.led.snapshot(i) for i in range(len(discovery.led_devices))]

    if json_output:
        import json as _json
        payload = {
            'app': asdict(app_snap),
            'lcd_devices': [asdict(s) for s in lcd_snaps],
            'led_devices': [asdict(s) for s in led_snaps],
        }
        typer.echo(_json.dumps(payload, indent=2, default=str))
        return 0

    # Human-readable output.
    typer.echo('─ App ─────────────────────────────────────────')
    typer.echo(f'  version:          {app_snap.version}')
    typer.echo(f'  install method:   {app_snap.install_method}  (distro: {app_snap.distro})')
    typer.echo(f'  autostart:        {"on" if app_snap.autostart else "off"}')
    typer.echo(f'  temp unit:        °{app_snap.temp_unit}')
    typer.echo(f'  language:         {app_snap.language}')
    typer.echo(f'  hdd metrics:      {"on" if app_snap.hdd_enabled else "off"}')
    typer.echo(f'  refresh interval: {app_snap.refresh_interval}s')
    if app_snap.gpu_device:
        typer.echo(f'  gpu:              {app_snap.gpu_device}')

    if not lcd_snaps and not led_snaps:
        typer.echo()
        typer.echo('No devices connected.')
        return 0

    for i, s in enumerate(lcd_snaps):
        typer.echo('')
        typer.echo(f'─ LCD {i} ───────────────────────────────────────')
        typer.echo(f'  connected:     {s.connected}')
        typer.echo(f'  resolution:    {s.resolution[0]}x{s.resolution[1]}')
        typer.echo(f'  brightness:    {s.brightness}%')
        typer.echo(f'  rotation:      {s.rotation}°')
        typer.echo(f'  split mode:    {s.split_mode}')
        typer.echo(f'  overlay:       {"enabled" if s.overlay_enabled else "disabled"}')
        typer.echo(f'  playing video: {s.playing}')
        if s.current_theme:
            typer.echo(f'  theme:         {s.current_theme}')

    for i, s in enumerate(led_snaps):
        typer.echo('')
        typer.echo(f'─ LED {i} ───────────────────────────────────────')
        typer.echo(f'  connected:   {s.connected}')
        typer.echo(f'  style id:    {s.style_id}')
        typer.echo(f'  mode:        {s.mode}')
        typer.echo(f'  color:       rgb{s.color}')
        typer.echo(f'  brightness:  {s.brightness}%')
        typer.echo(f'  global on:   {s.global_on}')
        if s.zones:
            typer.echo(f'  zones:       {len(s.zones)}')
            typer.echo(f'  zone sync:   {s.zone_sync}')
        typer.echo(f'  test mode:   {s.test_mode}')

    return 0
