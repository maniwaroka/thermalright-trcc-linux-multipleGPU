# Target Architecture — Hexagonal SOLID (Without the Ceremony)

## Problem

The current CommandBus layer (62 command dataclasses, 4 handlers, ~1,800 lines) sits between good adapters and good core logic, adding indirection without value. 80% of handler case arms are pure passthrough: `case SetBrightnessCommand(level=level): return self._lcd.set_brightness(level)`.

The hexagonal architecture is correct. The SOLID principles are correct. The CommandBus is not part of either — it's ceremony that grew between the layers. Removing it makes the architecture *more* hexagonal, not less.

## Hexagonal Layers (What We Keep)

```
┌─────────────────────────────────────────────────┐
│  Adapters (thin — parse input, format output)   │
│                                                 │
│  CLI:  trcc 0 brightness 75 image pic.png       │
│  API:  GET /trcc/0?brightness=75&image=pic.png  │
│  GUI:  slider + image picker on device panel    │
└───────────────────┬─────────────────────────────┘
                    │ direct method calls (no bus)
┌───────────────────▼─────────────────────────────┐
│  Core — Device Facades (LCDDevice, LEDDevice)   │
│                                                 │
│  ~22 operations total (methods on device)       │
│  Each takes args, returns result dict           │
│  Owns connection lifecycle + state              │
│  Type gates available operations                │
└───────────────────┬─────────────────────────────┘
                    │ injected services (DIP)
┌───────────────────▼─────────────────────────────┐
│  Services (pure business logic, no framework)   │
│                                                 │
│  DisplayService, OverlayService, LEDService     │
│  ImageService (delegates to Renderer)           │
│  SystemService (sensor polling)                 │
└───────────────────┬─────────────────────────────┘
                    │ injected transports (DIP)
┌───────────────────▼─────────────────────────────┐
│  Transport (raw device I/O)                     │
│                                                 │
│  SCSI, HID, Bulk, LED — unchanged              │
│  Platform adapters — unchanged                  │
└─────────────────────────────────────────────────┘
```

Dependencies point inward only. Adapters → Core → Services → Transport.
This is textbook hexagonal. The CommandBus was a layer that didn't belong.

## SOLID (How It Applies)

- **SRP**: Adapters parse/format. Devices orchestrate. Services compute. Transport sends bytes.
- **OCP**: `@DeviceProtocolFactory.register()` for new devices. New device = new data, not modified logic.
- **LSP**: LCD and LED are separate types — no fake `send_image()` on LED devices.
- **ISP**: `LCDDevice` and `LEDDevice` expose only their own operations.
- **DIP**: Services and transports injected via constructors. Core never imports adapters.

None of this requires a CommandBus. It requires clean interfaces and dependency injection, which we already have.

## The Device — Universal Entry Point

One device list, indexed. Handshake determines type. Index is the universal selector.

```
[0] Frozen Warframe Pro  (LCD, 320x320, /dev/sg2)
[1] AX120 R3             (LED, 6 zones, /dev/hidraw3)
```

### Adapter Mapping

| Operation | CLI | API | GUI |
|-----------|-----|-----|-----|
| Discover all | `trcc` | `GET /trcc` | Auto on startup |
| Select device | `trcc 0` | `GET /trcc/0` | Click device button |
| Single op | `trcc 0 brightness 75` | `GET /trcc/0?brightness=75` | Slider widget |
| Chained ops | `trcc 0 brightness 75 image pic.png` | `GET /trcc/0?brightness=75&image=pic.png` | Panel applies both |
| GUI mode | `trcc gui` | n/a | n/a |

### Command Chaining

Operations chain left-to-right, executed sequentially on one device connection:

```
trcc 0 brightness 75 image pic.png rotation 90
```

Pipeline: connect → set brightness → send image → set rotation → done.

Each adapter parses chained operations into a list of `(method_name, args)` and calls them in order on the device. No command objects — just method calls.

## Operations (Validated Against C# Source)

The Windows app has ~22 distinct operations. That's what the hardware supports.

