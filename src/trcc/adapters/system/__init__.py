"""System integration adapters — sensors, info, config, and the Platform
factory that picks the right concrete adapter from the environment."""
from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trcc.core.ports import Platform


# Lazy import map — module/class is imported only when the matching OS runs.
_OS_PLATFORMS: dict[str, tuple[str, str]] = {
    'win32':  ('trcc.adapters.system.windows_platform', 'WindowsPlatform'),
    'darwin': ('trcc.adapters.system.macos_platform',   'MacOSPlatform'),
    'linux':  ('trcc.adapters.system.linux_platform',   'LinuxPlatform'),
    'bsd':    ('trcc.adapters.system.bsd_platform',     'BSDPlatform'),
}


def make_platform() -> Platform:
    """Build the Platform for the current process.

    Single chokepoint for OS detection. Every composition root (production
    `__main__`, `dev/mock_*`, tests) uses this; everything else receives
    the resulting Platform via DI.

    Honors ``TRCC_MOCK`` for dev/test invocations:
        TRCC_MOCK=1               → MockPlatform with default device specs
        TRCC_MOCK=path/to/devs.json → MockPlatform with specs from file

    Otherwise dispatches on ``sys.platform`` via the lazy import table —
    Windows code never touches Linux modules and vice versa.
    """
    if mock_spec := os.environ.get('TRCC_MOCK'):
        return _make_mock_platform(mock_spec)

    key = 'bsd' if 'bsd' in sys.platform else sys.platform
    module, cls_name = _OS_PLATFORMS.get(key, _OS_PLATFORMS['linux'])

    from importlib import import_module
    return getattr(import_module(module), cls_name)()


def _make_mock_platform(spec: str) -> Platform:
    """Build MockPlatform from a TRCC_MOCK spec ('1', or a JSON path).

    MockPlatform lives in `tests/mock_platform.py`. Imported as
    `tests.mock_platform` (not `mock_platform`) so every caller — pytest
    (which already has tests/ as a package), dev scripts, and production
    `python -m trcc` — resolves the SAME module object. Critical for
    `isinstance(p, MockPlatform)` checks to work across import contexts.
    """
    import json
    from pathlib import Path

    _ensure_repo_root_on_path()
    from tests.mock_platform import (  # type: ignore[import-not-found]
        DEFAULT_DEVICES,
        MockPlatform,
    )

    if spec.strip() in ('1', 'true', 'yes'):
        return MockPlatform(list(DEFAULT_DEVICES))

    specs_path = Path(spec)
    if not specs_path.is_file():
        raise RuntimeError(
            f"TRCC_MOCK={spec!r} is neither '1' nor a readable specs file"
        )
    return MockPlatform(json.loads(specs_path.read_text()))


def _ensure_repo_root_on_path() -> None:
    """Add repo root to sys.path so `tests.mock_platform` imports as a package."""
    from pathlib import Path
    here = Path(__file__).resolve()
    # Walk up: adapters/system → adapters → trcc → src → repo root (has tests/)
    for parent in here.parents:
        if (parent / 'tests' / 'mock_platform.py').is_file():
            root = str(parent)
            if root not in sys.path:
                sys.path.insert(0, root)
            return


__all__ = ['make_platform']
