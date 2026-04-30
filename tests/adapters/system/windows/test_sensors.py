"""Tests for Windows sensor enumerator — platform-specific behavior only.

Shared base behavior (psutil, nvidia, computed I/O, polling, read_all)
is tested in tests/adapters/system/conftest.py.

Tests follow the app flow: discover() → read_all() → map_defaults().
Mock at I/O boundary only: LHM Computer COM object, WMI, psutil temps.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

MODULE = 'trcc.adapters.system.windows_platform'


# ── LHM mock helpers ─────────────────────────────────────────────────


def _mock_lhm_sensor(name: str, sensor_type: str, value: float | None) -> MagicMock:
    """Create a mock LHM sensor."""
    s = MagicMock()
    s.Name = name
    s.SensorType = sensor_type
    s.Value = value
    return s


def _mock_lhm_hardware(
    name: str, hw_type: str,
    sensors: list[MagicMock],
    sub_hardware: list[MagicMock] | None = None,
) -> MagicMock:
    hw = MagicMock()
    hw.Name = name
    hw.HardwareType = hw_type
    hw.Sensors = sensors
    hw.SubHardware = sub_hardware or []
    return hw


def _make_lhm_computer(hardware: list[MagicMock]) -> MagicMock:
    """Create a mock LHM Computer with given hardware."""
    computer = MagicMock()
    computer.Hardware = hardware
    return computer


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def mock_win_no_lhm(mock_io_no_nvidia):
    """Windows enumerator — no LHM, no nvidia. Psutil only."""
    with patch(f'{MODULE}.LHM_AVAILABLE', False), \
         patch(f'{MODULE}.psutil') as win_psutil:
        # Windows psutil for sensors_temperatures in _poll_platform
        win_psutil.sensors_temperatures.return_value = {}
        mock_io_no_nvidia.win_psutil = win_psutil
        yield mock_io_no_nvidia


@pytest.fixture
def mock_win_lhm(mock_io_no_nvidia):
    """Windows enumerator with mocked LHM GPU + CPU."""
    gpu_sensors = [
        _mock_lhm_sensor('GPU Core', 'Temperature', 72.0),
        _mock_lhm_sensor('GPU Core Load', 'Load', 95.0),
        _mock_lhm_sensor('GPU Core Clock', 'Clock', 1950.0),
        _mock_lhm_sensor('GPU Package Power', 'Power', 310.0),
        _mock_lhm_sensor('GPU Fan', 'Fan', 1800.0),
    ]
    cpu_sensors = [
        _mock_lhm_sensor('CPU Package', 'Temperature', 65.0),
        _mock_lhm_sensor('CPU Package Power', 'Power', 125.0),
    ]
    gpu_hw = _mock_lhm_hardware('NVIDIA RTX 4090', 'GpuNvidia', gpu_sensors)
    cpu_hw = _mock_lhm_hardware('Intel Core i9', 'Cpu', cpu_sensors)
    computer = _make_lhm_computer([gpu_hw, cpu_hw])

    with patch(f'{MODULE}.LHM_AVAILABLE', True), \
         patch(f'{MODULE}.Computer', return_value=computer, create=True), \
         patch(f'{MODULE}.psutil') as win_psutil:
        win_psutil.sensors_temperatures.return_value = {}
        mock_io_no_nvidia.win_psutil = win_psutil
        mock_io_no_nvidia.lhm_computer = computer
        yield mock_io_no_nvidia


@pytest.fixture
def mock_win_nvidia(mock_io):
    """Windows enumerator — no LHM, nvidia fallback."""
    mock_io.setup_nvidia(temp=68, usage=80, clock=1800, power_mw=250000, fan=55)
    with patch(f'{MODULE}.LHM_AVAILABLE', False), \
         patch(f'{MODULE}.psutil') as win_psutil:
        win_psutil.sensors_temperatures.return_value = {}
        mock_io.win_psutil = win_psutil
        yield mock_io


def _make_enum():
    from trcc.adapters.system.windows_platform import SensorEnumerator
    return SensorEnumerator()


@pytest.fixture
def enum_no_lhm(mock_win_no_lhm):
    e = _make_enum()
    e.discover()
    return e


@pytest.fixture
def enum_lhm(mock_win_lhm):
    e = _make_enum()
    e.discover()
    return e


@pytest.fixture
def enum_nvidia(mock_win_nvidia):
    e = _make_enum()
    e.discover()
    return e


# ── Discovery ────────────────────────────────────────────────────────


class TestDiscover:

    def test_psutil_only_when_no_lhm_no_nvidia(self, enum_no_lhm):
        sources = {s.source for s in enum_no_lhm.get_sensors()}
        assert 'psutil' in sources
        assert 'computed' in sources
        assert 'lhm' not in sources
        assert 'nvidia' not in sources

    def test_lhm_gpu_sensors_registered(self, enum_lhm):
        ids = [s.id for s in enum_lhm.get_sensors()]
        assert any('gpu_core' in sid and 'lhm:' in sid for sid in ids)
        assert any('cpu_package' in sid and 'lhm:' in sid for sid in ids)

    def test_lhm_gpu_skips_nvidia_discovery(self, enum_lhm):
        """When LHM finds GPU, pynvml discovery is skipped."""
        assert not any(s.source == 'nvidia' for s in enum_lhm.get_sensors())

    def test_nvidia_fallback_when_no_lhm(self, enum_nvidia):
        ids = [s.id for s in enum_nvidia.get_sensors()]
        assert 'nvidia:0:temp' in ids
        assert 'nvidia:0:gpu_busy' in ids

    def test_psutil_cpu_temps_registered(self, mock_io_no_nvidia):
        with patch(f'{MODULE}.LHM_AVAILABLE', False), \
             patch(f'{MODULE}.psutil') as win_psutil:
            win_psutil.sensors_temperatures.return_value = {
                'coretemp': [MagicMock(label='Package', current=65.0)],
            }
            e = _make_enum()
            e.discover()
            assert any(s.id == 'psutil:temp:coretemp:0' for s in e.get_sensors())

    def test_wmi_noop_without_package(self, enum_no_lhm):
        """WMI not installed on test system — no wmi sensors."""
        assert not any(s.source == 'wmi' for s in enum_no_lhm.get_sensors())

    def test_lhm_subhardware_discovered(self, mock_io_no_nvidia):
        core_sensor = _mock_lhm_sensor('Core #0', 'Temperature', 60.0)
        sub = _mock_lhm_hardware('CPU Core', 'Cpu', [core_sensor])
        cpu_hw = _mock_lhm_hardware('Intel CPU', 'Cpu', [], sub_hardware=[sub])
        computer = _make_lhm_computer([cpu_hw])

        with patch(f'{MODULE}.LHM_AVAILABLE', True), \
             patch(f'{MODULE}.Computer', return_value=computer, create=True), \
             patch(f'{MODULE}.psutil') as wp:
            wp.sensors_temperatures.return_value = {}
            e = _make_enum()
            e.discover()
            assert any('cpu_core' in s.id for s in e.get_sensors())


# ── Reading ──────────────────────────────────────────────────────────


class TestReadAll:

    def test_lhm_readings(self, enum_lhm):
        readings = enum_lhm.read_all()
        # LHM GPU temp
        lhm_keys = [k for k in readings if k.startswith('lhm:')]
        assert len(lhm_keys) > 0
        gpu_temp_key = [k for k in lhm_keys if 'gpu_core' in k and 'load' not in k]
        assert gpu_temp_key
        assert readings[gpu_temp_key[0]] == 72.0

    def test_nvidia_fallback_readings(self, enum_nvidia):
        readings = enum_nvidia.read_all()
        assert readings['nvidia:0:temp'] == 68.0
        assert readings['nvidia:0:gpu_busy'] == 80.0

    def test_psutil_base_readings(self, enum_no_lhm):
        readings = enum_no_lhm.read_all()
        assert 'psutil:cpu_percent' in readings
        assert 'psutil:mem_percent' in readings

    def test_lhm_none_values_skipped(self, mock_io_no_nvidia):
        dead_sensor = _mock_lhm_sensor('Dead', 'Temperature', None)
        dead_sensor.Value = None
        hw = _mock_lhm_hardware('Board', 'Motherboard', [dead_sensor])
        computer = _make_lhm_computer([hw])

        with patch(f'{MODULE}.LHM_AVAILABLE', True), \
             patch(f'{MODULE}.Computer', return_value=computer, create=True), \
             patch(f'{MODULE}.psutil') as wp:
            wp.sensors_temperatures.return_value = {}
            e = _make_enum()
            e.discover()
            readings = e.read_all()
            assert not any(k.startswith('lhm:board:dead') for k in readings)

    def test_lhm_poll_exception_isolated(self, mock_io_no_nvidia):
        """LHM COM failure doesn't crash the enumerator."""
        hw = MagicMock()
        hw.Name = 'Broken'
        hw.HardwareType = 'Cpu'
        hw.Sensors = []
        hw.SubHardware = []
        computer = _make_lhm_computer([hw])
        # Make Update() crash on poll
        hw.Update.side_effect = RuntimeError("COM disconnected")

        with patch(f'{MODULE}.LHM_AVAILABLE', True), \
             patch(f'{MODULE}.Computer', return_value=computer, create=True), \
             patch(f'{MODULE}.psutil') as wp:
            wp.sensors_temperatures.return_value = {}
            e = _make_enum()
            e.discover()
            # Should not raise — LHM failure is caught
            readings = e.read_all()
            assert 'psutil:cpu_percent' in readings


