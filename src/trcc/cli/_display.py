"""LCD display frame sending commands."""
from __future__ import annotations

import os

from trcc.cli import _cli_handler, _device


def test(device=None, loop=False):
    """Test display with color cycle."""
    try:
        import time

        from trcc.services import ImageService

        svc = _device._get_service(device)
        if not svc.selected:
            print("No device found.")
            return 1

        dev = svc.selected
        w, h = dev.resolution

        colors = [
            ((255, 0, 0), "Red"),
            ((0, 255, 0), "Green"),
            ((0, 0, 255), "Blue"),
            ((255, 255, 0), "Yellow"),
            ((255, 0, 255), "Magenta"),
            ((0, 255, 255), "Cyan"),
            ((255, 255, 255), "White"),
        ]

        print(f"Testing display on {dev.path}...")

        while True:
            for (r, g, b), name in colors:
                print(f"  Displaying: {name}")
                img = ImageService.solid_color(r, g, b, w, h)
                svc.send_pil(img, w, h)
                time.sleep(1)

            if not loop:
                break

        print("Test complete!")
        return 0
    except KeyboardInterrupt:
        print("\nTest interrupted.")
        return 0
    except Exception as e:
        print(f"Error testing display: {e}")
        return 1


def send_image(image_path, device=None):
    """Send image to LCD."""
    try:
        if not os.path.exists(image_path):
            print(f"Error: File not found: {image_path}")
            return 1

        from PIL import Image

        from trcc.services import ImageService

        svc = _device._get_service(device)
        if not svc.selected:
            print("No device found.")
            return 1

        dev = svc.selected
        w, h = dev.resolution
        img = Image.open(image_path).convert('RGB')
        img = ImageService.resize(img, w, h)
        svc.send_pil(img, w, h)
        print(f"Sent {image_path} to {dev.path}")
        return 0
    except Exception as e:
        print(f"Error sending image: {e}")
        return 1


def send_color(hex_color, device=None):
    """Send solid color to LCD."""
    try:
        hex_color = hex_color.lstrip('#')
        if len(hex_color) != 6:
            print("Error: Invalid hex color. Use format: ff0000")
            return 1

        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)

        from trcc.services import ImageService

        svc = _device._get_service(device)
        if not svc.selected:
            print("No device found.")
            return 1

        dev = svc.selected
        w, h = dev.resolution
        img = ImageService.solid_color(r, g, b, w, h)
        svc.send_pil(img, w, h)
        print(f"Sent color #{hex_color} to {dev.path}")
        return 0
    except Exception as e:
        print(f"Error sending color: {e}")
        return 1


def play_video(video_path, *, device=None, loop=True, duration=0):
    """Play video/GIF/ZT on LCD device."""
    try:
        import time
        from pathlib import Path

        if not os.path.exists(video_path):
            print(f"Error: File not found: {video_path}")
            return 1

        from trcc.services import MediaService

        svc = _device._get_service(device)
        if not svc.selected:
            print("No device found.")
            return 1

        dev = svc.selected
        w, h = dev.resolution

        media = MediaService()
        media.set_target_size(w, h)
        if not media.load(Path(video_path)):
            print(f"Error: Failed to load video: {video_path}")
            return 1

        total = media._state.total_frames
        fps = media._state.fps
        print(f"Playing {video_path} ({total} frames, {fps:.0f}fps) "
              f"on {dev.path} [{w}x{h}]")
        if loop:
            print("Press Ctrl+C to stop.")

        media._state.loop = loop
        media.play()

        interval = media.frame_interval_ms / 1000.0
        start = time.monotonic()

        while media.is_playing:
            frame, should_send, progress = media.tick()
            if frame is None:
                break
            if should_send:
                svc.send_pil(frame, w, h)
            if progress:
                pct, cur, total_t = progress
                print(f"\r  {cur} / {total_t} ({pct:.0f}%)",
                      end="", flush=True)
            if duration and (time.monotonic() - start) >= duration:
                break
            time.sleep(interval)

        print("\nDone.")
        return 0
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    except Exception as e:
        print(f"Error playing video: {e}")
        return 1


