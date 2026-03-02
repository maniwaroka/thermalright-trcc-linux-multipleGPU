# TRCC Linux REST API Specification

**Version:** 6.6.2
**Base URL:** `http://127.0.0.1:5000`
**Transport:** HTTP/1.1 (optional TLS via `--tls` flag, auto-generates self-signed cert)

---

## Authentication

Authentication is optional. When enabled via the `--token` flag on `trcc serve`, all endpoints except `/health` require a valid token.

### HTTP Endpoints

Pass the token in the `X-API-Token` header:

```
X-API-Token: your-secret-token
```

### WebSocket Endpoints

Pass the token as a query parameter:

```
ws://127.0.0.1:5000/display/preview/stream?token=your-secret-token
```

### Responses

| Scenario | Status | Body |
|----------|--------|------|
| Token configured, missing/invalid header | `401` | `{"detail": "Invalid token"}` |
| Token not configured | N/A | All requests pass through |
| `/health` endpoint | N/A | Always accessible, no auth required |

---

## Workflow

A typical session follows the **detect -> select -> use** pattern:

1. **Detect** devices on the USB bus: `POST /devices/detect`
2. **Select** a device by its index: `POST /devices/{id}/select`
3. **Use** domain endpoints (`/display/*`, `/led/*`, `/themes/*`, `/system/*`)

Calling display or LED endpoints before selecting a device returns `409 Conflict`.

The `protocol` field on each device determines which endpoint group applies:

- **LCD devices** (`scsi`, `hid`, `bulk`, `ly`): Use `/display/*` and `/themes/*` endpoints
- **LED devices** (`led`): Use `/led/*` endpoints

---

## Error Format

All errors return a JSON object with a `detail` field:

```json
{
  "detail": "Human-readable error message"
}
```

### Status Codes

| Code | Meaning | When |
|------|---------|------|
| `200` | Success | Normal response |
| `400` | Bad Request | Invalid parameters, hex color format, resolution format, theme archive format, unknown metrics category |
| `401` | Unauthorized | Missing or invalid `X-API-Token` header (when auth enabled) |
| `404` | Not Found | Device index out of range, theme not found, cloud theme not on server |
| `409` | Conflict | No device selected (display/LED endpoints), no video playing (pause) |
| `413` | Payload Too Large | Image upload exceeds 10 MB, theme archive exceeds 50 MB |
| `422` | Validation Error | FastAPI request body validation failure (missing fields, type mismatch, value out of range) |
| `500` | Internal Server Error | Send failed, video playback failed, theme import error |
| `503` | Service Unavailable | Cannot discover device resolution, no preview image available |

---

## Endpoints

### Health

#### `GET /health`

Health check. Always accessible, no authentication required.

**Response:**

```json
{
  "status": "ok",
  "version": "6.6.2"
}
```

---

### Devices

#### `GET /devices`

List currently known devices (from last detection scan).

**Response:**

```json
[
  {
    "id": 0,
    "name": "Thermalright Frozen Notte",
    "vid": 13875,
    "pid": 21507,
    "protocol": "hid",
    "resolution": [320, 320],
    "path": "/dev/hidraw3"
  }
]
```

---

#### `POST /devices/detect`

Rescan USB bus for Thermalright LCD and LED devices.

**Response:** Same format as `GET /devices` — returns the updated device list.

```json
[
  {
    "id": 0,
    "name": "Thermalright Frozen Notte",
    "vid": 13875,
    "pid": 21507,
    "protocol": "hid",
    "resolution": [320, 320],
    "path": "/dev/hidraw3"
  },
  {
    "id": 1,
    "name": "Thermalright HR-10 LED",
    "vid": 13875,
    "pid": 21520,
    "protocol": "led",
    "resolution": [0, 0],
    "path": ""
  }
]
```

---

#### `GET /devices/{device_id}`

Get details for a specific device.

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `device_id` | `int` | Zero-based device index |

**Response:**

```json
{
  "id": 0,
  "name": "Thermalright Frozen Notte",
  "vid": 13875,
  "pid": 21507,
  "protocol": "hid",
  "resolution": [320, 320],
  "path": "/dev/hidraw3"
}
```

**Errors:**

| Status | Detail |
|--------|--------|
| `404` | `Device {id} not found` |

---

#### `POST /devices/{device_id}/select`

Select a device by index. Initializes the appropriate dispatcher (LCD or LED) and mounts static file directories for the device's resolution.

