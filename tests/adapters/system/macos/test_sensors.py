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

MODULE = 'trcc.adapters.system.macos_platform'

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


def _make_smc_response(data_type: str, value: float) -> tuple[int, bytes]:
    """Create mock SMC raw bytes for a given data type and value.

    Encodes values the same way real hardware would:
    - sp78: big-endian signed 8.8 fixed-point
    - fpe2: big-endian unsigned 14.2 fixed-point
    - flt:  little-endian IEEE 754 float (all Macs)
    - ui8:  unsigned byte
    """
    dt_int = struct.unpack('>I', data_type.ljust(4).encode('ascii'))[0]

    match data_type.rstrip():
        case 'sp78':
            raw = struct.pack('>h', int(value * 256))
        case 'fpe2':
            raw = struct.pack('>H', int(value * 4))
        case 'flt':
            raw = struct.pack('<f', value)
        case 'ui8':
            raw = struct.pack('B', int(value))
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

    def ioconnect_side_effect(self, conn, selector, cmd, in_size,
                              cmd_out, out_size_ptr) -> int:
        """Side effect for IOConnectCallStructMethod.

        The mock replaces ctypes.byref with identity (lambda x: x),
        so cmd/cmd_out are the SMCKeyData_t structs directly.
        """
        key_str = struct.pack('>I', cmd.key).decode('ascii', errors='replace')
        if key_str not in self.keys:
            return 1  # kIOReturnError

        dt_int, raw = self.keys[key_str]

        if cmd.data8 == 9:  # kSMCGetKeyInfo
            cmd_out.keyInfo.dataType = dt_int
            cmd_out.keyInfo.dataSize = len(raw)
        elif cmd.data8 == 5:  # kSMCReadKey
            for i, b in enumerate(raw):
                cmd_out.bytes[i] = b

        return 0  # kIOReturnSuccess


@pytest.fixture
def mock_smc():
    """Pre-configured MockSMC with typical Apple Silicon readings."""
    smc = MockSMC()
    smc.add_key('Tp01', 'sp78', 45.0)   # CPU P-Core 1
    smc.add_key('Tg0f', 'sp78', 52.0)   # GPU Die
    smc.add_key('Tm0P', 'sp78', 38.0)   # Memory
    # Apple Silicon fans use flt (little-endian IEEE 754)
    smc.add_key('FNum', 'ui8', 2.0)     # 2 fans
    smc.add_key('F0Ac', 'flt', 1200.0)  # Fan 0
    smc.add_key('F1Ac', 'flt', 1350.0)  # Fan 1
    return smc


@pytest.fixture
def mock_smc_intel():
    """Pre-configured MockSMC with typical Intel Mac readings."""
    smc = MockSMC()
    smc.add_key('TC0P', 'sp78', 55.0)   # CPU Proximity
    smc.add_key('TG0D', 'sp78', 48.0)   # GPU Die
    # Intel fans use fpe2 (big-endian 14.2 fixed-point)
    smc.add_key('FNum', 'ui8', 2.0)     # 2 fans
    smc.add_key('F0Ac', 'fpe2', 1800.0) # Fan 0
    smc.add_key('F1Ac', 'fpe2', 1900.0) # Fan 1
    return smc


def _make_iokit_mock(smc: MockSMC) -> MagicMock:
    """Build an IOKit mock wired to a MockSMC instance.

    IOServiceOpen sets the connection handle to a non-zero value so
    _open_smc() succeeds (self._smc_conn != 0).
    """
    iokit = MagicMock()
    iokit.IOServiceMatching.return_value = 1  # non-NULL

    def _ioservice_open(service, task, conn_type, conn_ref):
        conn_ref.value = 42  # non-zero connection handle
        return 0  # kIOReturnSuccess

    iokit.IOServiceGetMatchingService.return_value = 1  # service handle
    iokit.IOServiceOpen.side_effect = _ioservice_open
    iokit.IOConnectCallStructMethod.side_effect = smc.ioconnect_side_effect
    iokit.IOServiceClose.return_value = 0
    iokit.IOObjectRelease = MagicMock()
    return iokit


@pytest.fixture
def mock_iokit(mock_smc):
    """Mock IOKit framework with working SMC connection."""
    return _make_iokit_mock(mock_smc)


