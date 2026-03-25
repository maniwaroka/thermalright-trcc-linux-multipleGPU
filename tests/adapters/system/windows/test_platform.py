"""Contract tests for WindowsPlatform — verifies it fulfils PlatformAdapter.

Runs on Linux. Each OS-specific concrete class is mocked at its import
path so the lazy `from X import Y` inside each factory method resolves
to a controllable spec-mock that inherits from the correct port ABC.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from trcc.adapters.system.windows.platform import WindowsPlatform
from trcc.core.ports import (
    AutostartManager,
    PlatformAdapter,
    PlatformSetup,
    SensorEnumerator,
)

_MOD = 'trcc.adapters.system.windows.platform'


class TestWindowsPlatformIsAdapter:
    def test_is_platform_adapter(self):
        assert isinstance(WindowsPlatform(), PlatformAdapter)


class TestWindowsPlatformContract:
    """Each factory method calls the right constructor and returns its result."""

    def setup_method(self):
        self._p = WindowsPlatform()

    def test_create_detect_fn_returns_callable(self):
        mock_detector = MagicMock()
        mock_detector.detect = MagicMock(return_value=[])
        with patch(
            'trcc.adapters.device.windows.detector.WindowsDeviceDetector',
            mock_detector,
        ):
            fn = self._p.create_detect_fn()
        assert callable(fn)

    def test_create_sensor_enumerator_returns_sensor_enumerator(self):
        mock_instance = MagicMock(spec=SensorEnumerator)
        with patch(
            'trcc.adapters.system.windows.sensors.WindowsSensorEnumerator',
            return_value=mock_instance,
        ):
            result = self._p.create_sensor_enumerator()
        assert result is mock_instance

    def test_create_autostart_manager_returns_autostart_manager(self):
        mock_instance = MagicMock(spec=AutostartManager)
        with patch(
            'trcc.adapters.system.windows.autostart.WindowsAutostartManager',
            return_value=mock_instance,
        ):
            result = self._p.create_autostart_manager()
        assert result is mock_instance

    def test_create_setup_returns_platform_setup(self):
        mock_instance = MagicMock(spec=PlatformSetup)
        with patch(
            'trcc.adapters.system.windows.setup.WindowsSetup',
            return_value=mock_instance,
        ):
            result = self._p.create_setup()
        assert result is mock_instance

    def test_get_memory_info_fn_returns_callable(self):
        mock_fn = MagicMock(return_value=[])
        with patch('trcc.adapters.system.windows.hardware.get_memory_info', mock_fn):
            fn = self._p.get_memory_info_fn()
        assert callable(fn)

    def test_get_disk_info_fn_returns_callable(self):
        mock_fn = MagicMock(return_value=[])
        with patch('trcc.adapters.system.windows.hardware.get_disk_info', mock_fn):
            fn = self._p.get_disk_info_fn()
        assert callable(fn)

    def test_configure_scsi_protocol_wires_windows_scsi(self):
        """Windows must call factory.configure_scsi with a WindowsScsiProtocol factory."""
        import sys
        factory = MagicMock()
        mock_scsi_module = MagicMock()
        sys.modules['trcc.adapters.device.windows.scsi_protocol'] = mock_scsi_module
        try:
            self._p.configure_scsi_protocol(factory)
        finally:
            sys.modules.pop('trcc.adapters.device.windows.scsi_protocol', None)

        factory.configure_scsi.assert_called_once()
        scsi_factory_fn = factory.configure_scsi.call_args[0][0]
        assert callable(scsi_factory_fn)

    def test_configure_scsi_protocol_passes_path_vid_pid(self):
        """The SCSI factory lambda extracts path/vid/pid from DeviceInfo."""
        import sys
        factory = MagicMock()
        mock_protocol_cls = MagicMock()
        mock_scsi_module = MagicMock()
        mock_scsi_module.WindowsScsiProtocol = mock_protocol_cls
        sys.modules['trcc.adapters.device.windows.scsi_protocol'] = mock_scsi_module
        try:
            self._p.configure_scsi_protocol(factory)
        finally:
            sys.modules.pop('trcc.adapters.device.windows.scsi_protocol', None)

        scsi_factory_fn = factory.configure_scsi.call_args[0][0]
        device_info = MagicMock()
        device_info.path = '\\\\.\\PhysicalDrive2'
        device_info.vid = 0x0402
        device_info.pid = 0x3922
        scsi_factory_fn(device_info)

        mock_protocol_cls.assert_called_once_with(
            device_info.path, vid=device_info.vid, pid=device_info.pid
        )