If the GUI daemon is running, the API routes commands through IPC instead of managing the device directly.

Re-selecting the same already-active device is a no-op.

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `device_id` | `int` | Zero-based device index |

**Response:**

```json
{
  "selected": "Thermalright Frozen Notte",
  "resolution": [320, 320]
}
```

**Errors:**

| Status | Detail |
|--------|--------|
| `404` | `Device {id} not found` |

---

#### `POST /devices/{device_id}/send`

Upload an image and send it directly to the device LCD.

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `device_id` | `int` | Zero-based device index |

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `rotation` | `int` | `0` | Rotation in degrees (0, 90, 180, 270) |
| `brightness` | `int` | `100` | Brightness percentage (0-100) |

**Request Body:** `multipart/form-data`

| Field | Type | Description |
|-------|------|-------------|
| `image` | `file` | Image file (PNG, JPEG, BMP, etc.). Max 10 MB. |

**Response:**

```json
{
  "sent": true,
  "resolution": [320, 320]
}
```

**Errors:**

| Status | Detail |
|--------|--------|
| `400` | `Invalid image format` |
| `404` | `Device {id} not found` |
| `413` | `Image exceeds 10 MB limit` |
| `500` | `Send failed (device busy or error)` |
| `503` | `Cannot discover device resolution` |

---

### Display

All display endpoints require a selected LCD device. Prefix: `/display`.

Static display operations (color, brightness, rotation, etc.) automatically stop any running video or overlay loop.

#### `POST /display/color`

Send a solid color to the LCD.

**Request Body:**

```json
{
  "hex": "ff0000"
}
```

**Response:**

```json
{
  "success": true
}
```

**Errors:**

| Status | Detail |
|--------|--------|
| `400` | `Invalid hex color (use 6-digit hex, e.g. 'ff0000')` |
| `409` | `No LCD device selected. POST /devices/{id}/select first.` |

---

#### `POST /display/brightness`

Set display brightness level. Persists to device config.

**Request Body:**

```json
{
  "level": 3
}
```

| Field | Type | Range | Description |
|-------|------|-------|-------------|
| `level` | `int` | `1`-`3` | 1 = 25%, 2 = 50%, 3 = 100% |

**Response:**

```json
{
  "success": true
}
```

**Errors:**

| Status | Detail |
|--------|--------|
| `409` | `No LCD device selected. POST /devices/{id}/select first.` |
| `422` | Validation error if level is outside 1-3 range |

---

#### `POST /display/rotation`

Set display rotation. Persists to device config.

**Request Body:**

```json
{
  "degrees": 90
}
```

| Field | Type | Values | Description |
|-------|------|--------|-------------|
| `degrees` | `int` | `0`, `90`, `180`, `270` | Clockwise rotation |

**Response:**

```json
{
  "success": true
}
```

---

#### `POST /display/split`

Set Dynamic Island split mode. Persists to device config.

**Request Body:**

```json
{
  "mode": 1
}
```

| Field | Type | Range | Description |
|-------|------|-------|-------------|
| `mode` | `int` | `0`-`3` | 0 = off, 1-3 = Dynamic Island variants |

**Response:**

```json
{
  "success": true
}
```

---

#### `POST /display/reset`

Reset the device by sending a solid red frame.

**Response:**

```json
{
  "success": true
}
```

---

#### `POST /display/mask`

Upload and apply a mask overlay image (PNG).

**Request Body:** `multipart/form-data`

| Field | Type | Description |
|-------|------|-------------|
| `image` | `file` | Mask PNG image. Max 10 MB. |

**Response:**

```json
{
  "success": true
}
```

**Errors:**

| Status | Detail |
|--------|--------|
| `409` | `No LCD device selected. POST /devices/{id}/select first.` |
| `413` | `Mask image exceeds 10 MB limit` |

---

#### `POST /display/overlay`

Render overlay from a DC config path and optionally send to the device.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `dc_path` | `string` | required | Path to `config1.dc` overlay config file |
| `send` | `bool` | `true` | Whether to send the rendered frame to the device |

**Response:**

```json
{
  "success": true
}
```

---

#### `GET /display/status`

Get current display connection state.

**Response (connected):**

```json
{
  "connected": true,
  "resolution": [320, 320],
  "device_path": "/dev/hidraw3"
}
```

**Response (not connected):**

```json
{
  "connected": false
}
```

---

### Video

Video endpoints control background video playback on the LCD. Prefix: `/display/video`.

