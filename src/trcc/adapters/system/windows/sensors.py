"""Windows hardware sensor discovery and reading.

Replaces Linux hwmon/RAPL/DRM with Windows-native sensor sources:
- LibreHardwareMonitor (LHM): GPU hotspot, memory junction, voltage — via
  undocumented NVAPI calls only available on Windows. Primary GPU source.
- pynvml: NVIDIA GPU fallback (cross-platform, fewer sensors)
- psutil: CPU usage/frequency, memory, disk I/O, network I/O (cross-platform)
- WMI: thermal zones, fan speeds

Sensor IDs follow the same format as Linux for compatibility:
    lhm:{hardware}:{sensor}    e.g., lhm:gpu0:hotspot
    nvidia:{gpu}:{metric}      e.g., nvidia:0:temp
    psutil:{metric}            e.g., psutil:cpu_percent
    wmi:{class}:{property}     e.g., wmi:thermal:zone0
    computed:{metric}           e.g., computed:disk_read
"""
from __future__ import annotations

import datetime
import logging
import threading
from typing import Any, Optional

import psutil

from trcc.core.models import SensorInfo

# ── Optional: LibreHardwareMonitor via pythonnet ──────────────────────
# pip install HardwareMonitor (requires .NET 4.7, admin for full access)
try:
    from HardwareMonitor.Hardware import Computer  # pyright: ignore[reportMissingImports]
    LHM_AVAILABLE = True
except Exception:
    LHM_AVAILABLE = False

# ── Optional: pynvml (fallback when LHM unavailable) ─────────────────
try:
    import pynvml  # pyright: ignore[reportMissingImports]
    pynvml.nvmlInit()
    NVML_AVAILABLE = True
except Exception:
    pynvml = None  # type: ignore[assignment]
    NVML_AVAILABLE = False

log = logging.getLogger(__name__)

# LHM SensorType → our category mapping
_LHM_TYPE_MAP: dict[str, tuple[str, str]] = {
    'Temperature': ('temperature', '°C'),
    'Fan': ('fan', 'RPM'),
    'Clock': ('clock', 'MHz'),
    'Load': ('usage', '%'),
    'Power': ('power', 'W'),
    'Voltage': ('voltage', 'V'),
    'SmallData': ('memory', 'MB'),
    'Data': ('memory', 'GB'),
    'Throughput': ('throughput', 'B/s'),
}