@_cli_handler
def set_brightness(level, *, device=None):
    """Set display brightness level (1=25%, 2=50%, 3=100%).

    Persists to device config so 'trcc resume' uses it.
    """
    level_map = {1: 25, 2: 50, 3: 100}
    if level not in level_map:
        print("Error: brightness level must be 1, 2, or 3")
        print("  1 = 25%  (dim)")
        print("  2 = 50%  (medium)")
        print("  3 = 100% (full)")
        return 1

    percent = level_map[level]

    svc = _device._get_service(device)
    if not svc.selected:
        print("No device found.")
        return 1

    dev = svc.selected

    # Persist to device config
    from trcc.conf import Settings
    key = Settings.device_config_key(dev.device_index, dev.vid, dev.pid)
    Settings.save_device_setting(key, 'brightness_level', level)

    print(f"Brightness set to L{level} ({percent}%) on {dev.path}")
    return 0


@_cli_handler
def set_rotation(degrees, *, device=None):
    """Set display rotation (0, 90, 180, 270).

    Persists to device config so 'trcc resume' uses it.
    """
    if degrees not in (0, 90, 180, 270):
        print("Error: rotation must be 0, 90, 180, or 270")
        return 1

    svc = _device._get_service(device)
    if not svc.selected:
        print("No device found.")
        return 1

    dev = svc.selected

    from trcc.conf import Settings
    key = Settings.device_config_key(dev.device_index, dev.vid, dev.pid)
    Settings.save_device_setting(key, 'rotation', degrees)

    print(f"Rotation set to {degrees}° on {dev.path}")
    return 0


def screencast(*, device=None, x=0, y=0, w=0, h=0, fps=10):
    """Stream screen region to LCD. Ctrl+C to stop."""
    try:
        import time

        from PIL import ImageGrab

        from trcc.services import ImageService

        svc = _device._get_service(device)
        if not svc.selected:
            print("No device found.")
            return 1

        dev = svc.selected
        lcd_w, lcd_h = dev.resolution

        # Determine capture region
        bbox = None
        if w > 0 and h > 0:
            bbox = (x, y, x + w, y + h)
            print(f"Capturing region ({x},{y}) {w}x{h} → {dev.path} [{lcd_w}x{lcd_h}]")
        else:
            print(f"Capturing full screen → {dev.path} [{lcd_w}x{lcd_h}]")

        print(f"Target: {fps} fps. Press Ctrl+C to stop.")

        interval = 1.0 / fps
        frames = 0

        while True:
            start = time.monotonic()
            img = ImageGrab.grab(bbox=bbox)
            img = ImageService.resize(img, lcd_w, lcd_h)
            svc.send_pil(img, lcd_w, lcd_h)
            frames += 1
            print(f"\r  Frames: {frames}", end="", flush=True)
            elapsed = time.monotonic() - start
            if elapsed < interval:
                time.sleep(interval - elapsed)

    except KeyboardInterrupt:
        print(f"\nStopped after {frames} frames.")
        return 0
    except ImportError:
        print("Error: Screen capture requires Pillow with ImageGrab support.")
        print("On Linux, install: pip install Pillow")
        return 1
    except Exception as e:
        print(f"\nError: {e}")
        return 1


@_cli_handler
def load_mask(mask_path, *, device=None):
    """Load mask overlay from file/directory and send composited image."""
    from pathlib import Path

    from PIL import Image

    from trcc.services import ImageService, OverlayService

    if not os.path.exists(mask_path):
        print(f"Error: Path not found: {mask_path}")
        return 1

    svc = _device._get_service(device)
    if not svc.selected:
        print("No device found.")
        return 1

    dev = svc.selected
    w, h = dev.resolution

    # Find mask image
    p = Path(mask_path)
    if p.is_dir():
        mask_file = p / "01.png"
        if not mask_file.exists():
            mask_file = next(p.glob("*.png"), None)
        if not mask_file:
            print(f"Error: No PNG files in {mask_path}")
            return 1
    else:
        mask_file = p

    overlay = OverlayService(w, h)
    mask_img = Image.open(mask_file).convert('RGBA')
    overlay.set_mask(mask_img)

    # Black background + mask
    bg = ImageService.solid_color(0, 0, 0, w, h)
    overlay.set_background(bg)
    overlay.enabled = True
    result = overlay.render()

    svc.send_pil(result, w, h)
    print(f"Sent mask {mask_file.name} to {dev.path}")
    return 0


