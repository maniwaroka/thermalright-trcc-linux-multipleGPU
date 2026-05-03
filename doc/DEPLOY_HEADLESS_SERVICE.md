# Headless Service Deployment — TRCC Linux

> Deploy TRCC as a systemd user service on headless servers (no display).
> The app runs the full metrics loop (50ms sensor polling, video playback, overlay rendering) with `QT_QPA_PLATFORM=offscreen`.

## Architecture

### Changes Made

Two files were modified to support headless mode:

#### 1. `src/trcc/ui/cli/__init__.py` — Conditional offscreen clear

In `gui()`, only clear `QT_QPA_PLATFORM=offscreen` if a display is actually available:

```python
_display = os.environ.get("DISPLAY")
_has_display = (
    _display is not None
    and subprocess.run(
        ["xdpyinfo"], capture_output=True,
        env={**os.environ, "DISPLAY": _display},
    ).returncode == 0
)
if _has_display:
    os.environ.pop('QT_QPA_PLATFORM', None)
```

If no display, the offscreen environment variable is preserved, allowing `launch()` to run the metrics loop headlessly.

#### 2. `src/trcc/ui/gui/__init__.py` — Headless code path in `launch()`

Detect offscreen mode and skip GUI window creation while keeping:
- Device discovery and connection
- Theme loading (including video backgrounds)
- Sensor polling loop (50ms tick)
- Qt event loop (`qapp.exec()`)

```python
is_offscreen = os.environ.get("QT_QPA_PLATFORM") == "offscreen"

if is_offscreen:
    # Headless: no splash window
    app.bootstrap(renderer_factory=QtRenderer)
    system_svc = app.build_system()
    app.set_system(system_svc)

    # Create TRCCApp but keep it hidden — preserves all handler logic
    window = _TRCCApp(system_svc=system_svc, platform=platform, decorated=False)
    ipc_server = IPCServer()
    ipc_server.start()
    window._ipc_server = ipc_server
    app.register(window)
    app._notify(AppEvent.DEVICES_CHANGED, list(app._devices.values()))
    app.start_metrics_loop()
    signal.signal(signal.SIGINT, lambda *_: qapp.quit())
    return qapp.exec()
```

### Why Create TRCCApp Even in Headless Mode

The `DEVICES_CHANGED` event is handled by `TRCCApp.on_app_event()`, which creates device handlers (`LCDHandler`, `LEDHandler`). Without the window, handlers wouldn't be created and the metrics loop would have no way to send frames to the LCD.

The window is created but never shown — it acts as the event dispatcher for device handlers while staying invisible in offscreen mode.

## Deployment

### 1. Build and Install

```bash
# Build wheel
python -m build --wheel

# Upload to server
scp dist/trcc_linux-*.whl alice@<server>:/tmp/

# Install (skip deps — PySide6 already present)
/home/alice/.venv/bin/python3 -m pip install --force-reinstall --no-deps /tmp/trcc_linux-*.whl
```

### 2. Create systemd Service

File: `~/.config/systemd/user/trcc-daemon.service`

```ini
[Unit]
Description=TRCC LCD Display Daemon (headless sensor loop)
Documentation=https://github.com/Lexonight1/thermalright-trcc-linux
After=default.target dbus.service

[Service]
Type=simple
ExecStart=/home/alice/.venv/bin/python3 -m trcc.ui.cli gui --resume
Restart=on-failure
RestartSec=5

Environment=QT_QPA_PLATFORM=offscreen
Environment=HOME=/home/alice
Environment=PYTHONUNBUFFERED=1

StandardOutput=journal
StandardError=journal
SyslogIdentifier=trcc-daemon

[Install]
WantedBy=default.target
```

**Important:** Write the file directly — do NOT use a symlink to `/tmp`. The service file must persist on disk.

### 3. Enable and Start

```bash
systemctl --user daemon-reload
systemctl --user enable trcc-daemon.service
systemctl --user start trcc-daemon.service
```

### 4. Verify

```bash
# Service status
systemctl --user status trcc-daemon.service

# Check frames are being sent
grep -c "send_frame" ~/.trcc/trcc.log

# Check video playback
tail -20 ~/.trcc/trcc.log | grep "_on_video_tick"

# View logs
journalctl --user -u trcc-daemon -f
```

Expected log output:
- `[TRCC] Starting LCD Control Center...`
- Device connected and initialized
- `_on_video_tick: sending encoded frame N`
- `LY frame sent: 1920x462, ... bytes, ... chunks`

## Troubleshooting

### Service fails to start

```bash
# Check if service file exists (not a broken symlink)
ls -la ~/.config/systemd/user/trcc-daemon.service
# Should show a regular file, not a symlink to /tmp

# Fix if broken
rm -f ~/.config/systemd/user/trcc-daemon.service
# Recreate the file (see section 2 above)

# Reload and restart
systemctl --user daemon-reload
systemctl --user restart trcc-daemon.service
```

### LCD shows "in use by another process"

Another TRCC instance is holding the device lock. Kill it:

```bash
pkill -f "trcc.ui.cli"
rm -f /tmp/.trcc-linux_instance_lock
systemctl --user restart trcc-daemon.service
```

### No frames being sent

Check logs for bootstrap errors:

```bash
journalctl --user -u trcc-daemon -n 50 --no-pager
```

Common issues:
- `ControllerBuilder: renderer not set` — the wheel wasn't rebuilt after the code changes
- `Failed to connect to device` — check USB connection and VID/PID in config

### High memory usage

The service uses ~1.2GB RAM. This is normal:
- PySide6 ~600MB
- Qt image buffers for 1920x462 rendering
- Video frame cache

## Maintenance

```bash
# Restart after code update
systemctl --user restart trcc-daemon.service

# Disable autostart
systemctl --user disable trcc-daemon.service

# View real-time logs
journalctl --user -u trcc-daemon -f

# Check if device is being polled
tail -f ~/.trcc/trcc.log | grep "METRICS_UPDATED\|send_frame"
```
