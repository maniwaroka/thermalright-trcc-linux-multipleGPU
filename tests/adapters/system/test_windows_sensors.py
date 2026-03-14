"""Tests for Windows sensor enumerator (mocked — runs on Linux)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from trcc.core.models import SensorInfo

MODULE = 'trcc.adapters.system.windows.sensors'


# ── Helpers ────────────────────────────────────────────────────────────


def _make_enum(**flags):
    """Create WindowsSensorEnumerator with optional feature flags."""
    with patch(f'{MODULE}.LHM_AVAILABLE', flags.get('lhm', False)), \
         patch(f'{MODULE}.NVML_AVAILABLE', flags.get('nvml', False)):
        from trcc.adapters.system.windows.sensors import WindowsSensorEnumerator
        return WindowsSensorEnumerator()


def _mock_lhm_sensor(name: str, sensor_type: str, value: float):
    """Create a mock LHM sensor object."""
    s = MagicMock()
    s.Name = name
    s.SensorType = sensor_type
    s.Value = value
    return s


def _mock_lhm_hardware(name: str, hw_type: str, sensors: list, sub: list | None = None):
    """Create a mock LHM hardware object."""
    hw = MagicMock()
    hw.Name = name
    hw.HardwareType = hw_type
    hw.Sensors = sensors
    hw.SubHardware = sub or []
    return hw


# ── Discovery Tests ───────────────────────────────────────────────────


class TestDiscoverPsutil:
    """psutil-based sensor discovery."""

    def test_discovers_cpu_and_memory(self):
        enum = _make_enum()
        enum._discover_psutil()
        ids = [s.id for s in enum._sensors]
        assert 'psutil:cpu_percent' in ids
        assert 'psutil:cpu_freq' in ids
        assert 'psutil:mem_used' in ids
        assert 'psutil:mem_total' in ids
        assert 'psutil:mem_percent' in ids

    def test_discovers_disk_and_network(self):
        enum = _make_enum()
        enum._discover_psutil()
        ids = [s.id for s in enum._sensors]
        assert 'computed:disk_read' in ids
        assert 'computed:disk_write' in ids
        assert 'computed:net_up' in ids
        assert 'computed:net_down' in ids

    @patch(f'{MODULE}.psutil')
    def test_discovers_cpu_temps(self, mock_psutil):
        mock_psutil.sensors_temperatures.return_value = {
            'coretemp': [MagicMock(label='Package', current=65.0)],
        }
        enum = _make_enum()
        enum._discover_psutil()
        temp_sensors = [s for s in enum._sensors if s.id == 'psutil:temp:coretemp:0']
        assert len(temp_sensors) == 1
        assert temp_sensors[0].name == 'Package'
        assert temp_sensors[0].source == 'psutil'

    @patch(f'{MODULE}.psutil')
    def test_cpu_temp_fallback_label(self, mock_psutil):
        mock_psutil.sensors_temperatures.return_value = {
            'k10temp': [MagicMock(label='', current=55.0)],
        }
        enum = _make_enum()
        enum._discover_psutil()
        temp_sensors = [s for s in enum._sensors if 'k10temp' in s.id]
        assert temp_sensors[0].name == 'k10temp temp0'

    def test_all_sensors_have_source(self):
        enum = _make_enum()
        enum._discover_psutil()
        for s in enum._sensors:
            assert s.source in ('psutil', 'computed')


class TestDiscoverLhm:
    """LibreHardwareMonitor discovery (mocked)."""

    @patch(f'{MODULE}.LHM_AVAILABLE', False)
    def test_noop_without_lhm(self):
        enum = _make_enum()
        enum._discover_lhm()
        assert enum._sensors == []
        assert enum._lhm_gpu_used is False

    @patch(f'{MODULE}.LHM_AVAILABLE', True)
    @patch(f'{MODULE}.Computer', create=True)
    def test_discovers_gpu_sensors(self, mock_computer_cls):
        gpu_sensors = [
            _mock_lhm_sensor('GPU Core', 'Temperature', 72.0),
            _mock_lhm_sensor('GPU Hot Spot', 'Temperature', 85.0),
            _mock_lhm_sensor('GPU Memory Junction', 'Temperature', 94.0),
            _mock_lhm_sensor('GPU Core', 'Voltage', 1.05),
            _mock_lhm_sensor('GPU Core', 'Clock', 1950.0),
            _mock_lhm_sensor('GPU Core', 'Load', 98.0),
            _mock_lhm_sensor('GPU Package', 'Power', 320.0),
        ]
        gpu_hw = _mock_lhm_hardware('NVIDIA RTX 4090', 'GpuNvidia', gpu_sensors)

        mock_computer = MagicMock()
        mock_computer.Hardware = [gpu_hw]
        mock_computer_cls.return_value = mock_computer

        enum = _make_enum()
        enum._discover_lhm()

        assert enum._lhm_gpu_used is True
        assert enum._lhm_computer is mock_computer

        ids = [s.id for s in enum._sensors]
        # Hotspot and memory junction — Windows-exclusive via NVAPI
        assert any('hot_spot' in sid for sid in ids)
        assert any('memory_junction' in sid for sid in ids)
        # Voltage — also NVAPI-exclusive
        voltage_sensors = [s for s in enum._sensors if s.category == 'voltage']
        assert len(voltage_sensors) >= 1

    @patch(f'{MODULE}.LHM_AVAILABLE', True)
    @patch(f'{MODULE}.Computer', create=True)
    def test_discovers_cpu_via_lhm(self, mock_computer_cls):
        cpu_sensors = [
            _mock_lhm_sensor('CPU Package', 'Temperature', 55.0),
            _mock_lhm_sensor('CPU Total', 'Load', 42.0),
        ]
        cpu_hw = _mock_lhm_hardware('AMD Ryzen 9 7950X', 'Cpu', cpu_sensors)

        mock_computer = MagicMock()
        mock_computer.Hardware = [cpu_hw]
        mock_computer_cls.return_value = mock_computer

        enum = _make_enum()
        enum._discover_lhm()

        assert enum._lhm_gpu_used is False  # No GPU hardware
        assert len(enum._sensors) == 2
        assert all(s.source == 'lhm' for s in enum._sensors)

    @patch(f'{MODULE}.LHM_AVAILABLE', True)
    @patch(f'{MODULE}.Computer', create=True)
    def test_discovers_subhardware(self, mock_computer_cls):
        core_sensor = _mock_lhm_sensor('Core #1', 'Temperature', 58.0)
        sub = MagicMock()
        sub.Name = 'CPU Core #1'
        sub.Sensors = [core_sensor]

        cpu_hw = _mock_lhm_hardware('Intel i9-13900K', 'Cpu', [], sub=[sub])

        mock_computer = MagicMock()
        mock_computer.Hardware = [cpu_hw]
        mock_computer_cls.return_value = mock_computer

        enum = _make_enum()
        enum._discover_lhm()

        assert len(enum._sensors) == 1
        assert 'cpu_core_#1' in enum._sensors[0].id

    @patch(f'{MODULE}.LHM_AVAILABLE', True)
    @patch(f'{MODULE}.Computer', create=True)
    def test_skips_unknown_sensor_type(self, mock_computer_cls):
        unknown = _mock_lhm_sensor('Mystery', 'UnknownType', 42.0)
        hw = _mock_lhm_hardware('Board', 'Motherboard', [unknown])

        mock_computer = MagicMock()
        mock_computer.Hardware = [hw]
        mock_computer_cls.return_value = mock_computer

        enum = _make_enum()
        enum._discover_lhm()
        assert enum._sensors == []

    @patch(f'{MODULE}.LHM_AVAILABLE', True)
    @patch(f'{MODULE}.Computer', create=True)
    def test_lhm_exception_handled(self, mock_computer_cls):
        mock_computer_cls.side_effect = RuntimeError("COM error")
        enum = _make_enum()
        enum._discover_lhm()
        assert enum._sensors == []
        assert enum._lhm_computer is None


class TestDiscoverNvidia:
    """pynvml-based GPU discovery (fallback)."""

    @patch(f'{MODULE}.NVML_AVAILABLE', False)
    def test_noop_without_nvml(self):
        enum = _make_enum()
        enum._discover_nvidia()
        assert enum._sensors == []

    @patch(f'{MODULE}.NVML_AVAILABLE', True)
    @patch(f'{MODULE}.pynvml')
    def test_discovers_gpu_sensors(self, mock_nvml):
        mock_nvml.nvmlDeviceGetCount.return_value = 1
        mock_nvml.nvmlDeviceGetHandleByIndex.return_value = 'handle0'
        mock_nvml.nvmlDeviceGetName.return_value = 'RTX 4090'

        enum = _make_enum()
        enum._discover_nvidia()

        assert len(enum._sensors) == 7
        ids = [s.id for s in enum._sensors]
        assert 'nvidia:0:temp' in ids
        assert 'nvidia:0:gpu_busy' in ids
        assert 'nvidia:0:clock' in ids
        assert 'nvidia:0:power' in ids
        assert 'nvidia:0:fan' in ids
        assert 'nvidia:0:mem_used' in ids
        assert 'nvidia:0:mem_total' in ids
        assert all(s.source == 'nvidia' for s in enum._sensors)

    @patch(f'{MODULE}.NVML_AVAILABLE', True)
    @patch(f'{MODULE}.pynvml')
    def test_gpu_name_bytes(self, mock_nvml):
        mock_nvml.nvmlDeviceGetCount.return_value = 1
        mock_nvml.nvmlDeviceGetHandleByIndex.return_value = 'h'
        mock_nvml.nvmlDeviceGetName.return_value = b'RTX 3080'

        enum = _make_enum()
        enum._discover_nvidia()
        assert any('RTX 3080' in s.name for s in enum._sensors)

    @patch(f'{MODULE}.NVML_AVAILABLE', True)
    @patch(f'{MODULE}.pynvml')
    def test_multi_gpu(self, mock_nvml):
        mock_nvml.nvmlDeviceGetCount.return_value = 2
        mock_nvml.nvmlDeviceGetHandleByIndex.side_effect = ['h0', 'h1']
        mock_nvml.nvmlDeviceGetName.side_effect = ['GPU A', 'GPU B']

        enum = _make_enum()
        enum._discover_nvidia()
        assert len(enum._sensors) == 14  # 7 per GPU
        assert any('nvidia:0:temp' in s.id for s in enum._sensors)
        assert any('nvidia:1:temp' in s.id for s in enum._sensors)

    @patch(f'{MODULE}.NVML_AVAILABLE', True)
    @patch(f'{MODULE}.pynvml')
    def test_nvml_exception_handled(self, mock_nvml):
        mock_nvml.nvmlDeviceGetCount.side_effect = RuntimeError("driver")
        enum = _make_enum()
        enum._discover_nvidia()
        assert enum._sensors == []


class TestDiscoverWmi:
    """WMI thermal zone discovery (mocked)."""

    def test_noop_without_wmi_package(self):
        enum = _make_enum()
        # wmi not installed on Linux — ImportError path
        enum._discover_wmi()
        assert not any(s.source == 'wmi' for s in enum._sensors)


class TestDiscoverComputed:
    """Computed datetime sensor discovery."""

    def test_discovers_datetime_sensors(self):
        enum = _make_enum()
        enum._discover_computed()
        ids = [s.id for s in enum._sensors]
        assert 'computed:date_year' in ids
        assert 'computed:time_hour' in ids
        assert 'computed:day_of_week' in ids
        assert len(enum._sensors) == 7
        assert all(s.source == 'computed' for s in enum._sensors)


class TestDiscoverEndToEnd:
    """Full discover() orchestration."""

    @patch(f'{MODULE}.LHM_AVAILABLE', False)
    @patch(f'{MODULE}.NVML_AVAILABLE', False)
    def test_discover_psutil_only(self):
        enum = _make_enum()
        sensors = enum.discover()
        assert len(sensors) > 0
        sources = {s.source for s in sensors}
        assert 'psutil' in sources
        assert 'computed' in sources
        assert 'nvidia' not in sources
        assert 'lhm' not in sources

    @patch(f'{MODULE}.LHM_AVAILABLE', True)
    @patch(f'{MODULE}.Computer', create=True)
    @patch(f'{MODULE}.NVML_AVAILABLE', True)
    @patch(f'{MODULE}.pynvml')
    def test_lhm_gpu_skips_nvml(self, mock_nvml, mock_computer_cls):
        """When LHM finds GPU, pynvml discovery is skipped."""
        gpu_sensor = _mock_lhm_sensor('GPU Core', 'Temperature', 72.0)
        gpu_hw = _mock_lhm_hardware('RTX 4090', 'GpuNvidia', [gpu_sensor])

        mock_computer = MagicMock()
        mock_computer.Hardware = [gpu_hw]
        mock_computer_cls.return_value = mock_computer

        enum = _make_enum(lhm=True, nvml=True)
        enum.discover()

        assert enum._lhm_gpu_used is True
        # pynvml should NOT have been called
        mock_nvml.nvmlDeviceGetCount.assert_not_called()

    @patch(f'{MODULE}.LHM_AVAILABLE', True)
    @patch(f'{MODULE}.Computer', create=True)
    @patch(f'{MODULE}.NVML_AVAILABLE', True)
    @patch(f'{MODULE}.pynvml')
    def test_no_lhm_gpu_falls_back_to_nvml(self, mock_nvml, mock_computer_cls):
        """When LHM finds no GPU hardware, pynvml is used."""
        cpu_sensor = _mock_lhm_sensor('CPU Package', 'Temperature', 55.0)
        cpu_hw = _mock_lhm_hardware('AMD Ryzen', 'Cpu', [cpu_sensor])

        mock_computer = MagicMock()
        mock_computer.Hardware = [cpu_hw]
        mock_computer_cls.return_value = mock_computer

        mock_nvml.nvmlDeviceGetCount.return_value = 1
        mock_nvml.nvmlDeviceGetHandleByIndex.return_value = 'h'
        mock_nvml.nvmlDeviceGetName.return_value = 'RTX 4090'

        enum = _make_enum(lhm=True, nvml=True)
        enum.discover()

        assert enum._lhm_gpu_used is False
        mock_nvml.nvmlDeviceGetCount.assert_called_once()

    def test_discover_clears_previous(self):
        enum = _make_enum()
        enum._sensors = [SensorInfo('old', 'Old', 'x', 'x', 'x')]
        enum.discover()
        assert not any(s.id == 'old' for s in enum._sensors)


# ── Reading Tests ─────────────────────────────────────────────────────


class TestPollPsutil:
    """psutil reading in _poll_once."""

    @patch(f'{MODULE}.psutil')
    def test_reads_cpu_and_memory(self, mock_psutil):
        mock_psutil.cpu_percent.return_value = 42.0
        mock_psutil.cpu_freq.return_value = MagicMock(current=3600.0)
        mock_psutil.virtual_memory.return_value = MagicMock(
            used=8 * 1024 ** 2, total=16 * 1024 ** 2, percent=50.0,
        )

        enum = _make_enum()
        enum._poll_once()
        readings = enum.read_all()

        assert readings['psutil:cpu_percent'] == 42.0
        assert readings['psutil:cpu_freq'] == 3600.0
        assert readings['psutil:mem_percent'] == 50.0

    @patch(f'{MODULE}.psutil')
    def test_no_cpu_freq(self, mock_psutil):
        mock_psutil.cpu_percent.return_value = 10.0
        mock_psutil.cpu_freq.return_value = None
        mock_psutil.virtual_memory.return_value = MagicMock(
            used=0, total=0, percent=0,
        )

        enum = _make_enum()
        enum._poll_once()
        readings = enum.read_all()
        assert 'psutil:cpu_freq' not in readings


class TestPollLhm:
    """LHM sensor reading (mocked)."""

    def test_reads_lhm_sensors(self):
        enum = _make_enum()
        mock_computer = MagicMock()

        gpu_sensors = [
            _mock_lhm_sensor('GPU Core Temp', 'Temperature', 72.0),
            _mock_lhm_sensor('GPU Hot Spot', 'Temperature', 85.0),
            _mock_lhm_sensor('GPU Core Voltage', 'Voltage', 1.05),
        ]
        gpu_hw = MagicMock()
        gpu_hw.Name = 'NVIDIA RTX 4090'
        gpu_hw.Sensors = gpu_sensors
        gpu_hw.SubHardware = []
        mock_computer.Hardware = [gpu_hw]
        enum._lhm_computer = mock_computer

        readings: dict[str, float] = {}
        enum._poll_lhm(readings)

        assert readings['lhm:nvidia_rtx_4090:gpu_core_temp'] == 72.0
        assert readings['lhm:nvidia_rtx_4090:gpu_hot_spot'] == 85.0
        assert readings['lhm:nvidia_rtx_4090:gpu_core_voltage'] == 1.05

    def test_skips_none_values(self):
        enum = _make_enum()
        mock_computer = MagicMock()
        sensor = _mock_lhm_sensor('Dead Sensor', 'Temperature', None)
        sensor.Value = None
        hw = MagicMock()
        hw.Name = 'Board'
        hw.Sensors = [sensor]
        hw.SubHardware = []
        mock_computer.Hardware = [hw]
        enum._lhm_computer = mock_computer

        readings: dict[str, float] = {}
        enum._poll_lhm(readings)
        assert len(readings) == 0

    def test_reads_subhardware(self):
        enum = _make_enum()
        mock_computer = MagicMock()

        core_sensor = _mock_lhm_sensor('Core #0', 'Temperature', 60.0)
        sub = MagicMock()
        sub.Name = 'CPU Core'
        sub.Sensors = [core_sensor]

        hw = MagicMock()
        hw.Name = 'Intel CPU'
        hw.Sensors = []
        hw.SubHardware = [sub]
        mock_computer.Hardware = [hw]
        enum._lhm_computer = mock_computer

        readings: dict[str, float] = {}
        enum._poll_lhm(readings)
        assert readings['lhm:cpu_core:core_#0'] == 60.0

    def test_lhm_poll_exception_isolated(self):
        enum = _make_enum()
        mock_computer = MagicMock()
        mock_computer.Hardware.__iter__ = MagicMock(side_effect=RuntimeError("COM"))
        enum._lhm_computer = mock_computer

        readings: dict[str, float] = {}
        enum._poll_lhm(readings)  # Should not raise
        assert len(readings) == 0


class TestPollNvidia:
    """pynvml reading (fallback)."""

    @patch(f'{MODULE}.pynvml')
    def test_reads_all_gpu_metrics(self, mock_nvml):
        mock_nvml.nvmlDeviceGetCount.return_value = 1
        mock_nvml.nvmlDeviceGetHandleByIndex.return_value = 'h'
        mock_nvml.NVML_TEMPERATURE_GPU = 0
        mock_nvml.NVML_CLOCK_GRAPHICS = 0
        mock_nvml.nvmlDeviceGetTemperature.return_value = 72
        mock_nvml.nvmlDeviceGetUtilizationRates.return_value = MagicMock(gpu=95)
        mock_nvml.nvmlDeviceGetClockInfo.return_value = 1950
        mock_nvml.nvmlDeviceGetPowerUsage.return_value = 320000  # mW
        mock_nvml.nvmlDeviceGetFanSpeed.return_value = 65
        mock_nvml.nvmlDeviceGetMemoryInfo.return_value = MagicMock(
            used=8 * 1024 ** 2, total=24 * 1024 ** 2,
        )

        enum = _make_enum()
        readings: dict[str, float] = {}
        enum._poll_nvidia(readings)

        assert readings['nvidia:0:temp'] == 72.0
        assert readings['nvidia:0:gpu_busy'] == 95.0
        assert readings['nvidia:0:clock'] == 1950.0
        assert readings['nvidia:0:power'] == 320.0
        assert readings['nvidia:0:fan'] == 65.0
        assert readings['nvidia:0:mem_used'] == 8.0
        assert readings['nvidia:0:mem_total'] == 24.0

    @patch(f'{MODULE}.pynvml')
    def test_individual_metric_failure_isolated(self, mock_nvml):
        """One metric failing doesn't block others."""
        mock_nvml.nvmlDeviceGetCount.return_value = 1
        mock_nvml.nvmlDeviceGetHandleByIndex.return_value = 'h'
        mock_nvml.NVML_TEMPERATURE_GPU = 0
        mock_nvml.NVML_CLOCK_GRAPHICS = 0
        mock_nvml.nvmlDeviceGetTemperature.side_effect = RuntimeError("no temp")
        mock_nvml.nvmlDeviceGetUtilizationRates.return_value = MagicMock(gpu=50)
        mock_nvml.nvmlDeviceGetClockInfo.side_effect = RuntimeError("no clock")
        mock_nvml.nvmlDeviceGetPowerUsage.side_effect = RuntimeError("no power")
        mock_nvml.nvmlDeviceGetFanSpeed.side_effect = RuntimeError("no fan")
        mock_nvml.nvmlDeviceGetMemoryInfo.side_effect = RuntimeError("no mem")

        enum = _make_enum()
        readings: dict[str, float] = {}
        enum._poll_nvidia(readings)

        assert 'nvidia:0:temp' not in readings
        assert readings['nvidia:0:gpu_busy'] == 50.0  # This one succeeded
        assert 'nvidia:0:clock' not in readings

    def test_poll_nvidia_noop_when_none(self):
        enum = _make_enum()
        readings: dict[str, float] = {}
        with patch(f'{MODULE}.pynvml', None):
            enum._poll_nvidia(readings)
        assert len(readings) == 0


