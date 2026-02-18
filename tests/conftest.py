"""Shared test fixtures for the TRCC Linux test suite."""
from __future__ import annotations

from unittest.mock import MagicMock

from PIL import Image

from trcc.core.models import DeviceInfo


def make_device_info(
    path: str = "/dev/sg0",
    name: str = "LCD",
    vid: int = 0x87CD,
    pid: int = 0x70DB,
    protocol: str = "scsi",
    resolution: tuple[int, int] = (320, 320),
    **kw,
) -> DeviceInfo:
    """Create a DeviceInfo with sensible test defaults."""
    return DeviceInfo(
        name=name, path=path, vid=vid, pid=pid,
        protocol=protocol, resolution=resolution, **kw,
    )


def make_mock_device(
    path: str = "/dev/sg0",
    name: str = "LCD",
    vid: int = 0x87CD,
    pid: int = 0x70DB,
    protocol: str = "scsi",
) -> MagicMock:
    """Create a MagicMock DetectedDevice with sensible defaults."""
    dev = MagicMock()
    dev.scsi_device = path
    dev.product_name = name
    dev.vid = vid
    dev.pid = pid
    dev.protocol = protocol
    dev.usb_path = "1-2"
    dev.vendor_name = "Thermalright"
    return dev


def make_mock_service(device: DeviceInfo | None = None) -> MagicMock:
    """Create a mock DeviceService with a pre-selected device."""
    svc = MagicMock()
    dev = device or make_device_info()
    svc.selected = dev
    svc.devices = [dev]
    svc.detect.return_value = svc.devices
    svc.send_pil.return_value = True
    return svc


def make_test_image(
    w: int = 320, h: int = 320, color: tuple[int, int, int] = (128, 0, 0),
) -> Image.Image:
    """Create a minimal PIL RGB image for testing."""
    return Image.new("RGB", (w, h), color)


def save_test_png(path: str, w: int = 320, h: int = 320) -> None:
    """Write a minimal valid PNG at *path*."""
    make_test_image(w, h).save(path, "PNG")
