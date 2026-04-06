"""API layer test fixtures — injectable fake TrccApp states.

Hexagonal testing principle: tests declare WHAT scenario they need,
not HOW to wire it. Each fixture is a fake adapter that satisfies the
TrccApp port contract for a specific device-connection scenario.

Fixtures:
  lcd_only_app  — LCD device connected, LED absent
  no_device_app — no device found (scan returns nothing)
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def lcd_only_app(monkeypatch):
    """Fake TrccApp: LCD connects, LED absent.

    DI chain: discover() → scan() → has_lcd=True, lcd_device wired
                                   → has_led=False (no LED found)

    Mock LCD device methods return plain dicts.
    DataManager.ensure_all is replaced with a spy (records (w, h) calls,
    no network I/O). Access via: app.ensure_all_calls.
    """
    from trcc.adapters.infra.data_repository import DataManager
    from trcc.core.app import TrccApp

    ensure_all_calls: list = []
    monkeypatch.setattr(
        DataManager, "ensure_all",
        classmethod(lambda cls, w, h: ensure_all_calls.append((w, h))),
    )

    TrccApp.reset()
    app = TrccApp(MagicMock())

    mock_lcd = MagicMock()
    mock_lcd.restore_last_theme.return_value = {"success": True, "image": None}

    # Fake discover: wires device into _devices dict
    mock_lcd.is_lcd = True
    mock_lcd.is_led = False
    def _fake_discover(path=None):
        app._devices['mock_lcd'] = mock_lcd
        return {"success": True, "message": "1 device(s) found"}

    app.discover = _fake_discover  # type: ignore[method-assign]

    TrccApp._instance = app  # type: ignore[assignment]
    app.ensure_all_calls = ensure_all_calls  # type: ignore[attr-defined]
    yield app, mock_lcd
    TrccApp.reset()


@pytest.fixture
def no_device_app(monkeypatch):
    """Fake TrccApp: no device found (scan returns nothing).

    DI chain: discover() → scan() → no devices → has_lcd=False, has_led=False

    lcd_from_service() returns a mock so the fallback path doesn't crash.
    DataManager.ensure_all is a no-op (no network calls in tests).
    """
    from trcc.adapters.infra.data_repository import DataManager
    from trcc.core.app import TrccApp

    monkeypatch.setattr(DataManager, "ensure_all", classmethod(lambda cls, *a, **kw: None))

    TrccApp.reset()
    app = TrccApp(MagicMock())

    mock_lcd = MagicMock()
    app.lcd_from_service = lambda svc: mock_lcd  # type: ignore[method-assign]

    # Fake discover: finds nothing
    app.discover = lambda path=None: {"success": True, "message": "0 device(s) found"}  # type: ignore[method-assign]

    TrccApp._instance = app  # type: ignore[assignment]
    yield app, mock_lcd
    TrccApp.reset()
