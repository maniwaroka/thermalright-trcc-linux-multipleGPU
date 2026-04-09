"""Tests for macOS sensor enumerator — platform-specific behavior only.

Shared base behavior (psutil, nvidia, computed I/O, polling, read_all)
is tested in tests/adapters/system/conftest.py.

Tests follow the app flow: discover() → read_all() → map_defaults().
Mock at I/O boundary only: subprocess for powermetrics/diskutil, _iokit.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

MODULE = 'trcc.adapters.system.macos.sensors'

POWERMETRICS_OUTPUT = (
    "CPU die temperature: 45.23 C\n"
    "GPU die temperature: 52.1 C\n"
    "SOC temperature: 38.0 C\n"
    "Fan: 1200 rpm\n"
    "Fan: 1350 rpm\n"
    "CPU 0 frequency: 1690 MHz\n"
    "CPU 4 frequency: 2937 MHz\n"
    "GPU active residency: 31%\n"
    "GPU Power: 4.5 W\n"
    "GPU HW active frequency: 1398 MHz\n"
)

DISKUTIL_OUTPUT = (
    "APFS Container Reference:     disk1\n"
    "Size (Capacity Ceiling):      500107862016 B (500.1 GB)\n"
    "Minimum Size:                 N/A\n"
    "Capacity In Use By Volumes:   400086323200 B (400.1 GB)\n"
    "Capacity Not Allocated:       100021538816 B (100.0 GB)\n"
)


@pytest.fixture
def mock_macos(mock_io_no_nvidia):
    """macOS Apple Silicon enumerator with mocked subprocess."""
    with patch(f'{MODULE}.IS_APPLE_SILICON', True), \
         patch(f'{MODULE}._iokit', None), \
         patch(f'{MODULE}.subprocess') as sub:
        # powermetrics for temps/GPU, diskutil for disk percent
        def _run_side_effect(cmd, **kwargs):
            if 'powermetrics' in cmd:
                return MagicMock(stdout=POWERMETRICS_OUTPUT)
            if 'diskutil' in cmd:
                return MagicMock(stdout=DISKUTIL_OUTPUT)
            return MagicMock(stdout='')
        sub.run.side_effect = _run_side_effect
        mock_io_no_nvidia.subprocess = sub
        yield mock_io_no_nvidia


@pytest.fixture
def mock_macos_intel(mock_io_no_nvidia):
    """macOS Intel enumerator with mocked IOKit."""
    with patch(f'{MODULE}.IS_APPLE_SILICON', False), \
         patch(f'{MODULE}._iokit', None), \
         patch(f'{MODULE}.subprocess') as sub:
        sub.run.return_value = MagicMock(stdout='')
        mock_io_no_nvidia.subprocess = sub
        yield mock_io_no_nvidia


@pytest.fixture
def enum(mock_macos):
    """Discovered macOS Apple Silicon enumerator."""
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
    """macOS discover() registers Apple Silicon / Intel sensors."""

    def test_apple_silicon_sensors_registered(self, enum):
        ids = [s.id for s in enum.get_sensors()]
        assert 'iokit:cpu_die' in ids
        assert 'iokit:gpu_die' in ids
        assert 'iokit:soc' in ids
        assert 'iokit:gpu_busy' in ids
        assert 'iokit:gpu_clock' in ids
        assert 'iokit:gpu_power' in ids
        assert 'iokit:fan0' in ids
        assert 'iokit:fan1' in ids

    def test_base_sensors_also_registered(self, enum):
        ids = [s.id for s in enum.get_sensors()]
        assert 'psutil:cpu_percent' in ids
        assert 'computed:disk_read' in ids
        assert 'computed:date_year' in ids

    def test_intel_no_smc_without_iokit(self, enum_intel):
        """Intel Mac without IOKit — no SMC sensors, psutil still works."""
        sensors = enum_intel.get_sensors()
        assert not any(s.source == 'smc' for s in sensors)
        assert any(s.source == 'psutil' for s in sensors)

    def test_sources_include_iokit(self, enum):
        sources = {s.source for s in enum.get_sensors()}
        assert 'iokit' in sources
        assert 'psutil' in sources
        assert 'computed' in sources


class TestReadAll:
    """macOS read_all() returns powermetrics-parsed readings."""

    def test_apple_silicon_temps(self, enum):
        readings = enum.read_all()
        assert readings['iokit:cpu_die'] == 45.23
        assert readings['iokit:gpu_die'] == 52.1
        assert readings['iokit:soc'] == 38.0

    def test_apple_silicon_fans(self, enum):
        readings = enum.read_all()
        assert readings['iokit:fan0'] == 1200.0
        assert readings['iokit:fan1'] == 1350.0

    def test_apple_silicon_gpu_metrics(self, enum):
        readings = enum.read_all()
        assert readings['iokit:gpu_busy'] == 31.0
        assert readings['iokit:gpu_power'] == 4.5
        assert readings['iokit:gpu_clock'] == 1398.0

    def test_cpu_freq_from_powermetrics(self, enum):
        """Per-core max freq overrides psutil:cpu_freq."""
        readings = enum.read_all()
        assert readings['psutil:cpu_freq'] == 2937.0

    def test_apfs_disk_percent(self, enum):
        readings = enum.read_all()
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
            assert 'iokit:cpu_die' not in readings

    def test_diskutil_failure_falls_back_to_psutil(self, mock_io_no_nvidia):
        with patch(f'{MODULE}.IS_APPLE_SILICON', True), \
             patch(f'{MODULE}._iokit', None), \
             patch(f'{MODULE}.subprocess') as sub, \
             patch(f'{MODULE}.psutil') as mac_psutil:
            def _run(cmd, **kwargs):
                if 'diskutil' in cmd:
                    raise FileNotFoundError("no diskutil")
                return MagicMock(stdout=POWERMETRICS_OUTPUT)
            sub.run.side_effect = _run
            mac_psutil.disk_usage.return_value = MagicMock(percent=55.0)
            from trcc.adapters.system.macos.sensors import MacOSSensorEnumerator
            e = MacOSSensorEnumerator()
            e.discover()
            readings = e.read_all()
            assert readings['computed:disk_percent'] == 55.0


class TestMapDefaults:
    """macOS map_defaults() routes to iokit/smc sources."""

    def test_apple_silicon_gpu_mapping(self, enum):
        mapping = enum.map_defaults()
        assert mapping['gpu_usage'] == 'iokit:gpu_busy'
        assert mapping['gpu_clock'] == 'iokit:gpu_clock'
        assert mapping['gpu_power'] == 'iokit:gpu_power'

    def test_mem_temp_falls_back_to_soc(self, enum):
        mapping = enum.map_defaults()
        assert mapping['mem_temp'] == 'iokit:soc'

    def test_mem_available_correct(self, enum):
        mapping = enum.map_defaults()
        assert mapping['mem_available'] == 'psutil:mem_available'

    def test_fan_mapping(self, enum):
        mapping = enum.map_defaults()
        assert mapping['fan_cpu'] == 'iokit:fan0'
        assert mapping['fan_gpu'] == 'iokit:fan1'

    def test_common_mappings_present(self, enum):
        mapping = enum.map_defaults()
        assert mapping['disk_activity'] == 'computed:disk_activity'
        assert mapping['net_total_up'] == 'computed:net_total_up'
        assert mapping['net_total_down'] == 'computed:net_total_down'


class TestParseMetric:
    """_parse_metric helper — the only non-public function worth testing."""

    def test_temperature(self):
        from trcc.adapters.system.macos.sensors import _parse_metric
        assert _parse_metric('CPU die temperature: 45.23 C') == 45.23

    def test_fan(self):
        from trcc.adapters.system.macos.sensors import _parse_metric
        assert _parse_metric('Fan: 1200 rpm') == 1200.0

    def test_no_number(self):
        from trcc.adapters.system.macos.sensors import _parse_metric
        assert _parse_metric('no numbers here') == 0.0