#### `POST /display/video/stop`

Stop background video playback.

**Response:**

```json
{
  "success": true,
  "message": "Video playback stopped"
}
```

---

#### `POST /display/video/pause`

Toggle pause on background video playback.

**Response:**

```json
{
  "success": true,
  "paused": true
}
```

**Errors:**

| Status | Detail |
|--------|--------|
| `409` | `No video playing` |

---

#### `GET /display/video/status`

Get current video playback state.

**Response (playing):**

```json
{
  "playing": true,
  "paused": false,
  "progress": 0.42,
  "current_time": "00:12",
  "total_time": "00:30",
  "fps": 30.0,
  "source": "/path/to/video.mp4",
  "loop": true
}
```

**Response (no video):**

```json
{
  "playing": false,
  "paused": false,
  "progress": 0.0,
  "current_time": "",
  "total_time": "",
  "fps": 0.0,
  "source": "",
  "loop": false
}
```

---

### Preview

Preview endpoints provide snapshots and live streams of the current LCD frame. Prefix: `/display`.

When the GUI daemon is running, frames are fetched via IPC. In standalone mode, frames come from the `on_frame_sent` capture callback.

#### `GET /display/preview`

Return the current LCD frame as a PNG image.

**Response:** Binary PNG image (`Content-Type: image/png`)

**Errors:**

| Status | Detail |
|--------|--------|
| `503` | `No image available` |

---

#### `WebSocket /display/preview/stream`

Live JPEG stream of the current LCD frame, functioning as a screen capture feed.

**Connection:**

```
ws://127.0.0.1:5000/display/preview/stream
ws://127.0.0.1:5000/display/preview/stream?token=your-secret-token
```

**Authentication:** When token auth is configured, pass `?token=` as a query parameter. Invalid tokens cause an immediate close with code `4001` and reason `"Unauthorized"`.

**Server -> Client:** Binary JPEG frames sent at the configured framerate.

**Client -> Server:** JSON control messages to adjust stream parameters:

| Message | Description | Constraints |
|---------|-------------|-------------|
| `{"fps": 15}` | Set stream framerate | 1-30 |
| `{"quality": 70}` | Set JPEG compression quality | 10-100 |
| `{"pause": true}` | Pause frame sending | `true` / `false` |

Multiple fields can be combined in a single message:

```json
{"fps": 20, "quality": 90}
```

**Default Parameters:**

| Parameter | Default |
|-----------|---------|
| `fps` | `10` |
| `quality` | `85` |
| `paused` | `false` |

**Behavior:**
- The server reads the current LCD frame at the configured FPS rate
- Each frame is JPEG-encoded at the configured quality and sent as a binary WebSocket message
- When no frame is available, the server silently skips that tick
- Invalid JSON control messages are silently ignored
- The connection stays open until the client disconnects

---

### LED

All LED endpoints require a selected LED device. Prefix: `/led`.

#### `POST /led/color`

Set LED static color.

**Request Body:**

```json
{
  "hex": "00ff00"
}
```

**Response:**

```json
{
  "success": true
}
```

**Errors:**

| Status | Detail |
|--------|--------|
| `400` | `Invalid hex color (use 6-digit hex, e.g. 'ff0000')` |
| `409` | `No LED device selected. POST /devices/{id}/select first.` |

---

#### `POST /led/mode`

Set LED effect mode.

**Request Body:**

```json
{
  "mode": "breathing"
}
```

| Field | Type | Values | Description |
|-------|------|--------|-------------|
| `mode` | `string` | `static`, `breathing`, `colorful`, `rainbow` | Effect mode name |

**Response:**

```json
{
  "success": true
}
```

---

#### `POST /led/brightness`

Set LED brightness.

**Request Body:**

```json
{
  "level": 75
}
```

| Field | Type | Range | Description |
|-------|------|-------|-------------|
| `level` | `int` | `0`-`100` | Brightness percentage |

**Response:**

```json
{
  "success": true
}
```

---

#### `POST /led/off`

Turn all LEDs off.

**Response:**

```json
{
  "success": true
}
```

---

#### `POST /led/sensor`

Set CPU/GPU sensor source for temperature/load linked modes.

**Request Body:**

```json
{
  "source": "cpu"
}
```

| Field | Type | Values | Description |
|-------|------|--------|-------------|
| `source` | `string` | `cpu`, `gpu` | Sensor data source |

**Response:**

```json
{
  "success": true
}
```

---

#### `POST /led/zones/{zone}/color`