@_cli_handler
def render_overlay(dc_path, *, device=None, send=False, output=None):
    """Render overlay from DC config file."""
    from pathlib import Path

    from trcc.adapters.system.info import get_all_metrics
    from trcc.services import ImageService, OverlayService

    if not os.path.exists(dc_path):
        print(f"Error: Path not found: {dc_path}")
        return 1

    # Resolve device for resolution
    w, h = 320, 320
    svc = None
    if device or send:
        svc = _device._get_service(device)
        if svc and svc.selected:
            w, h = svc.selected.resolution

    overlay = OverlayService(w, h)

    # Load DC config
    p = Path(dc_path)
    dc_file = p / "config1.dc" if p.is_dir() else p
    display_opts = overlay.load_from_dc(dc_file)

    # Collect system metrics
    metrics = get_all_metrics()
    overlay.update_metrics(metrics)
    overlay.enabled = True

    # Black background
    bg = ImageService.solid_color(0, 0, 0, w, h)
    overlay.set_background(bg)
    result = overlay.render()

    if output:
        result.save(output)
        print(f"Saved overlay render to {output}")

    if send and svc and svc.selected:
        svc.send_pil(result, w, h)
        print(f"Sent overlay to {svc.selected.path}")

    if not output and not send:
        elements = len(overlay.config) if overlay.config else 0
        print(f"Overlay config loaded: {elements} elements ({w}x{h})")
        if display_opts:
            for k, v in display_opts.items():
                print(f"  {k}: {v}")

    return 0


def reset(device=None):
    """Reset/reinitialize the LCD device."""
    try:
        from trcc.services import ImageService

        print("Resetting LCD device...")
        svc = _device._get_service(device)
        if not svc.selected:
            print("No device found.")
            return 1

        dev = svc.selected
        w, h = dev.resolution
        print(f"  Device: {dev.path}")

        img = ImageService.solid_color(255, 0, 0, w, h)
        svc.send_pil(img, w, h)
        print("[OK] Device reset - displaying RED")
        return 0
    except Exception as e:
        print(f"Error resetting device: {e}")
        return 1


@_cli_handler
def resume():
    """Send last-used theme to each detected device (headless, no GUI)."""
    import time

    from trcc.conf import Settings
    from trcc.services import DeviceService, ImageService

    svc = DeviceService()

    # Wait for USB devices to appear (they may not be ready at boot)
    devices: list = []
    for attempt in range(10):
        devices = svc.detect()
        if devices:
            break
        print(f"Waiting for device... ({attempt + 1}/10)")
        time.sleep(2)

    if not devices:
        print("No compatible TRCC device detected.")
        return 1

    sent = 0
    for dev in devices:
        if dev.protocol != "scsi":
            continue

        # Discover resolution via handshake
        if dev.resolution == (0, 0):
            try:
                from trcc.adapters.device.factory import DeviceProtocolFactory
                proto = DeviceProtocolFactory.get_protocol(dev)
                result = proto.handshake()
                res = getattr(result, 'resolution', None) if result else None
                if isinstance(res, tuple) and len(res) == 2 and res != (0, 0):
                    dev.resolution = res
            except Exception:
                continue

        key = Settings.device_config_key(dev.device_index, dev.vid, dev.pid)
        cfg = Settings.get_device_config(key)
        theme_path = cfg.get("theme_path")

        if not theme_path:
            print(f"  [{dev.product}] No saved theme, skipping")
            continue

        # Find the image to send (00.png in theme dir, or direct file)
        image_path = None
        if os.path.isdir(theme_path):
            candidate = os.path.join(theme_path, "00.png")
            if os.path.exists(candidate):
                image_path = candidate
        elif os.path.isfile(theme_path):
            image_path = theme_path

        if not image_path:
            print(f"  [{dev.product}] Theme not found: {theme_path}")
            continue

        try:
            from PIL import Image

            img = Image.open(image_path).convert("RGB")
            w, h = dev.resolution
            img = ImageService.resize(img, w, h)

            # Apply brightness
            brightness_level = cfg.get("brightness_level", 3)
            brightness_pct = {1: 25, 2: 50, 3: 100}.get(brightness_level, 100)
            img = ImageService.apply_brightness(img, brightness_pct)

            # Apply rotation
            rotation = cfg.get("rotation", 0)
            img = ImageService.apply_rotation(img, rotation)

            # Send via service (auto byte-order)
            svc.select(dev)
            svc.send_pil(img, w, h)
            print(f"  [{dev.product}] Sent: {os.path.basename(theme_path)}")
            sent += 1
        except Exception as e:
            print(f"  [{dev.product}] Error: {e}")

    if sent == 0:
        print("No themes were sent. Use the GUI to set a theme first.")
        return 1

    print(f"Resumed {sent} device(s).")
    return 0
