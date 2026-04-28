"""Shared CLI entry point — handles TRCC_NEXT opt-in then dispatches.

Used by both invocations:

    python -m trcc       →  __main__.py  →  this _entry.main()
    trcc (console script) →  pyproject `trcc = "trcc._entry:main"`  →  this

Setting ``TRCC_NEXT=1`` (or ``true`` / ``yes``) delegates the whole run
to the clean-slate ``trcc.next`` tree.  Unset → legacy behavior, which
is the default everyone gets.
"""
from __future__ import annotations

import os


def _next_opt_in_requested() -> bool:
    return os.environ.get("TRCC_NEXT", "").strip().lower() in ("1", "true", "yes")


def main() -> int | None:
    """Dispatch to next/ or legacy based on TRCC_NEXT."""
    if _next_opt_in_requested():
        from trcc.next.ui.cli.main import main as _next_main
        return _next_main()
    from trcc.ui.cli import main as _legacy_main
    return _legacy_main()
