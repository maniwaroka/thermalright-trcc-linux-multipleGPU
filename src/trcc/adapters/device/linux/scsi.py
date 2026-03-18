"""Linux SG_IO ioctl bridge — direct SCSI passthrough (no subprocess fork).

Provides raw SG_IO read/write via fcntl.ioctl. Platform-specific — only
works on Linux. Other platforms use bridge_windows.py, bridge_macos.py, etc.
"""
from __future__ import annotations

import ctypes
import logging
import os

log = logging.getLogger(__name__)

# =========================================================================
# SG_IO ioctl constants and structures
# =========================================================================

_SG_IO = 0x2285
_SG_DXFER_TO_DEV = -2
_SG_DXFER_FROM_DEV = -3


class _SgIoHdr(ctypes.Structure):
    """Linux sg_io_hdr_t for SG_IO ioctl."""

    _fields_ = [
        ('interface_id', ctypes.c_int),
        ('dxfer_direction', ctypes.c_int),
        ('cmd_len', ctypes.c_ubyte),
        ('mx_sb_len', ctypes.c_ubyte),
        ('iovec_count', ctypes.c_ushort),
        ('dxfer_len', ctypes.c_uint),
        ('dxferp', ctypes.c_void_p),
        ('cmdp', ctypes.c_void_p),
        ('sbp', ctypes.c_void_p),
        ('timeout', ctypes.c_uint),
        ('flags', ctypes.c_uint),
        ('pack_id', ctypes.c_int),
        ('usr_ptr', ctypes.c_void_p),
        ('status', ctypes.c_ubyte),
        ('masked_status', ctypes.c_ubyte),
        ('msg_status', ctypes.c_ubyte),
        ('sb_len_wr', ctypes.c_ubyte),
        ('host_status', ctypes.c_ushort),
        ('driver_status', ctypes.c_ushort),
        ('resid', ctypes.c_int),
        ('duration', ctypes.c_uint),
        ('info', ctypes.c_uint),
    ]


# Module state: None = untested, True/False = tested
sg_io_available: bool | None = None
# Cached file descriptors per device path
_device_fds: dict[str, int] = {}

# Pre-allocated SG_IO buffers per data size (avoids ctypes alloc per write).
# Key: data length → (cdb_buf, data_buf, sense_buf, hdr, ioctl_buf)
_SG_HDR_SIZE = ctypes.sizeof(_SgIoHdr)
_write_bufs: dict[int, tuple] = {}


def _get_device_fd(dev: str) -> int:
    """Get or open a file descriptor for the SCSI generic device."""
    fd = _device_fds.get(dev)
    if fd is not None:
        return fd
    fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)
    _device_fds[dev] = fd
    return fd


def close_device_fd(dev: str) -> None:
    """Close and remove cached fd."""
    fd = _device_fds.pop(dev, None)
    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            pass


def _get_write_bufs(cdb_len: int, data_len: int) -> tuple:
    """Get or create pre-allocated ctypes buffers for a given data size."""
    bufs = _write_bufs.get(data_len)
    if bufs is not None:
        return bufs
    cdb_buf = (ctypes.c_ubyte * cdb_len)()
    data_buf = (ctypes.c_ubyte * data_len)()
    sense_buf = (ctypes.c_ubyte * 32)()
    hdr = _SgIoHdr()
    ioctl_buf = ctypes.create_string_buffer(_SG_HDR_SIZE)
    # Pre-fill immutable header fields
    hdr.interface_id = ord('S')
    hdr.dxfer_direction = _SG_DXFER_TO_DEV
    hdr.cmd_len = cdb_len
    hdr.mx_sb_len = 32
    hdr.dxfer_len = data_len
    hdr.dxferp = ctypes.addressof(data_buf)
    hdr.cmdp = ctypes.addressof(cdb_buf)
    hdr.sbp = ctypes.addressof(sense_buf)
    hdr.timeout = 10000
    bufs = (cdb_buf, data_buf, sense_buf, hdr, ioctl_buf)
    _write_bufs[data_len] = bufs
    return bufs


def sg_io_write(dev: str, cdb: bytes, data: bytes) -> bool:
    """SCSI write via SG_IO ioctl. No subprocess, no temp file."""
    import fcntl  # Unix-only

    fd = _get_device_fd(dev)

    cdb_buf, data_buf, _sense, hdr, ioctl_buf = _get_write_bufs(len(cdb), len(data))
    # Copy payload into pre-allocated buffers
    ctypes.memmove(cdb_buf, cdb, len(cdb))
    ctypes.memmove(data_buf, data, len(data))

    ctypes.memmove(ioctl_buf, ctypes.addressof(hdr), _SG_HDR_SIZE)
    fcntl.ioctl(fd, _SG_IO, ioctl_buf)
    ctypes.memmove(ctypes.addressof(hdr), ioctl_buf, _SG_HDR_SIZE)

    return hdr.status == 0


def sg_io_read(dev: str, cdb: bytes, length: int) -> bytes:
    """SCSI read via SG_IO ioctl. No subprocess."""
    import fcntl  # Unix-only

    fd = _get_device_fd(dev)

    cdb_buf = (ctypes.c_ubyte * len(cdb)).from_buffer_copy(cdb)
    data_buf = (ctypes.c_ubyte * length)()
    sense_buf = (ctypes.c_ubyte * 32)()

    hdr = _SgIoHdr()
    hdr.interface_id = ord('S')
    hdr.dxfer_direction = _SG_DXFER_FROM_DEV
    hdr.cmd_len = len(cdb)
    hdr.mx_sb_len = 32
    hdr.dxfer_len = length
    hdr.dxferp = ctypes.addressof(data_buf)
    hdr.cmdp = ctypes.addressof(cdb_buf)
    hdr.sbp = ctypes.addressof(sense_buf)
    hdr.timeout = 10000

    buf = ctypes.create_string_buffer(ctypes.sizeof(hdr))
    ctypes.memmove(buf, ctypes.addressof(hdr), ctypes.sizeof(hdr))
    fcntl.ioctl(fd, _SG_IO, buf)
    ctypes.memmove(ctypes.addressof(hdr), buf, ctypes.sizeof(hdr))

    if hdr.status != 0:
        return b''
    actual = length - hdr.resid
    return bytes(data_buf[:actual])