Set color for a specific LED zone.

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `zone` | `int` | Zero-based zone index |

**Request Body:**

```json
{
  "hex": "0000ff"
}
```

**Response:**

```json
{
  "success": true
}
```

---

#### `POST /led/zones/{zone}/mode`

Set effect mode for a specific LED zone.

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `zone` | `int` | Zero-based zone index |

**Request Body:**

```json
{
  "mode": "rainbow"
}
```

**Response:**

```json
{
  "success": true
}
```

---

#### `POST /led/zones/{zone}/brightness`

Set brightness for a specific LED zone.

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `zone` | `int` | Zero-based zone index |

**Request Body:**

```json
{
  "level": 50
}
```

| Field | Type | Range | Description |
|-------|------|-------|-------------|
| `level` | `int` | `0`-`100` | Zone brightness percentage |

**Response:**

```json
{
  "success": true
}
```

---

#### `POST /led/zones/{zone}/toggle`

Toggle a specific LED zone on or off.

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `zone` | `int` | Zero-based zone index |

**Request Body:**

```json
{
  "on": true
}
```

**Response:**

```json
{
  "success": true
}
```

---

#### `POST /led/sync`

Enable or disable zone sync (circulate/select-all mode).

**Request Body:**

```json
{
  "enabled": true,
  "interval": 1000
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `enabled` | `bool` | yes | Enable/disable zone sync |
| `interval` | `int` | no | Sync interval in milliseconds (null for default) |

**Response:**

```json
{
  "success": true
}
```

---

#### `POST /led/segments/{index}/toggle`

Toggle a specific LED segment on or off.

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `index` | `int` | Zero-based segment index |

**Request Body:**

```json
{
  "on": true
}
```

**Response:**

```json
{
  "success": true
}
```

---

#### `POST /led/clock`

Set LED segment display clock format.

**Request Body:**

```json
{
  "is_24h": true
}
```

| Field | Type | Description |
|-------|------|-------------|
| `is_24h` | `bool` | `true` for 24-hour format, `false` for 12-hour |

**Response:**

```json
{
  "success": true
}
```

---

#### `POST /led/temp-unit`

Set LED segment display temperature unit.

**Request Body:**

```json
{
  "unit": "C"
}
```

| Field | Type | Values | Description |
|-------|------|--------|-------------|
| `unit` | `string` | `C`, `F` | Celsius or Fahrenheit |

**Response:**

```json
{
  "success": true
}
```

---

#### `GET /led/status`

Get current LED connection state.

**Response (connected):**

```json
{
  "connected": true,
  "status": "initialized"
}
```

**Response (not connected):**

```json
{
  "connected": false
}
```

---

### Themes

Theme listing, loading, saving, and import endpoints. Prefix: `/themes`.

#### `GET /themes`

List available local themes for a given resolution.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `resolution` | `string` | `"320x320"` | Display resolution in `WxH` format |

**Response:**

```json
[
  {
    "name": "Galaxy",
    "category": "nature",
    "is_animated": false,
    "has_config": true,
    "preview_url": "/static/themes/Galaxy/Theme.png"
  },
  {
    "name": "Neon Wave",
    "category": "abstract",
    "is_animated": true,
    "has_config": false,
    "preview_url": "/static/themes/Neon Wave/00.png"
  }
]
```

**Errors:**

| Status | Detail |
|--------|--------|
| `400` | `Invalid resolution format (use WxH)` |
| `400` | `Resolution out of range (100-4096)` |

---

#### `GET /themes/web`

List available cloud theme previews for a given resolution.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `resolution` | `string` | `"320x320"` | Display resolution in `WxH` format |

**Response:**

```json
[
  {
    "id": "a001",
    "category": "a",
    "preview_url": "/static/web/a001.png",
    "has_video": true,
    "download_url": "/themes/web/a001/download"
  }
]
```

---

#### `POST /themes/web/{theme_id}/download`

Download a cloud theme to local cache. Optionally starts video playback on the LCD.

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `theme_id` | `string` | Cloud theme identifier |

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `resolution` | `string` | auto | Resolution in `WxH` format. Defaults to the selected device's resolution, or `320x320`. |
| `send` | `bool` | `false` | If `true`, starts video playback on the selected LCD device |

**Response:**

```json
{
  "id": "a001",
  "cached_path": "/home/user/.local/share/trcc/web/320320/a001.mp4",
  "resolution": "320x320",
  "already_cached": false
}
```

**Errors:**

| Status | Detail |
|--------|--------|
| `404` | `Cloud theme '{theme_id}' not found on server` |
| `409` | `No LCD device selected. POST /devices/{id}/select first.` (when `send=true`) |

---

#### `GET /themes/masks`

List available mask overlays for a given resolution.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `resolution` | `string` | `"320x320"` | Display resolution in `WxH` format |

**Response:**

```json
[
  {
    "name": "Circle Frame",
    "preview_url": "/static/masks/Circle Frame/Theme.png"
  }
]
```

---

#### `POST /themes/load`

Load a local theme by name and send it to the device. Handles both static and animated themes.

For animated themes, starts background video playback. For static themes with overlay configs (`config1.dc`), starts a background overlay rendering loop that polls system metrics and updates the display every 2 seconds.

**Request Body:**

```json
{
  "name": "Galaxy",
  "resolution": "320x320"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | `string` | yes | Theme name (must match a discovered theme) |
| `resolution` | `string` | no | Override resolution in `WxH` format. Defaults to the selected device's resolution. |

**Response (static theme):**

```json
{
  "success": true,
  "theme": "Galaxy",
  "resolution": [320, 320]
}
```

**Response (animated theme):**

```json
{
  "success": true,
  "theme": "Neon Wave",
  "resolution": [320, 320],
  "animated": true
}
```

**Errors:**

| Status | Detail |
|--------|--------|
| `404` | `Theme '{name}' not found` |
| `404` | `No image file in theme '{name}'` |
| `409` | `No LCD device selected. POST /devices/{id}/select first.` |
| `500` | `Failed to start video playback` |
| `500` | `Send failed (device busy or error)` |

---

#### `POST /themes/save`

Save current device display as a named theme.

**Request Body:**

```json
{
  "name": "My Custom Theme"
}
```

**Response:**

```json
{
  "success": true,
  "message": "Theme 'My Custom Theme' saved",
  "name": "My Custom Theme"
}
```

---

#### `POST /themes/import`

Import a `.tr` theme archive file.

**Request Body:** `multipart/form-data`

| Field | Type | Description |
|-------|------|-------------|
| `file` | `file` | `.tr` theme archive. Max 50 MB. |

**Response:**

```json
{
  "success": true,
  "message": "Theme imported from mytheme.tr"
}
```

**Errors:**

| Status | Detail |
|--------|--------|
| `400` | `File must be a .tr theme archive` |
| `413` | `Theme archive exceeds 50 MB limit` |
| `500` | Import error details |

---

### System

System metrics and diagnostic endpoints. Prefix: `/system`.

#### `GET /system/metrics`

All system metrics as JSON. Covers CPU, GPU, memory, disk, network, fans, and date/time.

**Response:**

```json
{
  "cpu_temp": 52.0,
  "cpu_percent": 15.3,
  "cpu_freq": 3600.0,
  "cpu_power": 28.5,
  "gpu_temp": 45.0,
  "gpu_usage": 12.0,
  "gpu_clock": 1200.0,
  "gpu_power": 60.0,
  "mem_temp": 0.0,
  "mem_percent": 42.1,
  "mem_clock": 3200.0,
  "mem_available": 16384.0,
  "disk_temp": 38.0,
  "disk_activity": 5.2,
  "disk_read": 120.5,
  "disk_write": 45.3,
  "net_up": 1.2,
  "net_down": 15.8,
  "net_total_up": 1024.0,
  "net_total_down": 8192.0,
  "fan_cpu": 1200.0,
  "fan_gpu": 800.0,
  "fan_ssd": 0.0,
  "fan_sys2": 0.0,
  "date_year": 2026.0,
  "date_month": 3.0,
  "date_day": 1.0,
  "time_hour": 14.0,
  "time_minute": 30.0,
  "time_second": 45.0,
  "day_of_week": 0.0,
  "date": 0.0,
  "time": 0.0,
  "weekday": 0.0
}
```

---

#### `GET /system/metrics/{category}`

Filtered metrics by category. Returns only fields matching the category prefix.

**Path Parameters:**

| Parameter | Type | Values | Description |
|-----------|------|--------|-------------|
| `category` | `string` | `cpu`, `gpu`, `mem`, `memory`, `disk`, `net`, `network`, `fan` | Metric category |

**Response (example: `GET /system/metrics/cpu`):**

```json
{
  "cpu_temp": 52.0,
  "cpu_percent": 15.3,
  "cpu_freq": 3600.0,
  "cpu_power": 28.5
}
```

**Errors:**

| Status | Detail |
|--------|--------|
| `400` | `Unknown category '{category}'. Use: cpu, disk, fan, gpu, mem, memory, net, network` |

---

#### `GET /system/report`

Generate a diagnostic report for bug reports. Includes device info, USB permissions, SELinux status, and system details.

**Response:**

```json
{
  "report": "TRCC Linux Diagnostic Report\n..."
}
```

---

## Device Type Reference

The `protocol` field on each device determines its type and which endpoints apply.

| Protocol | Transport | Type | Endpoints |
|----------|-----------|------|-----------|
| `scsi` | SCSI generic (`/dev/sg*`) | LCD | `/display/*`, `/themes/*` |
| `hid` | PyUSB interrupt | LCD | `/display/*`, `/themes/*` |
| `bulk` | PyUSB bulk | LCD | `/display/*`, `/themes/*` |
| `ly` | PyUSB bulk (chunked) | LCD | `/display/*`, `/themes/*` |
| `led` | PyUSB HID | LED | `/led/*` |

LCD devices (`is_led=false`) use `DisplayDispatcher` for image/theme/video operations.
LED devices (`is_led=true`) use `LEDDispatcher` for RGB color/effect/segment operations.

Both device types share access to `/system/*` endpoints for metrics and diagnostics.

---

## Static Files

Static file directories are mounted after device selection, scoped to the selected device's resolution. Available only when the corresponding directories exist on disk.

| Mount Path | Content | Source |
|------------|---------|--------|
| `/static/themes/{name}/...` | Local theme assets (images, configs) | `ThemeDir.for_resolution(w, h)` |
| `/static/web/{file}` | Cloud theme preview images and videos | `DataManager.get_web_dir(w, h)` |
| `/static/masks/{name}/...` | Cloud mask overlay assets | `DataManager.get_web_masks_dir(w, h)` |

Static directories are remounted when the device changes (resolution may differ).

**Examples:**

```
GET /static/themes/Galaxy/Theme.png
GET /static/themes/Galaxy/00.png
GET /static/web/a001.png
GET /static/masks/Circle Frame/Theme.png
```

---

## HardwareMetrics Fields

Complete list of fields returned by `/system/metrics`. All values are `float`, defaulting to `0.0`.

### CPU

| Field | Description |
|-------|-------------|
| `cpu_temp` | CPU temperature (degrees) |
| `cpu_percent` | CPU usage percentage |
| `cpu_freq` | CPU frequency (MHz) |
| `cpu_power` | CPU power draw (watts) |

### GPU

| Field | Description |
|-------|-------------|
| `gpu_temp` | GPU temperature (degrees) |
| `gpu_usage` | GPU usage percentage |
| `gpu_clock` | GPU clock speed (MHz) |
| `gpu_power` | GPU power draw (watts) |

### Memory

| Field | Description |
|-------|-------------|
| `mem_temp` | Memory temperature (degrees) |
| `mem_percent` | Memory usage percentage |
| `mem_clock` | Memory clock speed (MHz) |
| `mem_available` | Available memory (MB) |

### Disk

| Field | Description |
|-------|-------------|
| `disk_temp` | Disk temperature (degrees) |
| `disk_activity` | Disk activity percentage |
| `disk_read` | Disk read speed (MB/s) |
| `disk_write` | Disk write speed (MB/s) |

### Network

| Field | Description |
|-------|-------------|
| `net_up` | Upload speed (KB/s) |
| `net_down` | Download speed (KB/s) |
| `net_total_up` | Total uploaded (MB) |
| `net_total_down` | Total downloaded (MB) |

### Fan

| Field | Description |
|-------|-------------|
| `fan_cpu` | CPU fan speed (RPM) |
| `fan_gpu` | GPU fan speed (RPM) |
| `fan_ssd` | SSD fan speed (RPM) |
| `fan_sys2` | System fan 2 speed (RPM) |

### Date/Time

| Field | Description |
|-------|-------------|
| `date_year` | Current year |
| `date_month` | Current month (1-12) |
| `date_day` | Current day of month |
| `time_hour` | Current hour (0-23) |
| `time_minute` | Current minute (0-59) |
| `time_second` | Current second (0-59) |
| `day_of_week` | Day of week (0=Monday) |
| `date` | Composite date value |
| `time` | Composite time value |
| `weekday` | Composite weekday value |
