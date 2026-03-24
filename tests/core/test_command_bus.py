"""Unit tests for core/command_bus.py — Phase 1 infrastructure."""
from __future__ import annotations

import time
from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock, patch

import pytest

from trcc.core.command_bus import (
    Command,
    CommandBus,
    CommandResult,
    LCDCommand,
    LEDCommand,
    LoggingMiddleware,
    Middleware,
    RateLimitMiddleware,
    TimingMiddleware,
)
from trcc.core.commands import (
    SendImageCommand,
    SetBrightnessCommand,
    SetLEDColorCommand,
    SetLEDModeCommand,
)
from trcc.core.models import LEDMode

# ── Helpers ─────────────────────────────────────────────────────────────────

def _ok_handler(cmd: Command) -> CommandResult:  # noqa: ARG001
    return CommandResult.ok(message="ok")


def _fail_handler(cmd: Command) -> CommandResult:  # noqa: ARG001
    return CommandResult.fail("something went wrong")


# ── CommandResult ────────────────────────────────────────────────────────────

class TestCommandResult:
    def test_from_dict_success(self):
        r = CommandResult.from_dict({"success": True, "message": "done"})
        assert r.success is True
        assert r.payload["message"] == "done"

    def test_from_dict_failure(self):
        r = CommandResult.from_dict({"success": False, "error": "oops"})
        assert r.success is False

    def test_ok_factory(self):
        r = CommandResult.ok(value=42)
        assert r.success is True
        assert r.payload["value"] == 42

    def test_fail_factory(self):
        r = CommandResult.fail("bad input")
        assert r.success is False
        assert r.payload["error"] == "bad input"


# ── Command dataclasses ──────────────────────────────────────────────────────

class TestCommandDataclasses:
    def test_lcd_command_is_frozen(self):
        cmd = SetBrightnessCommand(level=2)
        with pytest.raises(FrozenInstanceError):
            cmd.level = 3  # type: ignore[misc]

    def test_led_command_is_frozen(self):
        cmd = SetLEDColorCommand(r=255, g=0, b=0)
        with pytest.raises(FrozenInstanceError):
            cmd.r = 0  # type: ignore[misc]

    def test_lcd_command_isinstance(self):
        assert isinstance(SetBrightnessCommand(), LCDCommand)
        assert isinstance(SetBrightnessCommand(), Command)

    def test_led_command_isinstance(self):
        assert isinstance(SetLEDColorCommand(), LEDCommand)
        assert isinstance(SetLEDColorCommand(), Command)

    def test_led_mode_default(self):
        cmd = SetLEDModeCommand()
        assert cmd.mode == LEDMode.STATIC

    def test_send_image_default(self):
        cmd = SendImageCommand()
        assert cmd.image_path == ""

    def test_commands_equal_by_value(self):
        assert SetBrightnessCommand(level=2) == SetBrightnessCommand(level=2)
        assert SetBrightnessCommand(level=1) != SetBrightnessCommand(level=2)


# ── CommandBus dispatch ──────────────────────────────────────────────────────

class TestCommandBusDispatch:
    def test_dispatch_calls_registered_handler(self):
        bus = CommandBus()
        handler = MagicMock(return_value=CommandResult.ok())
        bus.register(SetBrightnessCommand, handler)
        cmd = SetBrightnessCommand(level=2)
        result = bus.dispatch(cmd)
        handler.assert_called_once_with(cmd)
        assert result.success is True

    def test_dispatch_unknown_command_raises(self):
        bus = CommandBus()
        with pytest.raises(KeyError, match="SetBrightnessCommand"):
            bus.dispatch(SetBrightnessCommand(level=1))

    def test_dispatch_returns_handler_result(self):
        bus = CommandBus()
        bus.register(SetBrightnessCommand, _fail_handler)
        result = bus.dispatch(SetBrightnessCommand())
        assert result.success is False
        assert result.payload["error"] == "something went wrong"

    def test_fluent_registration(self):
        bus = CommandBus()
        returned = bus.register(SetBrightnessCommand, _ok_handler)
        assert returned is bus

    def test_fluent_add_middleware(self):
        bus = CommandBus()
        returned = bus.add_middleware(LoggingMiddleware())
        assert returned is bus

    def test_register_overwrites_previous_handler(self):
        bus = CommandBus()
        bus.register(SetBrightnessCommand, _fail_handler)
        bus.register(SetBrightnessCommand, _ok_handler)
        assert bus.dispatch(SetBrightnessCommand()).success is True


# ── Middleware chain ─────────────────────────────────────────────────────────

class TestMiddlewareChain:
    def test_single_middleware_wraps_handler(self):
        order: list[str] = []

        class TrackMiddleware(Middleware):
            def handle(self, command, next_fn):
                order.append("before")
                result = next_fn(command)
                order.append("after")
                return result

        bus = CommandBus()
        bus.add_middleware(TrackMiddleware())
        bus.register(SetBrightnessCommand, lambda c: (order.append("handler"), CommandResult.ok())[1])
        bus.dispatch(SetBrightnessCommand())
        assert order == ["before", "handler", "after"]

    def test_middleware_executes_fifo_outer_to_inner(self):
        """First middleware added = outermost wrapper."""
        order: list[str] = []

        def make_mw(name: str) -> Middleware:
            class M(Middleware):
                def handle(self, command, next_fn):
                    order.append(f"{name}:before")
                    result = next_fn(command)
                    order.append(f"{name}:after")
                    return result
            return M()

        bus = CommandBus()
        bus.add_middleware(make_mw("A"))
        bus.add_middleware(make_mw("B"))
        bus.register(SetBrightnessCommand, _ok_handler)
        bus.dispatch(SetBrightnessCommand())
        assert order == ["A:before", "B:before", "B:after", "A:after"]

    def test_middleware_can_short_circuit(self):
        class BlockMiddleware(Middleware):
            def handle(self, command, next_fn):
                return CommandResult.fail("blocked")

        handler = MagicMock(return_value=CommandResult.ok())
        bus = CommandBus()
        bus.add_middleware(BlockMiddleware())
        bus.register(SetBrightnessCommand, handler)
        result = bus.dispatch(SetBrightnessCommand())
        assert result.success is False
        handler.assert_not_called()


