"""LCD display control endpoints — brightness, rotation, color, mask, overlay, preview."""
from __future__ import annotations

import asyncio
import hmac
import io
import json
import logging

from fastapi import APIRouter, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from trcc.api.models import (
    BrightnessRequest,
    HexColorRequest,
    RotationRequest,
    SplitRequest,
    dispatch_result,
    parse_hex_or_400,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/display", tags=["display"])


def _get_display():
    """Get the active DisplayDispatcher, raise 409 if not connected."""
    from trcc.api import _display_dispatcher

    if not _display_dispatcher or not _display_dispatcher.connected:
        raise HTTPException(status_code=409, detail="No LCD device selected. POST /devices/{id}/select first.")
    return _display_dispatcher


def _display_route(method: str, *args, **kwargs) -> dict:
    """Generic: get display dispatcher, call method, capture image, return result."""
    result = getattr(_get_display(), method)(*args, **kwargs)
    if result.get("image"):
        from trcc.api import set_current_image
        set_current_image(result["image"])
    return dispatch_result(result)


@router.post("/color")
def set_color(body: HexColorRequest) -> dict:
    """Send solid color to LCD."""
    r, g, b = parse_hex_or_400(body.hex)
    return _display_route("send_color", r, g, b)


@router.post("/brightness")
def set_brightness(body: BrightnessRequest) -> dict:
    """Set display brightness (1=25%, 2=50%, 3=100%). Persists to config."""
    return _display_route("set_brightness", body.level)


@router.post("/rotation")
def set_rotation(body: RotationRequest) -> dict:
    """Set display rotation (0, 90, 180, 270). Persists to config."""
    return _display_route("set_rotation", body.degrees)


@router.post("/split")
def set_split(body: SplitRequest) -> dict:
    """Set split mode (0=off, 1-3=Dynamic Island). Persists to config."""
    return _display_route("set_split_mode", body.mode)


@router.post("/reset")
def reset_display() -> dict:
    """Reset device by sending solid red frame."""
    return _display_route("reset")


@router.post("/mask")
async def load_mask(image: UploadFile) -> dict:
    """Upload and apply mask overlay (PNG)."""
    import tempfile
    from pathlib import Path

    lcd = _get_display()

    data = await image.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Mask image exceeds 10 MB limit")

    # Write to temp file for dispatcher (expects path)
    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name

    try:
        result = lcd.load_mask(tmp_path)
        return dispatch_result(result)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@router.post("/overlay")
async def render_overlay(dc_path: str, send: bool = True) -> dict:
    """Render overlay from DC config path and optionally send to device."""
    lcd = _get_display()
    result = lcd.render_overlay(dc_path, send=send)
    return dispatch_result(result)


@router.get("/status")
def display_status() -> dict:
    """Get current display state — resolution, device path, connection."""
    from trcc.api import _display_dispatcher

    if not _display_dispatcher or not _display_dispatcher.connected:
        return {"connected": False}

    lcd = _display_dispatcher
    return {
        "connected": True,
        "resolution": lcd.resolution,
        "device_path": lcd.device_path,
    }


# ── Preview endpoints ─────────────────────────────────────────────────


@router.get("/preview")
def display_preview() -> Response:
    """Return the current LCD frame as a PNG image."""
    from trcc.api import _current_image

    if _current_image is None:
        raise HTTPException(status_code=503, detail="No image available")

    buf = io.BytesIO()
    _current_image.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


@router.websocket("/preview/stream")
async def preview_stream(websocket: WebSocket):
    """Live JPEG stream of the current LCD frame.

    Auth: ``?token=`` query param (checked against configured API token).
    Change detection: only sends when the PIL Image object changes (``id()``).
    Client control: send JSON ``{"fps": N}``, ``{"quality": N}``, ``{"pause": bool}``.
    """
    from trcc.api import _api_token

    # ── Auth ──────────────────────────────────────────────────────────
    if _api_token:
        query_token = websocket.query_params.get("token", "")
        if not hmac.compare_digest(query_token, _api_token):
            await websocket.close(code=4001, reason="Unauthorized")
            return

    await websocket.accept()

    last_image_id: int | None = None
    fps = 10
    quality = 85
    paused = False

    try:
        while True:
            # ── Check for client control messages (non-blocking) ──────
            try:
                raw = await asyncio.wait_for(
                    websocket.receive_text(), timeout=1.0 / fps,
                )
                try:
                    msg = json.loads(raw)
                    if "fps" in msg:
                        fps = max(1, min(30, int(msg["fps"])))
                    if "quality" in msg:
                        quality = max(10, min(100, int(msg["quality"])))
                    if "pause" in msg:
                        paused = bool(msg["pause"])
                except (json.JSONDecodeError, ValueError, TypeError):
                    pass
                continue  # restart loop after processing message
            except asyncio.TimeoutError:
                pass  # no message — proceed to frame check

            if paused:
                continue

            # ── Read current image (re-import to get latest ref) ──────
            from trcc.api import _current_image as current  # noqa: F811

            if current is None or id(current) == last_image_id:
                continue  # no change — wait for next tick

            # ── Encode and send new frame ─────────────────────────────
            buf = io.BytesIO()
            current.save(buf, format="JPEG", quality=quality)
            await websocket.send_bytes(buf.getvalue())
            last_image_id = id(current)

    except WebSocketDisconnect:
        pass
    except Exception:
        log.debug("Preview stream closed", exc_info=True)
