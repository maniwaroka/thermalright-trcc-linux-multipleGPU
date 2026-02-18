"""LED color, mode, brightness control commands."""
from __future__ import annotations

from trcc.cli import _cli_handler


def _get_led_service():
    """Detect LED device and create initialized LEDService."""
    from trcc.adapters.device.detector import detect_devices
    from trcc.services import LEDService

    devices = detect_devices()
    led_dev = next(
        (d for d in devices if d.implementation == 'hid_led'), None)
    if not led_dev:
        return None, None

    led_svc = LEDService()
    from trcc.adapters.device.led import probe_led_model
    info = probe_led_model(led_dev.vid, led_dev.pid,
                           usb_path=led_dev.usb_path)
    if info and info.style:
        style_id = info.style.style_id
    else:
        style_id = 1

    status = led_svc.initialize(led_dev, style_id)
    return led_svc, status


@_cli_handler
def set_color(hex_color):
    """Set LED static color."""
    hex_color = hex_color.lstrip('#')
    if len(hex_color) != 6:
        print("Error: Invalid hex color. Use format: ff0000")
        return 1

    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)

    from trcc.core.models import LEDMode

    led_svc, status = _get_led_service()
    if not led_svc:
        print("No LED device found.")
        return 1

    print(status)
    led_svc.set_mode(LEDMode.STATIC)
    led_svc.set_color(r, g, b)
    led_svc.toggle_global(True)
    led_svc.send_tick()
    led_svc.save_config()

    print(f"LED color set to #{hex_color}")
    return 0


def set_mode(mode_name):
    """Set LED effect mode."""
    try:
        import time

        from trcc.core.models import LEDMode

        mode_map = {
            'static': LEDMode.STATIC,
            'breathing': LEDMode.BREATHING,
            'colorful': LEDMode.COLORFUL,
            'rainbow': LEDMode.RAINBOW,
        }

        mode = mode_map.get(mode_name.lower())
        if not mode:
            print(f"Error: Unknown mode '{mode_name}'")
            print(f"Available: {', '.join(mode_map)}")
            return 1

        led_svc, status = _get_led_service()
        if not led_svc:
            print("No LED device found.")
            return 1

        print(status)
        led_svc.set_mode(mode)
        led_svc.toggle_global(True)

        if mode in (LEDMode.BREATHING, LEDMode.COLORFUL, LEDMode.RAINBOW):
            print(f"LED mode: {mode_name} (running animation, Ctrl+C to stop)")
            try:
                while True:
                    led_svc.send_tick()
                    time.sleep(0.05)
            except KeyboardInterrupt:
                pass
            print("\nStopped.")
        else:
            led_svc.send_tick()
            print(f"LED mode: {mode_name}")

        led_svc.save_config()
        return 0
    except Exception as e:
        print(f"Error: {e}")
        return 1


@_cli_handler
def set_led_brightness(level):
    """Set LED brightness (0-100)."""
    if level < 0 or level > 100:
        print("Error: Brightness must be 0-100")
        return 1

    led_svc, status = _get_led_service()
    if not led_svc:
        print("No LED device found.")
        return 1

    print(status)
    led_svc.set_brightness(level)
    led_svc.toggle_global(True)
    led_svc.send_tick()
    led_svc.save_config()

    print(f"LED brightness set to {level}%")
    return 0


@_cli_handler
def led_off():
    """Turn LEDs off."""
    led_svc, status = _get_led_service()
    if not led_svc:
        print("No LED device found.")
        return 1

    print(status)
    led_svc.toggle_global(False)
    led_svc.send_tick()
    led_svc.save_config()

    print("LEDs turned off.")
    return 0


@_cli_handler
def set_sensor_source(source):
    """Set CPU/GPU sensor source for temp/load linked LED modes."""
    source = source.lower()
    if source not in ('cpu', 'gpu'):
        print("Error: Source must be 'cpu' or 'gpu'")
        return 1

    led_svc, status = _get_led_service()
    if not led_svc:
        print("No LED device found.")
        return 1

    print(status)
    led_svc.set_sensor_source(source)
    led_svc.save_config()

    print(f"LED sensor source set to {source.upper()}")
    return 0