# ── LoggingMiddleware ────────────────────────────────────────────────────────

class TestLoggingMiddleware:
    def test_logs_dispatch_at_debug(self):
        bus = CommandBus()
        bus.add_middleware(LoggingMiddleware())
        bus.register(SetBrightnessCommand, _ok_handler)
        with patch("trcc.core.command_bus.log") as mock_log:
            bus.dispatch(SetBrightnessCommand(level=1))
        mock_log.debug.assert_called_once_with("dispatch %s", "SetBrightnessCommand")

    def test_logs_failure_at_warning(self):
        bus = CommandBus()
        bus.add_middleware(LoggingMiddleware())
        bus.register(SetBrightnessCommand, _fail_handler)
        with patch("trcc.core.command_bus.log") as mock_log:
            bus.dispatch(SetBrightnessCommand())
        mock_log.warning.assert_called_once()

    def test_does_not_log_warning_on_success(self):
        bus = CommandBus()
        bus.add_middleware(LoggingMiddleware())
        bus.register(SetBrightnessCommand, _ok_handler)
        with patch("trcc.core.command_bus.log") as mock_log:
            bus.dispatch(SetBrightnessCommand())
        mock_log.warning.assert_not_called()


# ── TimingMiddleware ─────────────────────────────────────────────────────────

class TestTimingMiddleware:
    def test_logs_warning_for_slow_command(self):
        def slow_handler(cmd: Command) -> CommandResult:
            time.sleep(0.02)
            return CommandResult.ok()

        bus = CommandBus()
        bus.add_middleware(TimingMiddleware(threshold_ms=5.0))
        bus.register(SetBrightnessCommand, slow_handler)
        with patch("trcc.core.command_bus.log") as mock_log:
            bus.dispatch(SetBrightnessCommand())
        mock_log.warning.assert_called_once()
        assert "SetBrightnessCommand" in mock_log.warning.call_args[0][1]

    def test_no_warning_for_fast_command(self):
        bus = CommandBus()
        bus.add_middleware(TimingMiddleware(threshold_ms=10_000.0))
        bus.register(SetBrightnessCommand, _ok_handler)
        with patch("trcc.core.command_bus.log") as mock_log:
            bus.dispatch(SetBrightnessCommand())
        mock_log.warning.assert_not_called()


# ── RateLimitMiddleware ──────────────────────────────────────────────────────

class TestRateLimitMiddleware:
    def test_allows_first_dispatch(self):
        bus = CommandBus()
        bus.add_middleware(RateLimitMiddleware(min_interval_ms=100.0))
        bus.register(SetBrightnessCommand, _ok_handler)
        result = bus.dispatch(SetBrightnessCommand())
        assert result.success is True
        assert result.payload.get("skipped") is not True

    def test_skips_rapid_second_dispatch(self):
        bus = CommandBus()
        bus.add_middleware(RateLimitMiddleware(min_interval_ms=10_000.0))
        bus.register(SetBrightnessCommand, _ok_handler)
        bus.dispatch(SetBrightnessCommand())
        result = bus.dispatch(SetBrightnessCommand())
        assert result.payload.get("skipped") is True

    def test_allows_after_interval(self):
        bus = CommandBus()
        bus.add_middleware(RateLimitMiddleware(min_interval_ms=10.0))
        bus.register(SetBrightnessCommand, _ok_handler)
        bus.dispatch(SetBrightnessCommand())
        time.sleep(0.02)
        result = bus.dispatch(SetBrightnessCommand())
        assert result.payload.get("skipped") is not True

    def test_rate_limits_per_command_type(self):
        """Different command types have independent rate limit counters."""
        bus = CommandBus()
        bus.add_middleware(RateLimitMiddleware(min_interval_ms=10_000.0))
        bus.register(SetBrightnessCommand, _ok_handler)
        bus.register(SendImageCommand, _ok_handler)
        bus.dispatch(SetBrightnessCommand())
        # Different type — should not be rate-limited
        result = bus.dispatch(SendImageCommand())
        assert result.payload.get("skipped") is not True

    def test_skipped_result_is_success(self):
        """Skipped commands are not errors — callers need not special-case them."""
        bus = CommandBus()
        bus.add_middleware(RateLimitMiddleware(min_interval_ms=10_000.0))
        bus.register(SetBrightnessCommand, _ok_handler)
        bus.dispatch(SetBrightnessCommand())
        result = bus.dispatch(SetBrightnessCommand())
        assert result.success is True

    def test_handler_not_called_when_skipped(self):
        handler = MagicMock(return_value=CommandResult.ok())
        bus = CommandBus()
        bus.add_middleware(RateLimitMiddleware(min_interval_ms=10_000.0))
        bus.register(SetBrightnessCommand, handler)
        bus.dispatch(SetBrightnessCommand())
        bus.dispatch(SetBrightnessCommand())
        assert handler.call_count == 1
