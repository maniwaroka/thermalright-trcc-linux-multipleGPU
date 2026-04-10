"""Tests for macOS sensor enumerator — platform-specific behavior only.

Shared base behavior (psutil, nvidia, computed I/O, polling, read_all)
is tested in tests/adapters/system/conftest.py.

Tests follow the app flow: discover() → read_all() → map_defaults().
Mock at I/O boundary only: subprocess for powermetrics/diskutil, IOKit SMC.
"""
from __future__ import annotations

import ctypes
import struct
from unittest.mock import MagicMock, patch

import pytest

MODULE = 'trcc.adapters.system.macos.sensors'

# powermetrics --samplers gpu_power output (no smc sampler — Tahoe compatible)
POWERMETRICS_GPU_OUTPUT = (
    "GPU active residency: 31%\n"
    "GPU Power: 4.5 W\n"
    "GPU HW active frequency: 1398 MHz\n"
    "CPU 0 frequency: 1690 MHz\n"
    "CPU 4 frequency: 2937 MHz\n"
)

DISKUTIL_OUTPUT = (
    "APFS Container Reference:     disk1\n"
    "Size (Capacity Ceiling):      500107862016 B (500.1 GB)\n"
    "Minimum Size:                 N/A\n"
    "Capacity In Use By Volumes:   400086323200 B (400.1 GB)\n"
    "Capacity Not Allocated:       100021538816 B (100.0 GB)\n"
)


def _make_smc_response(data_type: str, value: float) -> MagicMock:
    """Create a mock that simulates IOConnectCallStructMethod for SMC reads.

    Returns a side_effect function that fills the SMCKeyData_t output struct
    with the given data type and encoded value.
    """
    dt_int = struct.unpack('>I', data_type.ljust(4).encode('ascii'))[0]

    match data_type.rstrip():
        case 'sp78':
            raw = struct.pack('>h', int(value * 256))
        case 'fpe2':
            raw = struct.pack('>H', int(value * 4))
        case 'flt':
            raw = struct.pack('>f', value)
        case _:
            raw = struct.pack('>H', int(value))

    return dt_int, raw


class MockSMC:
    """Simulates IOKit SMC reads for testing."""

    def __init__(self) -> None:
        self.keys: dict[str, tuple[int, bytes]] = {}

    def add_key(self, key: str, data_type: str, value: float) -> None:
        dt_int, raw = _make_smc_response(data_type, value)
        self.keys[key] = (dt_int, raw)

    def ioconnect_side_effect(self, conn, selector, in_ptr, in_size,
                              out_ptr, out_size_ptr) -> int:
        """Side effect for IOConnectCallStructMethod."""
        from trcc.adapters.system.macos.sensors import SMCKeyData_t

        cmd = ctypes.cast(in_ptr, ctypes.POINTER(SMCKeyData_t)).contents
        out = ctypes.cast(out_ptr, ctypes.POINTER(SMCKeyData_t)).contents

        # Find which key was requested
        key_str = struct.pack('>I', cmd.key).decode('ascii', errors='replace')
        if key_str not in self.keys:
            return 1  # kIOReturnError

        dt_int, raw = self.keys[key_str]

        if cmd.data8 == 9:  # kSMCGetKeyInfo
            out.keyInfo.dataType = dt_int
            out.keyInfo.dataSize = len(raw)
        elif cmd.data8 == 5:  # kSMCReadKey
            for i, b in enumerate(raw):
                out.bytes[i] = b

        return 0  # kIOReturnSuccess


@pytest.fixture
def mock_smc():
    """Pre-configured MockSMC with typical Apple Silicon readings."""
    smc = MockSMC()
    smc.add_key('Tp01', 'sp78', 45.0)   # CPU P-Core 1
    smc.add_key('Tg0f', 'sp78', 52.0)   # GPU Die
    smc.add_key('Tm0P', 'sp78', 38.0)   # Memory
    smc.add_key('F0Ac', 'fpe2', 1200.0)  # Fan 0
    smc.add_key('F1Ac', 'fpe2', 1350.0)  # Fan 1
    return smc


@pytest.fixture
def mock_iokit(mock_smc):
    """Mock IOKit framework with working SMC connection."""
    iokit = MagicMock()
    iokit.IOServiceMatching.return_value = 1  # non-NULL
    iokit.IOServiceGetMatchingService.return_value = 1  # service handle
    iokit.IOServiceOpen.return_value = 0  # kIOReturnSuccess
    iokit.IOConnectCallStructMethod.side_effect = mock_smc.ioconnect_side_effect
    iokit.IOServiceClose.return_value = 0
    iokit.IOObjectRelease = MagicMock()
    return iokit