@pytest.fixture
def mock_iokit_intel(mock_smc_intel):
    """Mock IOKit framework with Intel SMC."""
    return _make_iokit_mock(mock_smc_intel)


def _make_mock_ctypes():
    """Common ctypes mock setup for SMC tests."""
    mock_ctypes = MagicMock()
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
    return mock_ctypes


def _make_subprocess_side_effect(powermetrics_out=POWERMETRICS_GPU_OUTPUT,
                                 diskutil_out=DISKUTIL_OUTPUT):
    """Create subprocess.run side_effect for powermetrics/diskutil."""
    def _run(cmd, **kwargs):
        if 'powermetrics' in cmd:
            return MagicMock(stdout=powermetrics_out)
        if 'diskutil' in cmd:
            return MagicMock(stdout=diskutil_out)
        return MagicMock(stdout='')
    return _run


@pytest.fixture
def mock_macos(mock_io_no_nvidia, mock_iokit):
    """macOS Apple Silicon enumerator with mocked IOKit + subprocess."""
    with patch(f'{MODULE}.IS_APPLE_SILICON', True), \
         patch(f'{MODULE}._iokit', mock_iokit), \
         patch(f'{MODULE}.subprocess') as sub, \
         patch(f'{MODULE}.ctypes') as mock_ctypes_mod:
        mc = _make_mock_ctypes()
        for attr in dir(mc):
            if not attr.startswith('_'):
                setattr(mock_ctypes_mod, attr, getattr(mc, attr))
        sub.run.side_effect = _make_subprocess_side_effect()
        mock_io_no_nvidia.subprocess = sub
        yield mock_io_no_nvidia


@pytest.fixture
def mock_macos_no_smc(mock_io_no_nvidia):
    """macOS Apple Silicon without SMC access (no root)."""
    with patch(f'{MODULE}.IS_APPLE_SILICON', True), \
         patch(f'{MODULE}._iokit', None), \
         patch(f'{MODULE}.subprocess') as sub:
        sub.run.side_effect = _make_subprocess_side_effect()
        mock_io_no_nvidia.subprocess = sub
        yield mock_io_no_nvidia


@pytest.fixture
def mock_macos_intel(mock_io_no_nvidia, mock_iokit_intel):
    """macOS Intel enumerator with IOKit SMC."""
    with patch(f'{MODULE}.IS_APPLE_SILICON', False), \
         patch(f'{MODULE}._iokit', mock_iokit_intel), \
         patch(f'{MODULE}.subprocess') as sub, \
         patch(f'{MODULE}.ctypes') as mock_ctypes_mod:
        mc = _make_mock_ctypes()
        for attr in dir(mc):
            if not attr.startswith('_'):
                setattr(mock_ctypes_mod, attr, getattr(mc, attr))
        sub.run.return_value = MagicMock(stdout='')
        mock_io_no_nvidia.subprocess = sub
        yield mock_io_no_nvidia


@pytest.fixture
def enum_no_smc(mock_macos_no_smc):
    """Discovered macOS enumerator without SMC access."""
    from trcc.adapters.system.macos_platform import SensorEnumerator
    e = SensorEnumerator()
    e.discover()
    return e


@pytest.fixture
def enum_intel(mock_macos_intel):
    """Discovered macOS Intel enumerator with IOKit SMC."""
    from trcc.adapters.system.macos_platform import SensorEnumerator
    e = SensorEnumerator()
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

    def test_intel_smc_temps_discovered(self, enum_intel):
        """Intel Mac discovers temps via IOKit SMC."""
        ids = [s.id for s in enum_intel.get_sensors()]
        assert 'smc:TC0P' in ids
        assert 'smc:TG0D' in ids

    def test_intel_fans_discovered(self, enum_intel):
        """Intel Mac discovers fans via FNum + fpe2 encoding."""
        ids = [s.id for s in enum_intel.get_sensors()]
        assert 'smc:F0Ac' in ids
        assert 'smc:F1Ac' in ids
        # FNum=2, so F2Ac/F3Ac should NOT be probed
        assert 'smc:F2Ac' not in ids

    def test_intel_no_as_keys_probed(self, enum_intel):
        """Intel Mac does not probe Apple Silicon extended keys."""
        assert not any(s.id.startswith('smc:Te') for s in enum_intel.get_sensors())
        assert not any(s.id.startswith('smc:Tf') for s in enum_intel.get_sensors())


