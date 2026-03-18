"""macOS SCSI passthrough via pyusb bulk transfers.

On macOS, the kernel driver (IOUSBMassStorageClass) claims USB mass
storage devices exclusively. There is no sg_raw equivalent. Instead,
we detach the kernel driver and send raw SCSI CDBs as USB Bulk-Only
Transport (BOT) transfers via pyusb.

This requires root privileges or a signed app with Apple entitlements.
Same approach as Linux pyusb transport, but with explicit kernel driver
detach since macOS doesn't have udev rules.

Requires: brew install libusb
"""
from __future__ import annotations

import logging
import struct
from typing import Any, Optional

log = logging.getLogger(__name__)

# USB Bulk-Only Transport (BOT) constants
CBW_SIGNATURE = 0x43425355  # Command Block Wrapper
CBW_SIZE = 31
CSW_SIZE = 13


class MacOSScsiTransport:
    """Send raw SCSI commands to a USB mass-storage device on macOS.

    Uses pyusb bulk transfers with USB BOT (Bulk-Only Transport) protocol.
    The kernel driver must be detached first (requires root).

    Usage:
        transport = MacOSScsiTransport(vid=0x0416, pid=0x5020)
        transport.open()
        transport.send_cdb(cdb_bytes, data_bytes)
        transport.close()
    """

    def __init__(self, vid: int, pid: int) -> None:
        self._vid = vid
        self._pid = pid
        self._dev: Any = None
        self._ep_out: Optional[int] = None
        self._ep_in: Optional[int] = None
        self._tag = 0

    def open(self) -> bool:
        """Find device, detach kernel driver, claim interface."""
        try:
            import usb.core  # pyright: ignore[reportMissingImports]
            import usb.util  # pyright: ignore[reportMissingImports]
        except ImportError:
            log.error("pyusb not installed")
            return False

        dev: Any = usb.core.find(idVendor=self._vid, idProduct=self._pid)
        if dev is None:
            log.error("Device %04X:%04X not found", self._vid, self._pid)
            return False

        try:
            # macOS requires explicit kernel driver detach (needs root)
            if dev.is_kernel_driver_active(0):
                dev.detach_kernel_driver(0)
                log.info("Detached macOS kernel driver for %04X:%04X",
                         self._vid, self._pid)

            dev.set_configuration()

            # Find bulk endpoints
            cfg = dev.get_active_configuration()
            intf = cfg[(0, 0)]
            for ep in intf:
                if usb.util.endpoint_direction(ep.bEndpointAddress) == \
                        usb.util.ENDPOINT_OUT:
                    self._ep_out = ep.bEndpointAddress
                elif usb.util.endpoint_direction(ep.bEndpointAddress) == \
                        usb.util.ENDPOINT_IN:
                    self._ep_in = ep.bEndpointAddress

            if self._ep_out is None or self._ep_in is None:
                log.error("Could not find bulk endpoints")
                return False

            self._dev = dev
            return True

        except Exception:
            log.exception("Failed to open macOS SCSI device %04X:%04X",
                          self._vid, self._pid)
            return False

    def close(self) -> None:
        """Release interface and re-attach kernel driver."""
        if self._dev is not None:
            try:
                import usb.util  # pyright: ignore[reportMissingImports]
                usb.util.dispose_resources(self._dev)
                # Re-attach kernel driver so Finder can see the device again
                try:
                    self._dev.attach_kernel_driver(0)
                except Exception:
                    pass
            except Exception:
                pass
            self._dev = None

    def send_cdb(
        self,
        cdb: bytes,
        data: bytes,
        *,
        timeout: int = 5000,
    ) -> bool:
        """Send a SCSI CDB with data payload via USB BOT.

        Args:
            cdb: SCSI Command Descriptor Block (6-16 bytes)
            data: Data to send (frame bytes for LCD)
            timeout: Timeout in milliseconds

        Returns:
            True if transfer succeeded
        """
        if self._dev is None or self._ep_out is None:
            log.error("macOS SCSI device not open")
            return False

        self._tag += 1

        # Build Command Block Wrapper (CBW)
        cbw = struct.pack(
            '<IIIBBB',
            CBW_SIGNATURE,
            self._tag,
            len(data),
            0x00,  # Direction: host-to-device
            0,     # LUN
            len(cdb),
        )
        cbw += cdb.ljust(16, b'\x00')

        try:
            # Send CBW
            self._dev.write(self._ep_out, cbw, timeout=timeout)

            # Send data
            if data:
                self._dev.write(self._ep_out, data, timeout=timeout)

            # Read CSW (Command Status Wrapper)
            csw = self._dev.read(self._ep_in, CSW_SIZE, timeout=timeout)
            if len(csw) >= CSW_SIZE:
                status = csw[12]
                if status != 0:
                    log.warning("SCSI command status %d", status)
                    return False
            return True

        except Exception:
            log.exception("macOS SCSI transfer failed")
            return False

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()