class WindowsSensorEnumerator:
    """Discovers and reads hardware sensors on Windows.

    Same interface as Linux SensorEnumerator — drop-in replacement.

    Sensor priority for GPU:
    1. LibreHardwareMonitor — hotspot temp, memory junction temp, voltage
       (Windows-exclusive via NVAPI undocumented calls)
    2. pynvml fallback — basic temp, utilization, clock, power, fan, VRAM
    """

    def __init__(self) -> None:
        self._sensors: list[SensorInfo] = []
        self._readings: dict[str, float] = {}
        self._lock = threading.Lock()
        self._poll_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lhm_computer: Any = None  # LHM Computer instance (kept alive)
        self._lhm_gpu_used = False  # True if LHM handled GPU discovery

    def discover(self) -> list[SensorInfo]:
        """Scan system for all available sensors."""
        self._sensors.clear()
        self._lhm_gpu_used = False
        self._discover_psutil()
        self._discover_lhm()
        if not self._lhm_gpu_used:
            self._discover_nvidia()
        self._discover_wmi()
        self._discover_computed()
        log.info("Windows sensor discovery: %d sensors", len(self._sensors))
        return self._sensors

    def get_sensors(self) -> list[SensorInfo]:
        """Return previously discovered sensors."""
        return self._sensors

    def get_by_category(self, category: str) -> list[SensorInfo]:
        """Filter sensors by category."""
        return [s for s in self._sensors if s.category == category]

    def read_all(self) -> dict[str, float]:
        """Return current readings."""
        with self._lock:
            return dict(self._readings)

    def start_polling(self, interval: float = 2.0) -> None:
        """Start background polling thread."""
        if self._poll_thread and self._poll_thread.is_alive():
            return
        self._stop_event.clear()

        def _poll() -> None:
            while not self._stop_event.is_set():
                self._poll_once()
                self._stop_event.wait(interval)

        self._poll_thread = threading.Thread(
            target=_poll, daemon=True, name="win-sensors",
        )
        self._poll_thread.start()

    def stop_polling(self) -> None:
        """Stop background polling."""
        self._stop_event.set()
        if self._poll_thread:
            self._poll_thread.join(timeout=3)
        if self._lhm_computer is not None:
            try:
                self._lhm_computer.Close()
            except Exception:
                pass
            self._lhm_computer = None

    # ── Discovery methods ──────────────────────────────────────────

    def _discover_psutil(self) -> None:
        """Add CPU, memory, disk, network sensors via psutil."""
        self._sensors.extend([
            SensorInfo('psutil:cpu_percent', 'CPU Usage', 'cpu_percent', '%', 'psutil'),
            SensorInfo('psutil:cpu_freq', 'CPU Frequency', 'clock', 'MHz', 'psutil'),
        ])

        # CPU temperature (Windows: requires admin or LHM)
        temps = psutil.sensors_temperatures() if hasattr(psutil, 'sensors_temperatures') else {}
        for chip, entries in temps.items():
            for i, entry in enumerate(entries):
                sid = f'psutil:temp:{chip}:{i}'
                label = entry.label or f'{chip} temp{i}'
                self._sensors.append(
                    SensorInfo(sid, label, 'temperature', '°C', 'psutil'),
                )

        # Memory
        self._sensors.extend([
            SensorInfo('psutil:mem_used', 'Memory Used', 'memory', 'MB', 'psutil'),
            SensorInfo('psutil:mem_total', 'Memory Total', 'memory', 'MB', 'psutil'),
            SensorInfo('psutil:mem_percent', 'Memory Usage', 'memory', '%', 'psutil'),
        ])

        # Disk I/O
        self._sensors.extend([
            SensorInfo('computed:disk_read', 'Disk Read', 'disk_io', 'MB/s', 'computed'),
            SensorInfo('computed:disk_write', 'Disk Write', 'disk_io', 'MB/s', 'computed'),
        ])

        # Network I/O
        self._sensors.extend([
            SensorInfo('computed:net_up', 'Network Upload', 'network_io', 'KB/s', 'computed'),
            SensorInfo('computed:net_down', 'Network Download', 'network_io', 'KB/s', 'computed'),
        ])

    def _discover_lhm(self) -> None:
        """Discover sensors via LibreHardwareMonitor.

        LHM uses undocumented NVAPI calls to expose Windows-exclusive sensors:
        - GPU Hotspot Temperature (thermal throttle point)
        - GPU Memory Junction Temperature (GDDR6X VRAM)
        - GPU Core Voltage
        - Per-component power breakdown
        These are NOT available via pynvml or nvidia-smi.
        """
        if not LHM_AVAILABLE:
            return
        try:
            computer = Computer()
            computer.IsGpuEnabled = True
            computer.IsCpuEnabled = True
            computer.IsMotherboardEnabled = True
            computer.Open()
            self._lhm_computer = computer  # Keep alive for polling

            for hw in computer.Hardware:
                hw.Update()
                hw_type = str(hw.HardwareType)
                hw_name = str(hw.Name)

                # Track if LHM found GPU hardware
                if 'Gpu' in hw_type:
                    self._lhm_gpu_used = True

                source = 'lhm'
                hw_key = hw_name.lower().replace(' ', '_')[:20]

                for sensor in hw.Sensors:
                    s_type = str(sensor.SensorType)
                    s_name = str(sensor.Name)
                    mapping = _LHM_TYPE_MAP.get(s_type)
                    if not mapping:
                        continue
                    category, unit = mapping
                    sid = f'lhm:{hw_key}:{s_name.lower().replace(" ", "_")}'
                    self._sensors.append(
                        SensorInfo(sid, f'{hw_name} {s_name}', category, unit, source),
                    )

                # SubHardware (e.g., individual CPU cores)
                for sub in hw.SubHardware:
                    sub.Update()
                    sub_name = str(sub.Name)
                    sub_key = sub_name.lower().replace(' ', '_')[:20]
                    for sensor in sub.Sensors:
                        s_type = str(sensor.SensorType)
                        s_name = str(sensor.Name)
                        mapping = _LHM_TYPE_MAP.get(s_type)
                        if not mapping:
                            continue
                        category, unit = mapping
                        sid = f'lhm:{sub_key}:{s_name.lower().replace(" ", "_")}'
                        self._sensors.append(
                            SensorInfo(sid, f'{sub_name} {s_name}', category, unit, source),
                        )

            log.info("LHM discovery: %d sensors (GPU via NVAPI: %s)",
                     len(self._sensors), self._lhm_gpu_used)

        except Exception:
            log.debug("LibreHardwareMonitor discovery failed", exc_info=True)

    def _discover_nvidia(self) -> None:
        """Probe NVIDIA GPU via pynvml (fallback when LHM unavailable).

        pynvml provides basic sensors. LHM is preferred because it also
        exposes hotspot temp, memory junction temp, and voltage via NVAPI.
        """
        if not NVML_AVAILABLE or pynvml is None:
            return
        try:
            count = pynvml.nvmlDeviceGetCount()
            for i in range(count):
                handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                name = pynvml.nvmlDeviceGetName(handle)
                if isinstance(name, bytes):
                    name = name.decode()
                name = str(name)
                prefix = f'nvidia:{i}'
                self._sensors.extend([
                    SensorInfo(f'{prefix}:temp', f'{name} Temp', 'temperature', '°C', 'nvidia'),
                    SensorInfo(f'{prefix}:gpu_busy', f'{name} Usage', 'gpu_busy', '%', 'nvidia'),
                    SensorInfo(f'{prefix}:clock', f'{name} Clock', 'clock', 'MHz', 'nvidia'),
                    SensorInfo(f'{prefix}:power', f'{name} Power', 'power', 'W', 'nvidia'),
                    SensorInfo(f'{prefix}:fan', f'{name} Fan', 'fan', '%', 'nvidia'),
                    SensorInfo(f'{prefix}:mem_used', f'{name} VRAM Used', 'gpu_memory', 'MB', 'nvidia'),
                    SensorInfo(f'{prefix}:mem_total', f'{name} VRAM Total', 'gpu_memory', 'MB', 'nvidia'),
                ])
        except Exception:
            log.debug("NVIDIA GPU probe failed")

    def _discover_wmi(self) -> None:
        """Discover sensors via WMI (thermal zones)."""
        try:
            import wmi  # pyright: ignore[reportMissingImports]
            w = wmi.WMI(namespace='root\\WMI')

            # MSAcpi_ThermalZoneTemperature (requires admin)
            try:
                for tz in w.MSAcpi_ThermalZoneTemperature():
                    sid = f'wmi:thermal:{tz.InstanceName}'
                    self._sensors.append(
                        SensorInfo(sid, 'Thermal Zone', 'temperature', '°C', 'wmi'),
                    )
            except Exception:
                pass  # Requires admin elevation

        except ImportError:
            log.debug("wmi package not available")
        except Exception:
            log.debug("WMI sensor discovery failed")

    def _discover_computed(self) -> None:
        """Add computed/derived metrics."""
        for metric in ('date_year', 'date_month', 'date_day',
                       'time_hour', 'time_minute', 'time_second',
                       'day_of_week'):
            self._sensors.append(
                SensorInfo(f'computed:{metric}', metric, 'datetime', '', 'computed'),
            )

    # ── Reading methods ────────────────────────────────────────────

    def _poll_once(self) -> None:
        """Read all sensors once and update cached readings."""
        readings: dict[str, float] = {}

        # psutil: CPU
        readings['psutil:cpu_percent'] = psutil.cpu_percent(interval=None)
        freq = psutil.cpu_freq()
        if freq:
            readings['psutil:cpu_freq'] = freq.current

        # psutil: Memory
        mem = psutil.virtual_memory()
        readings['psutil:mem_used'] = mem.used / (1024 * 1024)
        readings['psutil:mem_total'] = mem.total / (1024 * 1024)
        readings['psutil:mem_percent'] = mem.percent

        # psutil: CPU temperatures (if available)
        if hasattr(psutil, 'sensors_temperatures'):
            temps = psutil.sensors_temperatures()
            for chip, entries in temps.items():
                for i, entry in enumerate(entries):
                    readings[f'psutil:temp:{chip}:{i}'] = entry.current

        # LibreHardwareMonitor — reads all LHM sensors including
        # GPU hotspot, memory junction, voltage (NVAPI-exclusive)
        if self._lhm_computer is not None:
            self._poll_lhm(readings)
        elif NVML_AVAILABLE and pynvml is not None:
            self._poll_nvidia(readings)

        # Date/time
        now = datetime.datetime.now()
        readings['computed:date_year'] = float(now.year)
        readings['computed:date_month'] = float(now.month)
        readings['computed:date_day'] = float(now.day)
        readings['computed:time_hour'] = float(now.hour)
        readings['computed:time_minute'] = float(now.minute)
        readings['computed:time_second'] = float(now.second)
        readings['computed:day_of_week'] = float(now.weekday())

        with self._lock:
            self._readings = readings

    def _poll_lhm(self, readings: dict[str, float]) -> None:
        """Read all sensors from LibreHardwareMonitor."""
        try:
            for hw in self._lhm_computer.Hardware:
                hw.Update()
                hw_name = str(hw.Name)
                hw_key = hw_name.lower().replace(' ', '_')[:20]

                for sensor in hw.Sensors:
                    val = sensor.Value
                    if val is None:
                        continue
                    s_name = str(sensor.Name).lower().replace(' ', '_')
                    readings[f'lhm:{hw_key}:{s_name}'] = float(val)

                for sub in hw.SubHardware:
                    sub.Update()
                    sub_name = str(sub.Name)
                    sub_key = sub_name.lower().replace(' ', '_')[:20]
                    for sensor in sub.Sensors:
                        val = sensor.Value
                        if val is None:
                            continue
                        s_name = str(sensor.Name).lower().replace(' ', '_')
                        readings[f'lhm:{sub_key}:{s_name}'] = float(val)
        except Exception:
            log.debug("LHM poll failed", exc_info=True)

    def _poll_nvidia(self, readings: dict[str, float]) -> None:
        """Read NVIDIA GPU sensors via pynvml (fallback)."""
        if pynvml is None:
            return
        try:
            count = pynvml.nvmlDeviceGetCount()
            for i in range(count):
                handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                prefix = f'nvidia:{i}'
                try:
                    readings[f'{prefix}:temp'] = float(
                        pynvml.nvmlDeviceGetTemperature(
                            handle, pynvml.NVML_TEMPERATURE_GPU))
                except Exception:
                    pass
                try:
                    util = pynvml.nvmlDeviceGetUtilizationRates(handle)
                    readings[f'{prefix}:gpu_busy'] = float(util.gpu)
                except Exception:
                    pass
                try:
                    readings[f'{prefix}:clock'] = float(
                        pynvml.nvmlDeviceGetClockInfo(
                            handle, pynvml.NVML_CLOCK_GRAPHICS))
                except Exception:
                    pass
                try:
                    readings[f'{prefix}:power'] = (
                        float(pynvml.nvmlDeviceGetPowerUsage(handle)) / 1000.0)
                except Exception:
                    pass
                try:
                    readings[f'{prefix}:fan'] = float(
                        pynvml.nvmlDeviceGetFanSpeed(handle))
                except Exception:
                    pass
                try:
                    mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                    readings[f'{prefix}:mem_used'] = float(mem_info.used) / (1024 * 1024)
                    readings[f'{prefix}:mem_total'] = float(mem_info.total) / (1024 * 1024)
                except Exception:
                    pass
        except Exception:
            pass
