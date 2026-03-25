"""Contract tests for LinuxPlatform — verifies it fulfils PlatformAdapter.

All methods are exercised on Linux directly (no mocking needed — the
concrete Linux adapters are available in the test environment).
"""
from __future__ import annotations

from unittest.mock import MagicMock

from trcc.adapters.system.linux.platform import LinuxPlatform
from trcc.core.ports import (
    AutostartManager,
    PlatformAdapter,
    PlatformSetup,
    SensorEnumerator,
)


class TestLinuxPlatformIsAdapter:
    def test_is_platform_adapter(self):
        assert isinstance(LinuxPlatform(), PlatformAdapter)


class TestLinuxPlatformContract:
    """Each factory method returns the correct port type."""

    def setup_method(self):
        self._p = LinuxPlatform()

    def test_create_detect_fn_returns_callable(self):
        fn = self._p.create_detect_fn()
        assert callable(fn)

    def test_create_sensor_enumerator_returns_sensor_enumerator(self):
        result = self._p.create_sensor_enumerator()
        assert isinstance(result, SensorEnumerator)

    def test_create_autostart_manager_returns_autostart_manager(self):
        result = self._p.create_autostart_manager()
        assert isinstance(result, AutostartManager)

    def test_create_setup_returns_platform_setup(self):
        result = self._p.create_setup()
        assert isinstance(result, PlatformSetup)

    def test_get_memory_info_fn_returns_callable(self):
        fn = self._p.get_memory_info_fn()
        assert callable(fn)

    def test_get_disk_info_fn_returns_callable(self):
        fn = self._p.get_disk_info_fn()
        assert callable(fn)

    def test_configure_scsi_protocol_is_noop(self):
        """Linux uses default SCSI — factory.configure_scsi must NOT be called."""
        factory = MagicMock()
        self._p.configure_scsi_protocol(factory)
        factory.configure_scsi.assert_not_called()
