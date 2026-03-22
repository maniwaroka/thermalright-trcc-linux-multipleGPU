"""TRCC logging configurator — single source of truth for log format.

Composition roots (CLI, GUI, API) instantiate TrccLoggingConfigurator
and call configure(verbosity) instead of touching logging.Formatter or
handler lists directly.

Format includes %(funcName)s so log lines self-locate after refactors.
"""
from __future__ import annotations

import logging
import logging.handlers
from abc import ABC, abstractmethod
from pathlib import Path

_DEFAULT_LOG_FILE = Path.home() / '.trcc' / 'trcc.log'


class TrccLoggingConfigurator(ABC):
    """Port: TRCC application logging configuration.

    Named TrccLoggingConfigurator to avoid confusion with Python's
    standard logging.config.BaseConfigurator.
    """

    @abstractmethod
    def configure(self, verbosity: int = 0) -> None:
        """Configure root logger with file + console handlers.

        Args:
            verbosity: 0 = WARNING on console, 1 = INFO, 2+ = DEBUG.
                       File handler is always DEBUG regardless.
        """


class StandardLoggingConfigurator(TrccLoggingConfigurator):
    """Configures file + console logging with a consistent format.

    The format string is defined once here. Both handlers use it.
    %(funcName)s is included so every log line names its calling method —
    refactoring a class or function automatically updates the log output.
    """

    FORMAT = '%(asctime)s [%(levelname)s] %(name)s.%(funcName)s: %(message)s'
    DATE_FMT = '%Y-%m-%d %H:%M:%S'
    DATE_FMT_CONSOLE = '%H:%M:%S'

    def __init__(self, log_file: Path = _DEFAULT_LOG_FILE) -> None:
        self._log_file = log_file

    def configure(self, verbosity: int = 0) -> None:
        """Replace all root logger handlers with file + console.

        Clears handlers set by the early __main__.py bootstrap so there
        are no duplicates. The bootstrap file handler stays until this
        is called, ensuring no log lines are lost during import.
        """
        root = logging.getLogger()
        root.handlers.clear()
        root.setLevel(logging.DEBUG)

        # File handler — always DEBUG, 1 MB × 3 backups
        self._log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            self._log_file, maxBytes=1_000_000, backupCount=3,
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(self.FORMAT, datefmt=self.DATE_FMT))
        root.addHandler(fh)

        # Console handler — level controlled by verbosity
        console_level = (
            logging.DEBUG if verbosity >= 2
            else logging.INFO if verbosity == 1
            else logging.WARNING
        )
        ch = logging.StreamHandler()
        ch.setLevel(console_level)
        ch.setFormatter(logging.Formatter(self.FORMAT, datefmt=self.DATE_FMT_CONSOLE))
        root.addHandler(ch)