@pytest.fixture
def mock_macos(mock_io_no_nvidia, mock_iokit):
    """macOS Apple Silicon enumerator with mocked IOKit + subprocess."""
    with patch(f'{MODULE}.IS_APPLE_SILICON', True), \
         patch(f'{MODULE}._iokit', mock_iokit), \
         patch(f'{MODULE}.subprocess') as sub, \
         patch(f'{MODULE}.ctypes') as mock_ctypes:
        # Make ctypes work with our mock IOKit
        mock_ctypes.util.find_library.return_value = 'libSystem'
        mock_ctypes.cdll.LoadLibrary.return_value = MagicMock(
            mach_task_self=MagicMock(return_value=0))
        mock_ctypes.c_uint = type('c_uint', (), {
            '__init__': lambda self, v=0: setattr(self, 'value', v),
            'value': 0,
        })
        mock_ctypes.byref = lambda x: x
        mock_ctypes.sizeof = lambda x: 80
        mock_ctypes.c_ulong = type('c_ulong', (), {
            '__init__': lambda self, v=0: setattr(self, 'value', v),
            'value': 80,
        })

        def _run_side_effect(cmd, **kwargs):
            if 'powermetrics' in cmd:
                return MagicMock(stdout=POWERMETRICS_GPU_OUTPUT)
            if 'diskutil' in cmd:
                return MagicMock(stdout=DISKUTIL_OUTPUT)
            return MagicMock(stdout='')
        sub.run.side_effect = _run_side_effect
        mock_io_no_nvidia.subprocess = sub
        yield mock_io_no_nvidia


@pytest.fixture
def mock_macos_no_smc(mock_io_no_nvidia):
    """macOS Apple Silicon without SMC access (no root)."""
    with patch(f'{MODULE}.IS_APPLE_SILICON', True), \
         patch(f'{MODULE}._iokit', None), \
         patch(f'{MODULE}.subprocess') as sub:
        def _run_side_effect(cmd, **kwargs):
            if 'powermetrics' in cmd:
                return MagicMock(stdout=POWERMETRICS_GPU_OUTPUT)
            if 'diskutil' in cmd:
                return MagicMock(stdout=DISKUTIL_OUTPUT)
            return MagicMock(stdout='')
        sub.run.side_effect = _run_side_effect
        mock_io_no_nvidia.subprocess = sub
        yield mock_io_no_nvidia


@pytest.fixture
def mock_macos_intel(mock_io_no_nvidia):
    """macOS Intel enumerator without IOKit."""
    with patch(f'{MODULE}.IS_APPLE_SILICON', False), \
         patch(f'{MODULE}._iokit', None), \
         patch(f'{MODULE}.subprocess') as sub:
        sub.run.return_value = MagicMock(stdout='')
        mock_io_no_nvidia.subprocess = sub
        yield mock_io_no_nvidia


@pytest.fixture
def enum_no_smc(mock_macos_no_smc):
    """Discovered macOS enumerator without SMC access."""
    from trcc.adapters.system.macos.sensors import MacOSSensorEnumerator
    e = MacOSSensorEnumerator()
    e.discover()
    return e


@pytest.fixture
def enum_intel(mock_macos_intel):
    """Discovered macOS Intel enumerator (no IOKit)."""
    from trcc.adapters.system.macos.sensors import MacOSSensorEnumerator
    e = MacOSSensorEnumerator()
    e.discover()
    return e


class TestDiscover:
    """macOS discover() registers sensors from SMC + powermetrics."""

    def test_apple_silicon_gpu_sensors_registered(self, enum_no_smc):
        ids = [s.id for s in enum_no_smc.get_sensors()]
        assert 'iokit:gpu_busy' in ids
        assert 'iokit:gpu_clock' in ids
        assert 'iokit:gpu_power' in ids

    def test_base_sensors_registered(self, enum_no_smc):
        ids = [s.id for s in enum_no_smc.get_sensors()]
        assert 'psutil:cpu_percent' in ids
        assert 'computed:disk_read' in ids
        assert 'computed:date_year' in ids

    def test_no_smc_without_iokit(self, enum_no_smc):
        """No SMC sensors when IOKit unavailable (no root)."""
        assert not any(s.source == 'smc' for s in enum_no_smc.get_sensors())

    def test_intel_no_smc_without_iokit(self, enum_intel):
        """Intel Mac without IOKit — no SMC sensors, psutil still works."""
        sensors = enum_intel.get_sensors()
        assert not any(s.source == 'smc' for s in sensors)
        assert any(s.source == 'psutil' for s in sensors)