class TestAppleSiliconDiscovery:
    """Apple Silicon extended key discovery + dynamic fan count."""

    def test_as_temp_keys_discovered(self, mock_io_no_nvidia, mock_smc):
        """AS-specific temp keys (Te*, Tg0K, etc.) are discovered."""
        mock_smc.add_key('Te04', 'sp78', 41.0)
        mock_smc.add_key('Tp0a', 'sp78', 43.0)
        mock_smc.add_key('Tg0K', 'sp78', 50.0)
        mock_smc.add_key('Tf14', 'sp78', 39.0)
        iokit = _make_iokit_mock(mock_smc)

        with patch(f'{MODULE}.IS_APPLE_SILICON', True), \
             patch(f'{MODULE}._iokit', iokit), \
             patch(f'{MODULE}.subprocess') as sub, \
             patch(f'{MODULE}.ctypes') as mock_ctypes_mod:
            mc = _make_mock_ctypes()
            for attr in dir(mc):
                if not attr.startswith('_'):
                    setattr(mock_ctypes_mod, attr, getattr(mc, attr))
            sub.run.side_effect = _make_subprocess_side_effect()
            from trcc.adapters.system.macos_platform import SensorEnumerator
            e = SensorEnumerator()
            e.discover()

        ids = [s.id for s in e.get_sensors()]
        assert 'smc:Te04' in ids
        assert 'smc:Tp0a' in ids
        assert 'smc:Tg0K' in ids
        assert 'smc:Tf14' in ids

        for s in e.get_sensors():
            if s.id in ('smc:Te04', 'smc:Tp0a', 'smc:Tg0K', 'smc:Tf14'):
                assert s.category == 'temperature'
                assert s.unit == '°C'

    def test_fnum_dynamic_fan_count(self, mock_io_no_nvidia, mock_smc):
        """FNum=2 limits fan probing to F0Ac and F1Ac only."""
        iokit = _make_iokit_mock(mock_smc)

        with patch(f'{MODULE}.IS_APPLE_SILICON', True), \
             patch(f'{MODULE}._iokit', iokit), \
             patch(f'{MODULE}.subprocess') as sub, \
             patch(f'{MODULE}.ctypes') as mock_ctypes_mod:
            mc = _make_mock_ctypes()
            for attr in dir(mc):
                if not attr.startswith('_'):
                    setattr(mock_ctypes_mod, attr, getattr(mc, attr))
            sub.run.side_effect = _make_subprocess_side_effect()
            from trcc.adapters.system.macos_platform import SensorEnumerator
            e = SensorEnumerator()
            e.discover()

        fan_ids = [s.id for s in e.get_sensors() if s.category == 'fan']
        assert 'smc:F0Ac' in fan_ids
        assert 'smc:F1Ac' in fan_ids
        assert len(fan_ids) == 2

    def test_fnum_unavailable_fallback(self, mock_io_no_nvidia):
        """Without FNum, falls back to probing F0Ac–F3Ac."""
        smc = MockSMC()
        smc.add_key('Tp01', 'sp78', 45.0)
        smc.add_key('F0Ac', 'flt', 1100.0)
        smc.add_key('F1Ac', 'flt', 1200.0)
        iokit = _make_iokit_mock(smc)

        with patch(f'{MODULE}.IS_APPLE_SILICON', True), \
             patch(f'{MODULE}._iokit', iokit), \
             patch(f'{MODULE}.subprocess') as sub, \
             patch(f'{MODULE}.ctypes') as mock_ctypes_mod:
            mc = _make_mock_ctypes()
            for attr in dir(mc):
                if not attr.startswith('_'):
                    setattr(mock_ctypes_mod, attr, getattr(mc, attr))
            sub.run.side_effect = _make_subprocess_side_effect()
            from trcc.adapters.system.macos_platform import SensorEnumerator
            e = SensorEnumerator()
            e.discover()

        fan_ids = [s.id for s in e.get_sensors() if s.category == 'fan']
        assert 'smc:F0Ac' in fan_ids
        assert 'smc:F1Ac' in fan_ids
        assert 'smc:F2Ac' not in fan_ids
        assert len(fan_ids) == 2

    def test_flt_fan_rpm_apple_silicon(self, mock_io_no_nvidia, mock_smc):
        """Apple Silicon fan RPM parsed correctly from flt (LE float)."""
        iokit = _make_iokit_mock(mock_smc)

        with patch(f'{MODULE}.IS_APPLE_SILICON', True), \
             patch(f'{MODULE}._iokit', iokit), \
             patch(f'{MODULE}.subprocess') as sub, \
             patch(f'{MODULE}.ctypes') as mock_ctypes_mod:
            mc = _make_mock_ctypes()
            for attr in dir(mc):
                if not attr.startswith('_'):
                    setattr(mock_ctypes_mod, attr, getattr(mc, attr))
            sub.run.side_effect = _make_subprocess_side_effect()
            from trcc.adapters.system.macos_platform import SensorEnumerator
            e = SensorEnumerator()
            e.discover()
            readings = e.read_all()

        assert abs(readings['smc:F0Ac'] - 1200.0) < 0.1
        assert abs(readings['smc:F1Ac'] - 1350.0) < 0.1


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
            from trcc.adapters.system.macos_platform import SensorEnumerator
            e = SensorEnumerator()
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
            from trcc.adapters.system.macos_platform import SensorEnumerator
            e = SensorEnumerator()
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
            from trcc.adapters.system.macos_platform import SensorEnumerator
            e = SensorEnumerator()
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
        from trcc.adapters.system.macos_platform import _parse_smc_bytes
        dt = struct.unpack('>I', b'sp78')[0]
        raw = (ctypes.c_uint8 * 32)()
        val = struct.pack('>h', int(45.5 * 256))
        raw[0], raw[1] = val[0], val[1]
        result = _parse_smc_bytes(dt, raw, 2)
        assert abs(result - 45.5) < 0.01

    def test_fpe2_fan_speed(self):
        from trcc.adapters.system.macos_platform import _parse_smc_bytes
        dt = struct.unpack('>I', b'fpe2')[0]
        raw = (ctypes.c_uint8 * 32)()
        val = struct.pack('>H', int(1200 * 4))
        raw[0], raw[1] = val[0], val[1]
        result = _parse_smc_bytes(dt, raw, 2)
        assert result == 1200.0

    def test_flt_little_endian(self):
        """flt type uses little-endian IEEE 754 on all Macs."""
        from trcc.adapters.system.macos_platform import _parse_smc_bytes
        dt = struct.unpack('>I', b'flt ')[0]
        raw = (ctypes.c_uint8 * 32)()
        val = struct.pack('<f', 1337.5)
        for i, b in enumerate(val):
            raw[i] = b
        result = _parse_smc_bytes(dt, raw, 4)
        assert abs(result - 1337.5) < 0.1

    def test_flt_fan_rpm(self):
        """Fan RPM via flt type (Apple Silicon pattern)."""
        from trcc.adapters.system.macos_platform import _parse_smc_bytes
        dt = struct.unpack('>I', b'flt ')[0]
        raw = (ctypes.c_uint8 * 32)()
        val = struct.pack('<f', 1200.0)
        for i, b in enumerate(val):
            raw[i] = b
        result = _parse_smc_bytes(dt, raw, 4)
        assert abs(result - 1200.0) < 0.1