class TestPollDatetime:
    """Datetime computed readings."""

    @patch(f'{MODULE}.psutil')
    @patch(f'{MODULE}.datetime')
    def test_reads_datetime(self, mock_dt, mock_psutil):
        mock_psutil.cpu_percent.return_value = 0
        mock_psutil.cpu_freq.return_value = None
        mock_psutil.virtual_memory.return_value = MagicMock(
            used=0, total=0, percent=0,
        )

        from datetime import datetime
        mock_dt.datetime.now.return_value = datetime(2026, 3, 13, 14, 30, 45)

        enum = _make_enum()
        enum._poll_once()
        readings = enum.read_all()

        assert readings['computed:date_year'] == 2026.0
        assert readings['computed:date_month'] == 3.0
        assert readings['computed:date_day'] == 13.0
        assert readings['computed:time_hour'] == 14.0
        assert readings['computed:time_minute'] == 30.0
        assert readings['computed:time_second'] == 45.0


# ── Polling Thread Tests ──────────────────────────────────────────────


class TestPolling:
    """start_polling / stop_polling lifecycle."""

    def test_start_stop(self):
        enum = _make_enum()
        with patch.object(enum, '_poll_once'):
            enum.start_polling(interval=0.01)
            assert enum._poll_thread is not None
            assert enum._poll_thread.is_alive()
            enum.stop_polling()
            assert not enum._poll_thread.is_alive()

    def test_double_start_noop(self):
        enum = _make_enum()
        with patch.object(enum, '_poll_once'):
            enum.start_polling(interval=0.01)
            first_thread = enum._poll_thread
            enum.start_polling(interval=0.01)  # Should not create new thread
            assert enum._poll_thread is first_thread
            enum.stop_polling()

    def test_stop_closes_lhm(self):
        enum = _make_enum()
        mock_computer = MagicMock()
        enum._lhm_computer = mock_computer
        enum.stop_polling()
        mock_computer.Close.assert_called_once()
        assert enum._lhm_computer is None

    def test_stop_lhm_close_exception_handled(self):
        enum = _make_enum()
        mock_computer = MagicMock()
        mock_computer.Close.side_effect = RuntimeError("COM")
        enum._lhm_computer = mock_computer
        enum.stop_polling()  # Should not raise
        assert enum._lhm_computer is None