class TestReadAll:
    """macOS read_all() returns sensor readings."""

    def test_gpu_metrics_from_powermetrics(self, enum_no_smc):
        readings = enum_no_smc.read_all()
        assert readings['iokit:gpu_busy'] == 31.0
        assert readings['iokit:gpu_power'] == 4.5
        assert readings['iokit:gpu_clock'] == 1398.0

    def test_cpu_freq_from_powermetrics(self, enum_no_smc):
        """Per-core max freq overrides psutil:cpu_freq."""
        readings = enum_no_smc.read_all()
        assert readings['psutil:cpu_freq'] == 2937.0

    def test_apfs_disk_percent(self, enum_no_smc):
        readings = enum_no_smc.read_all()
        expected = round(400086323200 / 500107862016 * 100, 1)
        assert readings['computed:disk_percent'] == expected

    def test_gpu_power_milliwatts(self, mock_io_no_nvidia):
        with patch(f'{MODULE}.IS_APPLE_SILICON', True), \
             patch(f'{MODULE}._iokit', None), \
             patch(f'{MODULE}.subprocess') as sub:
            def _run(cmd, **kwargs):
                if 'powermetrics' in cmd:
                    return MagicMock(stdout="GPU Power: 150 mW\n")
                return MagicMock(stdout='')
            sub.run.side_effect = _run
            from trcc.adapters.system.macos.sensors import MacOSSensorEnumerator
            e = MacOSSensorEnumerator()
            e.discover()
            readings = e.read_all()
            assert readings['iokit:gpu_power'] == 0.15

    def test_powermetrics_failure_degrades_gracefully(self, mock_io_no_nvidia):
        with patch(f'{MODULE}.IS_APPLE_SILICON', True), \
             patch(f'{MODULE}._iokit', None), \
             patch(f'{MODULE}.subprocess') as sub:
            def _run(cmd, **kwargs):
                if 'powermetrics' in cmd:
                    raise RuntimeError("no root")
                return MagicMock(stdout='')
            sub.run.side_effect = _run
            from trcc.adapters.system.macos.sensors import MacOSSensorEnumerator
            e = MacOSSensorEnumerator()
            e.discover()
            readings = e.read_all()
            assert 'psutil:cpu_percent' in readings
            assert 'iokit:gpu_busy' not in readings

    def test_diskutil_failure_falls_back_to_psutil(self, mock_io_no_nvidia):
        with patch(f'{MODULE}.IS_APPLE_SILICON', True), \
             patch(f'{MODULE}._iokit', None), \
             patch(f'{MODULE}.subprocess') as sub, \
             patch(f'{MODULE}.psutil') as mac_psutil:
            def _run(cmd, **kwargs):
                if 'diskutil' in cmd:
                    raise FileNotFoundError("no diskutil")
                return MagicMock(stdout=POWERMETRICS_GPU_OUTPUT)
            sub.run.side_effect = _run
            mac_psutil.disk_usage.return_value = MagicMock(percent=55.0)
            from trcc.adapters.system.macos.sensors import MacOSSensorEnumerator
            e = MacOSSensorEnumerator()
            e.discover()
            readings = e.read_all()
            assert readings['computed:disk_percent'] == 55.0


class TestMapDefaults:
    """macOS map_defaults() routes to smc/iokit sources."""

    def test_gpu_mapping(self, enum_no_smc):
        mapping = enum_no_smc.map_defaults()
        assert mapping['gpu_usage'] == 'iokit:gpu_busy'
        assert mapping['gpu_clock'] == 'iokit:gpu_clock'
        assert mapping['gpu_power'] == 'iokit:gpu_power'

    def test_mem_available_correct(self, enum_no_smc):
        mapping = enum_no_smc.map_defaults()
        assert mapping['mem_available'] == 'psutil:mem_available'

    def test_common_mappings_present(self, enum_no_smc):
        mapping = enum_no_smc.map_defaults()
        assert mapping['disk_activity'] == 'computed:disk_activity'
        assert mapping['net_total_up'] == 'computed:net_total_up'
        assert mapping['net_total_down'] == 'computed:net_total_down'


class TestSMCParsing:
    """_parse_smc_bytes handles all SMC data types."""

    def test_sp78_temperature(self):
        from trcc.adapters.system.macos.sensors import _parse_smc_bytes
        dt = struct.unpack('>I', b'sp78')[0]
        raw = (ctypes.c_uint8 * 32)()
        val = struct.pack('>h', int(45.5 * 256))
        raw[0], raw[1] = val[0], val[1]
        result = _parse_smc_bytes(dt, raw, 2)
        assert abs(result - 45.5) < 0.01

    def test_fpe2_fan_speed(self):
        from trcc.adapters.system.macos.sensors import _parse_smc_bytes
        dt = struct.unpack('>I', b'fpe2')[0]
        raw = (ctypes.c_uint8 * 32)()
        val = struct.pack('>H', int(1200 * 4))
        raw[0], raw[1] = val[0], val[1]
        result = _parse_smc_bytes(dt, raw, 2)
        assert result == 1200.0

    def test_flt_float(self):
        from trcc.adapters.system.macos.sensors import _parse_smc_bytes
        dt = struct.unpack('>I', b'flt ')[0]
        raw = (ctypes.c_uint8 * 32)()
        val = struct.pack('>f', 3.14)
        for i, b in enumerate(val):
            raw[i] = b
        result = _parse_smc_bytes(dt, raw, 4)
        assert abs(result - 3.14) < 0.01


class TestParseMetric:
    """_parse_metric helper — powermetrics line parser."""

    def test_temperature(self):
        from trcc.adapters.system.macos.sensors import _parse_metric
        assert _parse_metric('CPU die temperature: 45.23 C') == 45.23

    def test_fan(self):
        from trcc.adapters.system.macos.sensors import _parse_metric
        assert _parse_metric('Fan: 1200 rpm') == 1200.0

    def test_no_number(self):
        from trcc.adapters.system.macos.sensors import _parse_metric
        assert _parse_metric('no numbers here') == 0.0
