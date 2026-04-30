"""API composition root — long-lived Trcc singleton for the FastAPI process.

Unlike the CLI (one Trcc per invocation), the API builds Trcc once at
startup and reuses it across every request. Endpoints fetch it via
`get_trcc()`.

Composition pattern (production):
    from trcc.ui.api._boot import get_trcc
    result = get_trcc().lcd.set_brightness(0, 50)
    return asdict(result)

Test/dev injection — seed the singleton with a specific Platform before
any endpoint runs:
    get_trcc(platform=MockPlatform(specs))   # warms the cache
    # Subsequent get_trcc() calls return the cached, mock-backed Trcc.
"""
from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from trcc.core.trcc import Trcc

if TYPE_CHECKING:
    from trcc.core.ports import Platform

log = logging.getLogger(__name__)

_trcc: Trcc | None = None
_lock = threading.Lock()


def get_trcc(platform: Platform | None = None) -> Trcc:
    """Return the Trcc singleton, building it on first access.

    Idempotent + thread-safe — multiple API workers don't race. Pass
    ``platform`` once (typically from a dev/test bootstrapper) to build
    the singleton with a specific Platform; omit thereafter.
    """
    global _trcc
    with _lock:
        if _trcc is None:
            from trcc.ui.cli import _make_cli_renderer  # same offscreen Qt setup as CLI
            log.info('API: building Trcc (offscreen renderer + discovery)...')
            t = Trcc(platform) if platform is not None else Trcc.for_current_os()
            t.bootstrap()
            t.with_renderer(_make_cli_renderer())
            t.discover()
            _trcc = t
            log.info('API: Trcc ready')
        return _trcc


def shutdown() -> None:
    """Release the Trcc singleton. Called by FastAPI on shutdown."""
    global _trcc
    with _lock:
        if _trcc is not None:
            try:
                _trcc.cleanup()
            except Exception:
                log.exception('API: Trcc cleanup failed')
            _trcc = None