# ── Mapping ──────────────────────────────────────────────────────────


class TestMapDefaults:

    def test_lhm_gpu_mapping(self, enum_lhm):
        mapping = enum_lhm.map_defaults()
        assert 'gpu_temp' in mapping
        assert mapping['gpu_temp'].startswith('lhm:')

    def test_nvidia_fallback_gpu_mapping(self, enum_nvidia):
        mapping = enum_nvidia.map_defaults()
        assert mapping['gpu_temp'] == 'nvidia:0:temp'
        assert mapping['gpu_usage'] == 'nvidia:0:gpu_busy'

    def test_no_gpu_mapping_without_any(self, enum_no_lhm):
        mapping = enum_no_lhm.map_defaults()
        assert 'gpu_temp' not in mapping

    def test_common_mappings(self, enum_no_lhm):
        mapping = enum_no_lhm.map_defaults()
        assert mapping['cpu_percent'] == 'psutil:cpu_percent'
        assert mapping['mem_available'] == 'psutil:mem_available'
        assert mapping['disk_read'] == 'computed:disk_read'
        assert mapping['net_total_up'] == 'computed:net_total_up'

    def test_lhm_cpu_temp_mapping(self, enum_lhm):
        mapping = enum_lhm.map_defaults()
        assert 'cpu_temp' in mapping
        assert mapping['cpu_temp'].startswith('lhm:')

    def test_lhm_cpu_power_mapping(self, enum_lhm):
        mapping = enum_lhm.map_defaults()
        assert 'cpu_power' in mapping
        assert 'package' in mapping['cpu_power']


