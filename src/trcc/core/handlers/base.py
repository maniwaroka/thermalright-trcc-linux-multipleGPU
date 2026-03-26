"""Base class for device command handlers.

Provides shared behaviour used by both LCD and LED handlers:
  - _validate_metrics(): single validation point for both device types
  - __init_subclass__(): subclasses declare `handles` tuple, self-documenting
  - __call__(): contract — subclasses must implement

Design notes:
  - Plain base class (not ABC, not Protocol) — provides shared behaviour only
  - Structural contract is HandlerFn = Callable[[Command], CommandResult]
  - __slots__ = () on base so subclass slots work without __dict__ overhead
"""
from __future__ import annotations

from typing import Any, ClassVar

from ..command_bus import Command, CommandResult


class DeviceCommandHandler:
    """Shared base for LCD and LED command handlers.

    Subclasses declare a `handles` class variable listing every command type
    they dispatch. build_*_bus() uses this tuple to auto-register the handler
    — adding a new command only requires updating `handles` and the match arm.
    """

    __slots__ = ()

    handles: ClassVar[tuple[type[Command], ...]] = ()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # Subclasses without `handles` are fine (e.g. LEDGuiCommandHandler
        # intentionally handles only a subset and doesn't use auto-registration).

    def _validate_metrics(self, metrics: Any) -> bool:
        """Return True if metrics data is safe to pass to a device.

        Both LCD and LED handlers call this before dispatching UpdateMetrics*
        commands — one validation point, symmetric contract.
        """
        return metrics is not None

    def __call__(self, cmd: Command) -> CommandResult:
        raise NotImplementedError(
            f"{type(self).__name__} must implement __call__"
        )

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"
