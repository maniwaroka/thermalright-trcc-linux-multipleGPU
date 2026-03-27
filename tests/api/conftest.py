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

    DI chain: os_bus.dispatch(DiscoverDevicesCommand)
              → scan() → _wire_bus() → has_lcd=True, lcd_device wired
                                      → has_led=False (no LED found)

    Uses build_lcd_bus(mock_lcd) so command dispatch actually routes
    through the real LCDCommandHandler to mock_lcd methods — commands
    return proper CommandResult objects, not bare MagicMocks.

    DataManager.ensure_all is replaced with a spy (records (w, h) calls,
    no network I/O). Access via: app.ensure_all_calls.
    """
    from trcc.adapters.infra.data_repository import DataManager
    from trcc.core.app import TrccApp
    from trcc.core.command_bus import CommandBus, CommandResult
    from trcc.core.handlers.lcd import build_lcd_bus

    ensure_all_calls: list = []
    monkeypatch.setattr(
        DataManager, "ensure_all",
        classmethod(lambda cls, w, h: ensure_all_calls.append((w, h))),
    )

    TrccApp.reset()
    app = TrccApp(MagicMock())

    mock_lcd = MagicMock()
    # restore_last_theme is called via RestoreLastThemeCommand through the bus
    mock_lcd.restore_last_theme.return_value = {"success": True, "image": None}

    # Fake os_bus: DiscoverDevicesCommand wires lcd_device + real lcd_bus
    def _fake_dispatch(cmd):
        app._lcd_device = mock_lcd
        app._lcd_bus = build_lcd_bus(mock_lcd)
        return CommandResult.ok(message="1 device(s) found")

    fake_os_bus = MagicMock(spec=CommandBus)
    fake_os_bus.dispatch.side_effect = _fake_dispatch
    app._os_bus = fake_os_bus

    TrccApp._instance = app  # type: ignore[assignment]
    app.ensure_all_calls = ensure_all_calls  # type: ignore[attr-defined]
    yield app, mock_lcd
    TrccApp.reset()


@pytest.fixture
def no_device_app(monkeypatch):
    """Fake TrccApp: no device found (scan returns nothing).

    DI chain: os_bus.dispatch(DiscoverDevicesCommand)
              → scan() → no devices → has_lcd=False, has_led=False
              → _lcd_bus stays None

    lcd_from_service() returns a mock so the fallback path doesn't crash.
    DataManager.ensure_all is a no-op (no network calls in tests).
    """
    from trcc.adapters.infra.data_repository import DataManager
    from trcc.core.app import TrccApp
    from trcc.core.command_bus import CommandBus, CommandResult

    monkeypatch.setattr(DataManager, "ensure_all", classmethod(lambda cls, *a, **kw: None))

    TrccApp.reset()
    app = TrccApp(MagicMock())

    mock_lcd = MagicMock()
    app.lcd_from_service = lambda svc: mock_lcd  # type: ignore[method-assign]

    # Fake os_bus: DiscoverDevicesCommand finds nothing — buses stay None
    fake_os_bus = MagicMock(spec=CommandBus)
    fake_os_bus.dispatch.return_value = CommandResult.ok(message="0 device(s) found")
    app._os_bus = fake_os_bus

    TrccApp._instance = app  # type: ignore[assignment]
    yield app, mock_lcd
    TrccApp.reset()
