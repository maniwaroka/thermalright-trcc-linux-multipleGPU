"""GUI test fixtures."""
from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture()
def bare_trcc_app(qapp):
    """Yield a bare TRCCApp instance with __init__ skipped.

    Resets the singleton before and after — safe for parallel test workers.
    """
    from trcc.gui.trcc_app import TRCCApp

    TRCCApp._instance = None
    with patch.object(TRCCApp, '__init__', lambda self, *a, **kw: None):
        inst = TRCCApp.__new__(TRCCApp)
    yield inst
    TRCCApp._instance = None
