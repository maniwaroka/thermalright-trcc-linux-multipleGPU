"""Re-export stub — SCSI protocol moved to adapters/transport/adapter_scsi.py.

All names re-exported for backward compatibility. Detection functions
(find_lcd_devices, send_image_to_device) included via the transport copy
until the detection/ refactor phase separates them.
"""
from trcc.adapters.transport.adapter_scsi import *  # noqa: F401,F403
from trcc.adapters.transport.adapter_scsi import (  # noqa: F401 — private names for tests
    _BOOT_MAX_RETRIES,
    _BOOT_SIGNATURE,
    _BOOT_WAIT_SECONDS,
    _CHUNK_SIZE_LARGE,
    _CHUNK_SIZE_SMALL,
    _FRAME_CMD_BASE,
    _POST_INIT_DELAY,
    _device_fds,
    _load_saved_identity,
    _sg_io_available,
    _sg_io_read,
    _sg_io_write,
    _write_bufs,
)
