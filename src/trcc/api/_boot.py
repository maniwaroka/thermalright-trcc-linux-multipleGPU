"""API composition root — long-lived Trcc singleton for the FastAPI process.

Unlike the CLI (one Trcc per invocation), the API builds Trcc once at
startup and reuses it across every request. Endpoints fetch it via
`get_trcc()`.

Composition pattern:
    from trcc.api._boot import get_trcc
    result = get_trcc().lcd.set_brightness(0, 50)
    return asdict(result)
"""
from __future__ import annotations

import logging
import threading

from trcc.core.trcc import Trcc

log = logging.getLogger(__name__)

_trcc: Trcc | None = None
_lock = threading.Lock()


def get_trcc() -> Trcc:
    """Return the Trcc singleton, building it on first access.

    Idempotent + thread-safe — multiple API workers don't race.
    """
    global _trcc  # noqa: PLW0603
    with _lock:
        if _trcc is None:
            from trcc.cli import _make_cli_renderer  # same offscreen Qt setup as CLI
            log.info('API: building Trcc (offscreen renderer + discovery)...')
            t = Trcc.for_current_os()
            t.bootstrap()
            t.with_renderer(_make_cli_renderer())
            t.discover()
            _trcc = t
            log.info('API: Trcc ready')
        return _trcc


def shutdown() -> None:
    """Release the Trcc singleton. Called by FastAPI on shutdown."""
    global _trcc  # noqa: PLW0603
    with _lock:
        if _trcc is not None:
            try:
                _trcc.cleanup()
            except Exception:
                log.exception('API: Trcc cleanup failed')
            _trcc = None
