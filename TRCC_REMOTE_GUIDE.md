# TRCC Remote -- Flutter App Coding Guide

This document is the authoritative reference for the Claude instance building the `trcc_remote` Flutter mobile app. It describes the TRCC Linux REST API that the app communicates with, the two device types, the required workflow, error handling, and common pitfalls.

For the full endpoint specification, see `API_SPEC.md`.

---

## What the System Does

TRCC Linux is a desktop application that controls Thermalright CPU cooler LCD and LED displays over USB. It exposes a REST API (FastAPI, default port 5000) on the host machine. The Flutter app connects to this API over the local network (LAN) to provide remote control from a phone or tablet.

The desktop app does all the heavy lifting -- USB protocol handling, image encoding, frame sending, sensor polling. The mobile app is a thin remote control: it discovers the API, sends commands, and displays previews.

```
[Flutter App] --HTTP/WS over LAN--> [TRCC Linux API :5000] --USB--> [Thermalright Device]
```

The API binds to `127.0.0.1` by default. The user must start the API with `--host 0.0.0.0` (or a specific LAN IP) for the mobile app to reach it. The `--token` flag enables bearer-style authentication.

---

## Two Device Types: LCD vs LED

Every Thermalright device is either an **LCD display** or an **LED segment display**. The device type determines which API endpoints are valid. The `protocol` field in the device response tells you which type you are dealing with.

### LCD Devices (protocol: scsi, hid, bulk, ly)

LCD devices have a screen. You send images, themes, video, overlays. Use:
- `/display/*` -- brightness, rotation, color, preview, video, overlay, mask
- `/themes/*` -- list, load, save, import local themes; list, download cloud themes

### LED Devices (protocol: led)

LED devices have RGB LEDs and 7-segment digit displays on CPU coolers. No screen, no images, no themes. Use:
- `/led/*` -- color, mode, brightness, off, zones, segments, clock format, temp unit

### UI Branching Rule

After selecting a device, check `device.protocol`. If it equals `"led"`, show the LED control UI. For any other protocol value (`"scsi"`, `"hid"`, `"bulk"`, `"ly"`), show the LCD display UI. Never show LCD controls for an LED device or vice versa -- the API will return 409 because the wrong dispatcher type is initialized.

```dart
// After device selection
final isLed = device.protocol == 'led';
if (isLed) {
  // Navigate to LED control screen
} else {
  // Navigate to LCD display screen
}
```

---

## Required Workflow

There is a strict ordering you must follow. Skipping steps causes 409 errors.

### Step 1: Discover the API

The user provides the host address (IP or hostname). Verify reachability:

```
GET http://{host}:5000/health
```

Response: `{"status": "ok", "version": "6.5.3"}`

This endpoint never requires authentication. Use it for connectivity checks.

### Step 2: Detect Devices

List what the API already knows about, then trigger a USB rescan:

```
GET /devices              --> list currently known devices (may be empty)
POST /devices/detect      --> rescan USB bus, returns updated device list
```

Both return an array of device objects:

```json
[
  {
    "id": 0,
    "name": "Thermalright AXP120-X67",
    "vid": 12994,
    "pid": 21505,
    "protocol": "hid",
    "resolution": [320, 320],
    "path": "/dev/hidraw3"
  },
  {
    "id": 1,
    "name": "Thermalright HR10",
    "vid": 12994,
    "pid": 4608,
    "protocol": "led",
    "resolution": [0, 0],
    "path": "/dev/hidraw5"
  }
]
```

Notes:
- `id` is the zero-based index in the device list. It is stable within a session but may change after a rescan.
- `resolution` is `[0, 0]` for LED devices (they have no screen).
- `protocol` determines the device type (see above).
- You only need to call `POST /devices/detect` once per session, or when the user explicitly requests a rescan. Do not poll it.

### Step 3: Select a Device

```
POST /devices/{id}/select
```

Response: `{"selected": "Thermalright AXP120-X67", "resolution": [320, 320]}`