# ── Polling lifecycle ────────────────────────────────────────────────


class TestPolling:

    def test_lhm_closed_on_stop(self, mock_win_lhm):
        e = _make_enum()
        e.discover()
        e.start_polling(interval=0.01)
        e.stop_polling()
        mock_win_lhm.lhm_computer.Close.assert_called_once()


# ── LHM type map ─────────────────────────────────────────────────────


class TestLhmTypeMap:

    def test_known_types_mapped(self):
        from trcc.adapters.system.windows_platform import _LHM_TYPE_MAP
        assert 'Temperature' in _LHM_TYPE_MAP
        assert 'Fan' in _LHM_TYPE_MAP
        assert 'Clock' in _LHM_TYPE_MAP
        assert 'Load' in _LHM_TYPE_MAP
        assert 'Power' in _LHM_TYPE_MAP

    def test_unknown_type_not_mapped(self):
        from trcc.adapters.system.windows_platform import _LHM_TYPE_MAP
        assert 'Warp' not in _LHM_TYPE_MAP


# ── WMI GPU fallback (issue #131) ────────────────────────────────────
#
# AMD-on-Windows users without LibreHardwareMonitor running used to see
# "No GPUs detected" because `pynvml` is NVIDIA-only. v9.6.0 adds a
# `Win32_VideoController` fallback that detects every GPU Windows knows
# about — temperature/usage data still requires LHM/ADLX, but at least
# the card appears in `trcc gpus` for selection.


def _mock_wmi_video(name: str = 'AMD Radeon RX 9070 XT') -> MagicMock:
    """Build a mock Win32_VideoController instance with the given name."""
    vc = MagicMock()
    vc.Name = name
    return vc


