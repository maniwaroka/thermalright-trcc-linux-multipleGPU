"""Product registry — the `(vid, pid) → ProductInfo` map.

Adding a new device = append a row.  Zero code changes elsewhere.  Data
is copied over from the legacy `core/models` registries when migrating
individual products; this file becomes the single source of truth once
Phase 1 lands.
"""
from __future__ import annotations

from typing import Tuple

from .models import Kind, ProductInfo, Wire

# =========================================================================
# ALL_DEVICES — (vid, pid) → ProductInfo
# =========================================================================


ALL_DEVICES: dict[Tuple[int, int], ProductInfo] = {
    # --- SCSI LCD (USB mass-storage passthrough) ---------------------
    (0x0402, 0x3922): ProductInfo(
        vid=0x0402, pid=0x3922,
        vendor="ALi",
        product="LCD 320x320",
        wire=Wire.SCSI, kind=Kind.LCD,
        device_type=1, fbl=100,
        native_resolution=(320, 320),
        orientations=(0, 90, 180, 270),
        native_orientation="landscape",
    ),

    # --- HID LCD Type 2 ("H" variant) --------------------------------
    (0x0416, 0x5302): ProductInfo(
        vid=0x0416, pid=0x5302,
        vendor="H",
        product="LCD 240x320",
        wire=Wire.HID, kind=Kind.LCD,
        device_type=2,
        native_resolution=(240, 320),
        orientations=(0, 90, 180, 270),
        native_orientation="portrait",
    ),

    # --- HID LCD Type 3 ("ALi" variant) ------------------------------
    (0x0418, 0x5303): ProductInfo(
        vid=0x0418, pid=0x5303,
        vendor="ALi",
        product="LCD 320x320",
        wire=Wire.HID, kind=Kind.LCD,
        device_type=3, fbl=100,
        native_resolution=(320, 320),
        orientations=(0, 90, 180, 270),
    ),
    (0x0418, 0x5304): ProductInfo(
        vid=0x0418, pid=0x5304,
        vendor="ALi",
        product="LCD 320x320 (alt)",
        wire=Wire.HID, kind=Kind.LCD,
        device_type=3, fbl=100,
        native_resolution=(320, 320),
        orientations=(0, 90, 180, 270),
    ),

    # TODO: remaining 12+ products — populated during Phase 8 port-over.
    # Keeps Phase 1 focused on shape validation with a representative
    # sample from each wire protocol (SCSI, HID Type 2, HID Type 3).
    # Bulk, LY, and LED products follow when their Device classes land.
}


# =========================================================================
# Lookups
# =========================================================================


def find_product(vid: int, pid: int) -> ProductInfo | None:
    """Look up a product by VID/PID, or None if unknown."""
    return ALL_DEVICES.get((vid, pid))


def products_by_wire(wire: Wire) -> list[ProductInfo]:
    """Return all products using a given wire protocol."""
    return [p for p in ALL_DEVICES.values() if p.wire is wire]


def products_by_kind(kind: Kind) -> list[ProductInfo]:
    """Return all products of a given kind (LCD or LED)."""
    return [p for p in ALL_DEVICES.values() if p.kind is kind]
