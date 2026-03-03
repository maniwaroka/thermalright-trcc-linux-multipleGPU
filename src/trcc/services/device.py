"""Device detection, selection, and frame sending service.

Pure Python, no Qt dependencies.
Absorbed from DeviceController + DeviceModel in controllers.py/models.py.
"""
from __future__ import annotations

import logging
import queue
import threading
from typing import Any, Optional

from ..core.models import DeviceInfo

log = logging.getLogger(__name__)


class DeviceService:
    """Device lifecycle: detect, select, handshake, send."""

    def __init__(self, get_protocol: Any = None) -> None:
        self._devices: list[DeviceInfo] = []
        self._selected: DeviceInfo | None = None
        self._send_lock = threading.Lock()
        self._send_busy = False
        self.on_frame_sent: Any = None  # callback(PIL Image) — called after every send_pil
        self._send_queue: queue.Queue = queue.Queue(maxsize=1)
        self._send_worker: threading.Thread | None = None
        self._get_protocol = get_protocol  # DIP: injected factory function

    # ── Detection ────────────────────────────────────────────────────

    def detect(self) -> list[DeviceInfo]:
        """Scan for all connected LCD/LED/Bulk devices via device_detector."""
        log.debug("DeviceService: scanning for devices...")
        try:
            from ..adapters.device.registry_detector import DetectedDevice, DeviceDetector

            raw: list[DetectedDevice] = DeviceDetector.detect()
            self._devices = [
                DeviceInfo(
                    name=f"{d.vendor_name} {d.product_name}",
                    path=d.scsi_device or f"hid:{d.vid:04x}:{d.pid:04x}",
                    vendor=d.vendor_name,
                    product=d.product_name,
                    model=d.model,
                    vid=d.vid,
                    pid=d.pid,
                    device_index=i,
                    protocol=d.protocol,
                    device_type=d.device_type,
                    implementation=d.implementation,
                )
                for i, d in enumerate(raw)
            ]
        except ImportError:
            self._devices = []

        # Enrich LED devices with probe data (PM → style, model name).
        for d, raw_d in zip(self._devices, raw):
            if d.implementation == 'hid_led':
                self._enrich_led_device(d, raw_d.usb_path)

        log.info("DeviceService: found %d device(s)", len(self._devices))
        for d in self._devices:
            log.debug("  %s [%04X:%04X] %s res=%s",
                      d.name, d.vid, d.pid, d.protocol, d.resolution)

        # Auto-select first device
        if self._devices and not self._selected:
            self._selected = self._devices[0]

        return self._devices

    @staticmethod
    def _enrich_led_device(device: DeviceInfo, usb_path: str) -> None:
        """Probe LED device to resolve PM → style and model name.

        Without this, all 0416:8001 devices start as generic "LED_DIGITAL"
        which falls back to style 1 (AX120_DIGITAL, 30 LEDs) — wrong for
        multi-segment devices like PA120 (84), LF8 (93), etc.
        """
        try:
            from ..adapters.device.adapter_led import probe_led_model
            info = probe_led_model(device.vid, device.pid, usb_path=usb_path)
            if info and info.style:
                device.led_style_id = info.style.style_id
                device.model = info.style.model_name
                log.debug("LED probe: PM=%d → style=%d model=%s",
                          info.pm, info.style.style_id, info.style.model_name)
        except Exception as e:
            log.debug("LED probe failed: %s", e)

    # ── Selection ────────────────────────────────────────────────────

    def select(self, device: DeviceInfo) -> None:
        """Select a device."""
        self._selected = device

    @property
    def selected(self) -> DeviceInfo | None:
        """Currently selected device."""
        return self._selected

    @property
    def devices(self) -> list[DeviceInfo]:
        """List of detected devices."""
        return self._devices

    # ── Protocol resolution (DIP) ────────────────────────────────────

    def _get_proto(self, device: DeviceInfo) -> Any:
        """Get protocol for device — uses injected factory or default."""
        if self._get_protocol:
            return self._get_protocol(device)
        from ..adapters.device.abstract_factory import DeviceProtocolFactory
        return DeviceProtocolFactory.get_protocol(device)

    # ── Handshake ────────────────────────────────────────────────────

    def handshake(self, device: DeviceInfo) -> Any:
        """Run protocol handshake for HID/Bulk devices.

        Returns:
            HandshakeResult or None on error/import failure.
        """
        try:
            protocol = self._get_proto(device)
            if hasattr(protocol, 'handshake'):
                return protocol.handshake()
        except Exception as e:
            log.error("Handshake error: %s", e)
        return None

    # ── Send ─────────────────────────────────────────────────────────

    def send_pil(self, image: Any, width: int, height: int) -> bool:
        """Send PIL/numpy image to device.

        Protocol knows its FBL from handshake — encoding is internal.
        """
        if not self._selected:
            return False
        try:
            protocol = self._get_proto(self._selected)
            ok = protocol.send_pil(image, width, height)
            if ok and self.on_frame_sent:
                self.on_frame_sent(image)
            return ok
        except Exception as e:
            log.error("Device send error: %s", e)
            return False

    def send_pil_async(self, image: Any, width: int, height: int) -> None:
        """Send PIL image via persistent worker thread. Drops frame if busy."""
        self._submit(lambda: self.send_pil(image, width, height))

    def _submit(self, job: Any) -> None:
        """Submit a send job to the persistent worker. Drops if queue full."""
        self._ensure_worker()
        try:
            self._send_queue.put_nowait(job)
        except queue.Full:
            pass  # drop frame — worker still processing previous

    def _ensure_worker(self) -> None:
        """Start the persistent send worker thread if not alive."""
        if self._send_worker and self._send_worker.is_alive():
            return
        self._send_worker = threading.Thread(
            target=self._send_loop, daemon=True, name="device-send")
        self._send_worker.start()

    def _send_loop(self) -> None:
        """Persistent worker: process send jobs from the queue."""
        while True:
            try:
                job = self._send_queue.get(timeout=30)
                job()
            except queue.Empty:
                return  # idle for 30s → exit thread (re-created on next submit)
            except Exception:
                log.exception("Send worker error")

    @property
    def is_busy(self) -> bool:
        """Check if a send is in progress."""
        with self._send_lock:
            return self._send_busy

    # ── Protocol info ────────────────────────────────────────────────

    def get_protocol_info(self) -> Optional[Any]:
        """Get protocol/backend info for the selected device."""
        try:
            from ..adapters.device.abstract_factory import DeviceProtocolFactory

            return DeviceProtocolFactory.get_protocol_info(self._selected)
        except ImportError:
            return None