class TestWmiGpuFallback:
    """Verifies the WMI Win32_VideoController fallback path used when
    neither LHM nor pynvml returned any GPUs."""

    def _patch_wmi(self, controllers: list[MagicMock]):
        """Install a mock `wmi` module returning the given controllers."""
        mock_wmi_mod = MagicMock()
        mock_wmi_instance = MagicMock()
        mock_wmi_instance.Win32_VideoController.return_value = controllers
        mock_wmi_mod.WMI.return_value = mock_wmi_instance
        return patch.dict('sys.modules', {'wmi': mock_wmi_mod})

    def test_returns_amd_gpu(self):
        """The reporter's exact card (RX 9070 XT) appears in the list."""
        from trcc.adapters.system.windows_platform import SensorEnumerator
        with self._patch_wmi([_mock_wmi_video('AMD Radeon RX 9070 XT')]):
            result = SensorEnumerator._wmi_get_gpu_list()
        assert result == [('wmi:0', 'AMD Radeon RX 9070 XT')]

    def test_returns_multiple_gpus_in_order(self):
        """Integrated + discrete are both listed, indexed in detection order."""
        from trcc.adapters.system.windows_platform import SensorEnumerator
        controllers = [
            _mock_wmi_video('AMD Radeon Graphics'),     # integrated
            _mock_wmi_video('AMD Radeon RX 9070 XT'),   # discrete
        ]
        with self._patch_wmi(controllers):
            result = SensorEnumerator._wmi_get_gpu_list()
        assert result == [
            ('wmi:0', 'AMD Radeon Graphics'),
            ('wmi:1', 'AMD Radeon RX 9070 XT'),
        ]

    def test_strips_whitespace_in_name(self):
        from trcc.adapters.system.windows_platform import SensorEnumerator
        with self._patch_wmi([_mock_wmi_video('  AMD Radeon  ')]):
            result = SensorEnumerator._wmi_get_gpu_list()
        assert result == [('wmi:0', 'AMD Radeon')]

    def test_skips_controllers_with_no_name(self):
        """WMI sometimes returns ghost controllers (Name=None). Skip them."""
        from trcc.adapters.system.windows_platform import SensorEnumerator
        ghost = MagicMock()
        ghost.Name = None
        controllers = [ghost, _mock_wmi_video('Real GPU')]
        with self._patch_wmi(controllers):
            result = SensorEnumerator._wmi_get_gpu_list()
        assert result == [('wmi:1', 'Real GPU')]

    def test_returns_empty_when_wmi_pkg_missing(self):
        """No `wmi` package on path (e.g. running on Linux): empty list, no crash."""
        from trcc.adapters.system.windows_platform import SensorEnumerator
        with patch.dict('sys.modules', {'wmi': None}):
            # `import wmi` with sys.modules[wmi]=None raises ImportError
            result = SensorEnumerator._wmi_get_gpu_list()
        assert result == []

    def test_returns_empty_on_wmi_exception(self):
        """WMI subsystem can raise (e.g. COM init failure). Don't propagate."""
        from trcc.adapters.system.windows_platform import SensorEnumerator
        mock_wmi_mod = MagicMock()
        mock_wmi_mod.WMI.side_effect = RuntimeError('COM not initialised')
        with patch.dict('sys.modules', {'wmi': mock_wmi_mod}):
            result = SensorEnumerator._wmi_get_gpu_list()
        assert result == []


class TestGetGpuListFallbackOrder:
    """get_gpu_list() preference: LHM → pynvml (NVIDIA) → WMI (universal)."""

    def test_wmi_only_fires_when_lhm_and_pynvml_empty(self):
        """Reporter scenario: AMD GPU, no LHM, no NVIDIA. WMI fallback fires."""
        from trcc.adapters.system.windows_platform import SensorEnumerator
        e = SensorEnumerator()
        e._lhm_computer = None  # No LHM
        # Patch base get_gpu_list (pynvml NVIDIA path) to return empty
        with patch.object(
            SensorEnumerator.__bases__[0], 'get_gpu_list', return_value=[],
        ), patch.object(
            SensorEnumerator, '_wmi_get_gpu_list',
            return_value=[('wmi:0', 'AMD Radeon RX 9070 XT')],
        ):
            result = e.get_gpu_list()
        assert result == [('wmi:0', 'AMD Radeon RX 9070 XT')]

    def test_wmi_skipped_when_lhm_returns_gpus(self):
        """LHM is preferred when running — its results include sensor data."""
        from trcc.adapters.system.windows_platform import SensorEnumerator
        e = SensorEnumerator()
        # Mock LHM with one GPU
        lhm_gpu = MagicMock()
        lhm_gpu.HardwareType = 'GpuAmd'
        lhm_gpu.Name = 'AMD Radeon RX 9070 XT'
        e._lhm_computer = MagicMock()
        e._lhm_computer.Hardware = [lhm_gpu]
        with patch.object(
            SensorEnumerator, '_wmi_get_gpu_list',
            return_value=[('wmi:0', 'should not see this')],
        ) as wmi_mock:
            result = e.get_gpu_list()
        wmi_mock.assert_not_called()
        assert any('amd_radeon_rx_9070_xt'.startswith(key.split(':')[1])
                   or 'amd_radeon_rx_9070' in key for key, _ in result)

    def test_wmi_skipped_when_pynvml_returns_gpus(self):
        """pynvml NVIDIA path takes precedence over WMI when present."""
        from trcc.adapters.system.windows_platform import SensorEnumerator
        e = SensorEnumerator()
        e._lhm_computer = None
        with patch.object(
            SensorEnumerator.__bases__[0], 'get_gpu_list',
            return_value=[('nvidia:0', 'RTX 4090 (24576 MB)')],
        ), patch.object(
            SensorEnumerator, '_wmi_get_gpu_list',
            return_value=[('wmi:0', 'should not see this')],
        ) as wmi_mock:
            result = e.get_gpu_list()
        wmi_mock.assert_not_called()
        assert result == [('nvidia:0', 'RTX 4090 (24576 MB)')]