# ── Getters ───────────────────────────────────────────────────────────


class TestGetters:
    """get_sensors, get_by_category, read_all."""

    def test_get_sensors_returns_list(self):
        enum = _make_enum()
        assert enum.get_sensors() == []

    def test_get_by_category(self):
        enum = _make_enum()
        enum._sensors = [
            SensorInfo('a', 'A', 'temperature', '°C', 'lhm'),
            SensorInfo('b', 'B', 'fan', 'RPM', 'lhm'),
            SensorInfo('c', 'C', 'temperature', '°C', 'nvidia'),
        ]
        temps = enum.get_by_category('temperature')
        assert len(temps) == 2
        fans = enum.get_by_category('fan')
        assert len(fans) == 1

    def test_read_all_returns_copy(self):
        enum = _make_enum()
        enum._readings = {'x': 1.0}
        r = enum.read_all()
        r['x'] = 999.0
        assert enum._readings['x'] == 1.0  # Original unchanged


# ── LHM Type Map ──────────────────────────────────────────────────────


class TestLhmTypeMap:
    """_LHM_TYPE_MAP coverage."""

    def test_all_expected_types_mapped(self):
        from trcc.adapters.system.windows.sensors import _LHM_TYPE_MAP
        expected = {'Temperature', 'Fan', 'Clock', 'Load', 'Power',
                    'Voltage', 'SmallData', 'Data', 'Throughput'}
        assert set(_LHM_TYPE_MAP.keys()) == expected

    def test_voltage_mapping(self):
        from trcc.adapters.system.windows.sensors import _LHM_TYPE_MAP
        assert _LHM_TYPE_MAP['Voltage'] == ('voltage', 'V')

    def test_temperature_mapping(self):
        from trcc.adapters.system.windows.sensors import _LHM_TYPE_MAP
        assert _LHM_TYPE_MAP['Temperature'] == ('temperature', '°C')