This initializes the backend dispatcher for the device. Until you do this, all `/display/*`, `/led/*`, and `/themes/*` endpoints return **409 Conflict**. The select call:
- Performs USB handshake and resolution discovery
- Initializes the correct dispatcher (LCD or LED)
- Mounts static file directories for theme/mask previews
- Restores the last-used theme (LCD devices)
- Is idempotent if you re-select the same device

After selecting, you do NOT need to re-select on every request. Select once, then use endpoints freely until you switch devices or the API restarts.

### Step 4: Use Device Endpoints

Now use `/display/*` + `/themes/*` for LCD, or `/led/*` for LED.

---

## Endpoint Reference by Domain

### Device Management

| Method | Path | Body | Response | Notes |
|--------|------|------|----------|-------|
| GET | `/devices` | -- | `DeviceResponse[]` | List known devices |
| POST | `/devices/detect` | -- | `DeviceResponse[]` | USB rescan |
| GET | `/devices/{id}` | -- | `DeviceResponse` | Single device details |
| POST | `/devices/{id}/select` | -- | `{"selected", "resolution"}` | Initialize device |
| POST | `/devices/{id}/send` | multipart image + rotation + brightness query params | `{"sent", "resolution"}` | Send raw image to LCD |
| GET | `/health` | -- | `{"status", "version"}` | No auth required |

### Display (LCD only)

| Method | Path | Body | Response | Notes |
|--------|------|------|----------|-------|
| POST | `/display/color` | `{"hex": "ff0000"}` | dispatch result | Solid color fill |
| POST | `/display/brightness` | `{"level": 1\|2\|3}` | dispatch result | 1=25%, 2=50%, 3=100% |
| POST | `/display/rotation` | `{"degrees": 0\|90\|180\|270}` | dispatch result | Persists to config |
| POST | `/display/split` | `{"mode": 0\|1\|2\|3}` | dispatch result | 0=off, 1-3=Dynamic Island |
| POST | `/display/reset` | -- | dispatch result | Sends solid red frame |
| POST | `/display/mask` | multipart PNG upload | dispatch result | Apply mask overlay |
| POST | `/display/overlay` | `dc_path` + `send` query params | dispatch result | Render overlay from DC config |
| GET | `/display/status` | -- | `{"connected", "resolution", "device_path"}` | Connection state |
| GET | `/display/preview` | -- | PNG image bytes | Single frame snapshot |
| WS | `/display/preview/stream` | -- | Binary JPEG frames | Live preview stream |
| POST | `/display/video/stop` | -- | `{"success", "message"}` | Stop video playback |
| POST | `/display/video/pause` | -- | `{"success", "paused"}` | Toggle pause |
| GET | `/display/video/status` | -- | `VideoStatusResponse` | Playback state |

### LED (LED only)

| Method | Path | Body | Response | Notes |
|--------|------|------|----------|-------|
| POST | `/led/color` | `{"hex": "00ff00"}` | dispatch result | Static color |
| POST | `/led/mode` | `{"mode": "breathing"}` | dispatch result | Effect mode |
| POST | `/led/brightness` | `{"level": 75}` | dispatch result | 0-100 range |
| POST | `/led/off` | -- | dispatch result | Turn off LEDs |
| POST | `/led/sensor` | `{"source": "cpu"}` | dispatch result | Sensor source for linked modes |
| POST | `/led/zones/{zone}/color` | `{"hex": "0000ff"}` | dispatch result | Per-zone color |
| POST | `/led/zones/{zone}/mode` | `{"mode": "rainbow"}` | dispatch result | Per-zone effect |
| POST | `/led/zones/{zone}/brightness` | `{"level": 50}` | dispatch result | Per-zone brightness |
| POST | `/led/zones/{zone}/toggle` | `{"on": true}` | dispatch result | Zone on/off |
| POST | `/led/sync` | `{"enabled": true, "interval": null}` | dispatch result | Zone sync/circulate |
| POST | `/led/segments/{index}/toggle` | `{"on": false}` | dispatch result | Segment on/off |
| POST | `/led/clock` | `{"is_24h": true}` | dispatch result | 12h/24h format |
| POST | `/led/temp-unit` | `{"unit": "C"}` | dispatch result | Celsius/Fahrenheit |
| GET | `/led/status` | -- | `{"connected", "status"}` | Connection state |

