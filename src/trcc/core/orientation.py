"""Display orientation geometry — pure functions, no I/O.

Follows the core/encoding.py pattern: standalone pure-domain computations
that both services/ and conf.py can import without circular deps.

C# equivalents:
    effective_resolution → directionB + is{W}x{H} flags → GetFileListMBDir()
    image_rotation       → directionB → RotateImg() dispatch in ImageToJpg
"""
from __future__ import annotations


def effective_resolution(w: int, h: int, rotation: int) -> tuple[int, int]:
    """Canvas resolution after rotation — swaps w,h for non-square at 90/270.

    Square displays (320x320, 480x480) are unaffected by rotation.
    Non-square displays (800x480, 1600x720) swap dimensions for portrait.

    C# GetFileListMBDir: ``directionB == 0 || directionB == 180`` → landscape,
    else portrait (swapped dims).
    """
    if w != h and rotation in (90, 270):
        return (h, w)
    return (w, h)


def image_rotation(w: int, h: int, rotation: int) -> int:
    """Rotation angle to apply to images.

    Non-square at 90/270: canvas is already portrait (effective_resolution
    swapped dims), so no image rotation needed — return 0.
    Square or 0/180: return actual rotation angle.
    """
    if w != h and rotation in (90, 270):
        return 0
    return rotation