class TestASKeyMetadata:
    """_as_key_metadata derives names from Apple Silicon key prefixes."""

    def test_cpu_pcore(self):
        from trcc.adapters.system.macos_platform import _as_key_metadata
        name, cat, unit = _as_key_metadata('Tp0a')
        assert 'CPU P-Core' in name
        assert cat == 'temperature'
        assert unit == '°C'

    def test_cpu_ecore(self):
        from trcc.adapters.system.macos_platform import _as_key_metadata
        name, cat, unit = _as_key_metadata('Te04')
        assert 'CPU E-Core' in name
        assert cat == 'temperature'

    def test_gpu_die(self):
        from trcc.adapters.system.macos_platform import _as_key_metadata
        name, cat, unit = _as_key_metadata('Tg0K')
        assert 'GPU Die' in name
        assert cat == 'temperature'

    def test_die_fabric(self):
        from trcc.adapters.system.macos_platform import _as_key_metadata
        name, cat, unit = _as_key_metadata('Tf14')
        assert 'Die Fabric' in name
        assert cat == 'temperature'

    def test_memory(self):
        from trcc.adapters.system.macos_platform import _as_key_metadata
        name, cat, unit = _as_key_metadata('Tm1p')
        assert 'Memory' in name
        assert cat == 'temperature'