### Themes (LCD only)

| Method | Path | Body / Params | Response | Notes |
|--------|------|---------------|----------|-------|
| GET | `/themes?resolution=320x320` | query param | `ThemeResponse[]` | List local themes |
| GET | `/themes/web?resolution=320x320` | query param | `WebThemeResponse[]` | List cloud themes |
| POST | `/themes/web/{id}/download` | `resolution`, `send` query params | `WebThemeDownloadResponse` | Download cloud theme |
| GET | `/themes/masks?resolution=320x320` | query param | `MaskResponse[]` | List mask overlays |
| POST | `/themes/load` | `{"name": "MyTheme", "resolution": "320x320"}` | `{"success", "theme", "resolution"}` | Load and send theme |
| POST | `/themes/save` | `{"name": "MyTheme"}` | `{"success", "message", "name"}` | Save current display |
| POST | `/themes/import` | multipart .tr file | `{"success", "message"}` | Import theme archive |

### System Metrics

| Method | Path | Response | Notes |
|--------|------|----------|-------|
| GET | `/system/metrics` | full metrics dict | CPU, GPU, memory, disk, network, fans |
| GET | `/system/metrics/{category}` | filtered metrics dict | Categories: cpu, gpu, mem, disk, net, fan |
| GET | `/system/report` | `{"report": "..."}` | Diagnostic report text |

---

## Preview Stream (WebSocket)

The preview stream is the primary way to show the user what is currently on the LCD screen. It delivers binary JPEG frames over a WebSocket connection.

### Connection

```
ws://{host}:5000/display/preview/stream?token={api_token}
```

The `token` query parameter is required if the API was started with `--token`. If no token is configured, omit it.

### Frame Format

Each WebSocket message is a **binary** message containing raw JPEG bytes. Decode it directly into an image widget. There is no framing, no length prefix, no JSON wrapping -- just JPEG data.

### Client Control

Send JSON text messages to adjust the stream:

```json
{"fps": 5}          // Frame rate: 1-30, default 10
{"quality": 70}     // JPEG quality: 10-100, default 85
{"pause": true}     // Pause/resume stream
```

You can combine keys: `{"fps": 5, "quality": 60}`.

### Mobile Recommendations

- **Start with fps: 5** on mobile. 10 fps is smooth but wastes battery. Let the user increase it.
- **Start with quality: 70** to reduce bandwidth. 85 is the default but unnecessary for a phone-sized preview.
- **Pause when backgrounded.** Send `{"pause": true}` in `AppLifecycleState.paused`, resume on `AppLifecycleState.resumed`.
- **Do not buffer frames.** Display each frame as it arrives, discarding the previous one. If you queue frames, you introduce latency that grows unbounded.
- A typical 320x320 JPEG at quality 70 is 15-30 KB. At 5 fps that is 75-150 KB/s -- reasonable for WiFi.

### Single Frame Snapshot

For one-shot preview (e.g., thumbnail in a device list), use the HTTP endpoint instead:

```
GET /display/preview
```

Returns a PNG image. Returns 503 if no image is available yet (device just selected, theme not loaded). Retry once after a short delay.

---

## Authentication

Authentication is optional. If the API was started with `--token mysecret`, every request (except `/health`) must include the token.

### HTTP Requests

```
X-API-Token: mysecret
```

Add this header to every HTTP request. A missing or wrong token returns **401 Unauthorized**.

### WebSocket

```
ws://{host}:5000/display/preview/stream?token=mysecret
```

Pass the token as a query parameter. An invalid token closes the socket with code 4001.

### No Token Configured

If the user did not set a token, omit the header and query parameter entirely. The API accepts all requests.

### App UI Pattern

The connection settings screen should have:
- Host address field (IP or hostname)
- Port field (default 5000)
- Token field (optional, password-masked)
- A "Test Connection" button that hits `GET /health`

Store these in secure local storage (e.g., `flutter_secure_storage`).