### LCD (~12 operations)
| Operation | Args | Device method |
|-----------|------|---------------|
| `brightness` | `0-100` | `lcd.set_brightness(level)` |
| `image` | `path or url` | `lcd.send_image(path)` |
| `color` | `hex or r,g,b` | `lcd.send_color(r, g, b)` |
| `rotation` | `0, 90, 180, 270` | `lcd.set_rotation(degrees)` |
| `theme` | `name` | `lcd.load_theme(name)` |
| `overlay` | `on/off` | `lcd.set_overlay(enabled)` |
| `video` | `path` | `lcd.play_video(path)` |
| `screencast` | `region` | `lcd.start_screencast(...)` |
| `gif` | `path` | `lcd.send_gif(path)` |
| `text` | `string` | `lcd.send_text(text)` |
| `export` | `path` | `lcd.export_theme(path)` |
| `import` | `path` | `lcd.import_theme(path)` |

### LED (~6 operations)
| Operation | Args | Device method |
|-----------|------|---------------|
| `brightness` | `0-100` | `led.set_brightness(level)` |
| `color` | `hex or r,g,b` | `led.set_color(r, g, b)` |
| `mode` | `name` | `led.set_mode(mode)` |
| `speed` | `1-5` | `led.set_speed(speed)` |
| `zone` | `index color` | `led.set_zone(idx, r, g, b)` |
| `toggle` | — | `led.toggle()` |

### Shared (~4 operations)
| Operation | Args | Device method |
|-----------|------|---------------|
| `info` | — | `device.info()` |
| `disconnect` | — | `device.disconnect()` |
| `temp-unit` | `c/f` | via `settings.set_temp_unit()` |
| `language` | `code` | via `settings.set_lang()` |

## GUI Specifics

- `trcc gui` discovers + handshakes all devices at startup
- Device buttons appear based on what was found (or saved in config)
- Clicking a device button opens LCD panel or LED panel based on device type
- Panel widgets call device methods directly — no bus dispatch
- LED rate limiting is a local concern in `led_handler.py` (throttle slider signals with a timer)
- Multiple devices: each has its own connection, panels switch between them

## What Changes

### Remove (~1,800 lines)
- `core/command_bus.py` — bus + middleware infrastructure
- `core/commands/` — 62 command dataclasses (replaced by ~22 device methods that already exist)
- `core/handlers/` — 4 handlers with match statements (pure passthrough, no logic)

### Simplify (rewire ~122 dispatch sites)
- CLI: `bus.dispatch(SetBrightnessCommand(level=75))` → `lcd.set_brightness(75)`
- API: `bus.dispatch(SetBrightnessCommand(level=75))` → `lcd.set_brightness(75)`
- GUI: `self._bus.dispatch(SetBrightnessCommand(level=75))` → `self._lcd.set_brightness(75)`
- LED rate limiting: move from `RateLimitMiddleware` to timer in `led_handler.py`
- Logging: `log.debug()` at adapter boundary or in device methods (where it belongs)

### Keep (unchanged)
- `core/models.py` — domain data, single source of truth
- `core/lcd_device.py`, `core/led_device.py` — device facades (these ARE the operations)
- `services/` — business logic (display, overlay, LED, system)
- `adapters/device/` — transport layer (SCSI, HID, Bulk, LED protocols)
- `adapters/system/` — platform adapters (Linux, Windows, macOS, BSD)
- `conf.py` — settings singleton
- `gui/` widgets — just rewire from `bus.dispatch()` to `device.method()`

### Add
- Command chaining in CLI arg parser (parse `brightness 75 image pic.png` as operation list)
- Query param chaining in API (parse `?brightness=75&image=pic.png` as operation list)
- Device index as first-class concept in all three adapters

## Test Strategy

Tests follow the same simplification:
- **Core tests**: Call device methods with realistic inputs, assert outputs. No command objects.
- **Service tests**: Inject mock transports, verify business logic with real inputs/outputs.
- **Adapter tests**: Verify adapters parse input correctly and call the right device method.
- **Integration tests**: Full pipeline — adapter parses input → device method → service logic → mock transport.
- **Fixture-based**: Fixtures provide realistic device state (handshake results, device info). Tests assert on real return values, not mock calls.