---

## Error Handling

The API returns standard HTTP status codes with a JSON `{"detail": "..."}` body on errors.

### Status Codes

| Code | Meaning | Action |
|------|---------|--------|
| 200 | Success | Process response |
| 400 | Bad input (invalid hex, bad resolution format, missing field) | Show error to user, fix input |
| 401 | Invalid or missing API token | Prompt user to check token in settings |
| 404 | Device/theme/category not found | Show "not found" message |
| 409 | No device selected (dispatcher not initialized) | Call `POST /devices/{id}/select` first |
| 413 | Upload too large (image >10MB, theme >50MB) | Show size limit to user |
| 500 | Server-side failure (send failed, device error) | Show error, suggest retry |
| 503 | No image available yet | Retry once after 1-2 seconds |

### Retry Rules

- **Do retry:** 503 (no image yet -- device just initialized, give it a moment)
- **Do not retry:** 400, 401, 404, 409, 413 -- these are client errors that won't resolve on their own
- **Maybe retry:** 500 -- device might have been busy, one retry is reasonable
- **Connection refused / timeout** -- API is not running or unreachable. Show offline state, do not retry in a tight loop.

### Dispatch Result Format

Most action endpoints (display, LED, theme load) return a "dispatch result" dict:

```json
{"success": true, "message": "Color set to ff0000"}
```

On failure, the API converts this to a 400 response:

```json
{"detail": "Invalid mode 'sparkle'. Use: static, breathing, colorful, rainbow"}
```

---

## Data Formats

### Hex Colors

Six-digit hex string, **without** the `#` prefix. Lowercase preferred.

```
"ff0000"   -- red
"00ff00"   -- green
"0000ff"   -- blue
```

The API returns 400 on invalid hex (wrong length, non-hex characters, or if you include `#`).

### Resolution Strings

Format: `"WxH"` -- width, lowercase `x`, height. Used as query parameters for theme and mask listing.

```
"320x320"
"480x480"
"1280x480"
```

The API validates the range 100-4096 for each dimension.

### Resolution Tuples

Device responses and select responses return resolution as a JSON array `[width, height]`:

```json
"resolution": [320, 320]
```

LED devices return `[0, 0]`.

### Video Status

```json
{
  "playing": true,
  "paused": false,
  "progress": 0.45,
  "current_time": "0:12",
  "total_time": "0:27",
  "fps": 24.0,
  "source": "/path/to/video.mp4",
  "loop": true
}
```

---

## Themes

### Local Themes

Listed via `GET /themes?resolution=320x320`. Each theme has:

```json
{
  "name": "Galaxy",
  "category": "Animated",
  "is_animated": true,
  "has_config": true,
  "preview_url": "/static/themes/Galaxy/Theme.png"
}
```

- `is_animated` -- if true, loading this theme starts video playback on the device
- `has_config` -- if true, this theme has an overlay config (live metrics like CPU temp, clock)
- `preview_url` -- relative to the API base URL. Fetch as `http://{host}:5000{preview_url}`

Load a theme: `POST /themes/load` with `{"name": "Galaxy"}`. The API handles everything -- image resizing, video playback, overlay loop startup.

### Cloud Themes

Listed via `GET /themes/web?resolution=320x320`:

```json
{
  "id": "a001",
  "category": "a",
  "preview_url": "/static/web/a001.png",
  "has_video": true,
  "download_url": "/themes/web/a001/download"
}
```

To use a cloud theme:
1. Show the preview image from `preview_url`
2. When the user taps it, `POST /themes/web/{id}/download?send=true`
3. The API downloads the theme (or uses cache) and starts playback

The `send=true` parameter tells the API to immediately play the theme on the device after downloading.

### Masks

Listed via `GET /themes/masks?resolution=320x320`:

```json
{
  "name": "Circle",
  "preview_url": "/static/masks/Circle/Theme.png"
}
```

Masks are PNG overlays applied on top of the current display content.

### Preview URL Pattern

All preview URLs are **relative paths** starting with `/static/`. Construct the full URL by prepending the API base URL:

```dart
final fullUrl = 'http://$host:$port$previewUrl';
```

If the API has token auth, you must include `X-API-Token` as a header when fetching these images. For `Image.network()` in Flutter, pass the header:

```dart
Image.network(
  fullUrl,
  headers: {'X-API-Token': token},
)
```

---

## LED Controls in Detail

LED devices have a different interaction model than LCD devices. There is no screen to preview -- you control physical LEDs on the cooler.

### Global Controls

- **Color** (`POST /led/color`): Set a single static color for all LEDs. Body: `{"hex": "ff0000"}`.
- **Mode** (`POST /led/mode`): Effect mode. Body: `{"mode": "static"}`. Available modes: `static`, `breathing`, `colorful`, `rainbow` (and others depending on device style).
- **Brightness** (`POST /led/brightness`): 0-100 range. Body: `{"level": 75}`.
- **Off** (`POST /led/off`): Turn all LEDs off. No body.
- **Sensor** (`POST /led/sensor`): Set which hardware sensor drives temperature-linked modes. Body: `{"source": "cpu"}` or `{"source": "gpu"}`.

### Zone Controls

Some LED devices have multiple zones (physical groups of LEDs). Zones are indexed starting at 0.

- `POST /led/zones/{zone}/color` -- color per zone
- `POST /led/zones/{zone}/mode` -- effect per zone
- `POST /led/zones/{zone}/brightness` -- brightness per zone
- `POST /led/zones/{zone}/toggle` -- enable/disable zone. Body: `{"on": true}`.

### Zone Sync

`POST /led/sync` with `{"enabled": true}` links all zones to the same settings. With `"interval"` set, the device rotates (circulates) the active zone on a timer.

### Segment Controls

LED devices with 7-segment digit displays allow toggling individual segments:

- `POST /led/segments/{index}/toggle` -- toggle segment. Body: `{"on": true}`.

### Clock and Temperature

- `POST /led/clock` -- set 12h or 24h format. Body: `{"is_24h": true}`.
- `POST /led/temp-unit` -- Celsius or Fahrenheit. Body: `{"unit": "C"}` or `{"unit": "F"}`.

### Status

`GET /led/status` returns connection info:

```json
{"connected": true, "status": "..."}
```

or `{"connected": false}` if no LED device is selected.

---

## System Metrics

The API exposes host system metrics (CPU, GPU, memory, disk, network, fans) via `/system/metrics`. These are the same metrics that drive overlay displays on LCD themes.

```
GET /system/metrics           --> all metrics
GET /system/metrics/cpu       --> CPU-only
GET /system/metrics/gpu       --> GPU-only
GET /system/metrics/mem       --> memory
GET /system/metrics/disk      --> disk
GET /system/metrics/net       --> network
GET /system/metrics/fan       --> fan speeds
```

Useful for building a "system monitor" screen in the app. Poll at 2-5 second intervals -- these values do not change faster than that.

---

## Things That Will Waste Your Time

These are mistakes that seem reasonable but will cause bugs, performance issues, or wasted effort. Learn from them.

### Do not poll /devices/detect

Device detection triggers a USB bus scan. It is slow (hundreds of milliseconds) and unnecessary to repeat. Call it once when the user opens the device list or taps "Refresh." Do not put it on a timer.

### Do not re-select the device on every request

`POST /devices/{id}/select` initializes the USB handshake, discovers resolution, and sets up the dispatcher. It is idempotent (re-selecting the same device is a no-op), but it is not free. Call it once after the user picks a device. All subsequent `/display/*`, `/led/*`, and `/themes/*` calls use the already-initialized dispatcher. The only time you need to re-select is when switching to a different device or reconnecting after the API restarts.

### Do not buffer WebSocket preview frames

Each WebSocket binary message is a complete JPEG frame. Display it immediately, replacing the previous frame. If you push frames into a queue and consume them asynchronously, you will build up latency -- the preview will fall behind real-time and never catch up. One frame buffer, always overwritten.

### Do not mix LCD and LED commands for the same device

If the selected device is LCD (protocol scsi/hid/bulk/ly), the LED dispatcher is null and `/led/*` returns 409. If the selected device is LED (protocol led), the display dispatcher is null and `/display/*` returns 409. The API enforces this, but your UI should never present the wrong controls in the first place.

### Do not assume the API is always reachable

The phone is on WiFi. WiFi drops. The user walks to another room. The desktop goes to sleep. The API process gets killed. Design for intermittent connectivity:
- Show a clear "disconnected" state
- Periodically check `GET /health` (every 10-30 seconds when idle)
- Reconnect the WebSocket when it drops (with exponential backoff, max ~30 seconds)
- Do not queue commands while offline -- just disable the controls and tell the user

### Do not hardcode localhost or 127.0.0.1

The Flutter app runs on a phone. `localhost` on the phone is the phone, not the desktop running TRCC. The user must enter the desktop's LAN IP (e.g., `192.168.1.42`). Always use the configured host address. Never default to localhost.

### Do not send rotation/brightness/split without a loaded theme

These controls modify the current display state. If nothing has been sent to the device yet, the result is undefined (may produce a garbled frame or no visible change). Load a theme or send a color first, then adjust settings.

### Do not parse preview_url as an absolute URL

Theme and mask `preview_url` fields are relative paths like `/static/themes/Galaxy/Theme.png`. They are not full URLs. Always prepend the API base URL. Also remember to include the auth header if token auth is enabled.

### Do not forget to handle the 503 on first preview

Right after device selection, `GET /display/preview` may return 503 because no frame has been rendered yet. This is normal. Wait 1-2 seconds and retry once, or just skip the preview until a theme is loaded.

### Do not use GET for state-changing operations

All state-changing operations are POST. GET endpoints are read-only (device list, status, preview, metrics, theme list). If you find yourself wanting to "set" something with GET, you are using the wrong endpoint.

---

## Suggested App Architecture

This is not prescriptive, but a reasonable starting point for Flutter:

### State Management

Use Riverpod or BLoC. Key state objects:
- **Connection state**: host, port, token, reachable (bool), API version
- **Device list**: fetched from `/devices`, refreshed on detect
- **Selected device**: the device currently being controlled, its type (LCD/LED)
- **LCD state**: brightness, rotation, current theme, video status, preview stream
- **LED state**: color, mode, brightness, zone states, segment states
- **System metrics**: polled from `/system/metrics`

### Service Layer

A single `TrccApiService` class that wraps all HTTP calls and the WebSocket connection. All methods return typed results (not raw `Response` objects). Handle errors in one place.

```dart
class TrccApiService {
  final String host;
  final int port;
  final String? token;

  Future<List<Device>> detectDevices();
  Future<void> selectDevice(int id);
  Future<void> setLcdColor(String hex);
  Future<void> setLedColor(String hex);
  Stream<Uint8List> previewStream({int fps = 5, int quality = 70});
  // ...
}
```

### Screen Flow

```
Connection Screen (enter host/port/token, test connection)
  --> Device List Screen (detect, show devices)
    --> LCD Control Screen (themes, preview, brightness, rotation)
    --> LED Control Screen (color, mode, zones, segments)
```

---

## Quick Start Checklist

1. [ ] Connection screen: host, port, token fields. "Test" button hits `GET /health`.
2. [ ] Device list: `POST /devices/detect`, show results, branch on protocol.
3. [ ] Device select: `POST /devices/{id}/select` on tap.
4. [ ] LCD preview: connect WebSocket, display JPEG frames, fps=5 on mobile.
5. [ ] LCD theme browser: `GET /themes`, show previews, `POST /themes/load` on tap.
6. [ ] LCD controls: brightness (1/2/3), rotation (0/90/180/270), solid color.
7. [ ] LED controls: color picker, mode selector, brightness slider, off button.
8. [ ] LED zones: list zones, per-zone color/mode/brightness/toggle.
9. [ ] Error handling: 409 triggers re-select flow, 401 triggers auth prompt, show all others.
10. [ ] Offline handling: health check polling, reconnect WebSocket, disable controls when unreachable.
