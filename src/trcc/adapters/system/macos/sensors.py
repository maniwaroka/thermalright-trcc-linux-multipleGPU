"""macOS hardware sensor discovery and reading.

Platform-specific sources:
- IOKit SMC: CPU/GPU temp, fan speed (Intel + Apple Silicon — direct ctypes)
- powermetrics: GPU active residency, power, clock (Apple Silicon only)
- psutil: CPU usage/frequency, memory, disk I/O, network I/O
- pynvml: NVIDIA GPU (rare on Mac, eGPU only)

Sensor IDs follow the same format as Linux for compatibility:
    smc:{key}              e.g., smc:TC0P (CPU temp)
    iokit:{sensor}         e.g., iokit:gpu_busy (GPU active residency)
    psutil:{metric}        e.g., psutil:cpu_percent
    nvidia:{gpu}:{metric}  e.g., nvidia:0:temp
    computed:{metric}      e.g., computed:disk_read
"""
from __future__ import annotations

import ctypes
import ctypes.util
import logging
import platform
import re
import struct
import subprocess

import psutil

from trcc.adapters.system._base import SensorEnumeratorBase
from trcc.core.models import SensorInfo

log = logging.getLogger(__name__)

# Detect Apple Silicon vs Intel
IS_APPLE_SILICON = platform.machine() == 'arm64'

# ── IOKit framework bindings ─────────────────────────────────────────

_iokit_path = ctypes.util.find_library('IOKit')
_cf_path = ctypes.util.find_library('CoreFoundation')
_iokit = ctypes.cdll.LoadLibrary(_iokit_path) if _iokit_path else None
_cf = ctypes.cdll.LoadLibrary(_cf_path) if _cf_path else None

if _iokit:
    _iokit.IOServiceMatching.restype = ctypes.c_void_p
    _iokit.IOServiceMatching.argtypes = [ctypes.c_char_p]
    _iokit.IOServiceGetMatchingService.restype = ctypes.c_uint
    _iokit.IOServiceGetMatchingService.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    _iokit.IOServiceOpen.restype = ctypes.c_int
    _iokit.IOServiceOpen.argtypes = [
        ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,
        ctypes.POINTER(ctypes.c_uint),
    ]
    _iokit.IOConnectCallStructMethod.restype = ctypes.c_int
    _iokit.IOServiceClose.restype = ctypes.c_int
    _iokit.IOServiceClose.argtypes = [ctypes.c_uint]


# ── SMC constants ────────────────────────────────────────────────────

KERNEL_INDEX_SMC = 2
SMC_CMD_READ_KEYINFO = 9
SMC_CMD_READ_BYTES = 5

# SMC key table — Intel + Apple Silicon.
# Discovery probes every key; only those returning valid values are registered.
# Apple Silicon keys vary by chip generation — trial-and-error handles this.
_SMC_KEYS: dict[str, tuple[str, str, str]] = {
    # Intel CPU temps
    'TC0P': ('CPU Proximity', 'temperature', '°C'),
    'TC0D': ('CPU Die', 'temperature', '°C'),
    'TC0E': ('CPU Core 0', 'temperature', '°C'),
    'TC1C': ('CPU Core 1', 'temperature', '°C'),
    'TC2C': ('CPU Core 2', 'temperature', '°C'),
    'TC3C': ('CPU Core 3', 'temperature', '°C'),
    # Apple Silicon CPU temps (performance cores)
    'Tp01': ('CPU P-Core 1', 'temperature', '°C'),
    'Tp02': ('CPU P-Core 2', 'temperature', '°C'),
    'Tp05': ('CPU P-Core 5', 'temperature', '°C'),
    'Tp09': ('CPU P-Core 9', 'temperature', '°C'),
    'Tp0T': ('CPU Package', 'temperature', '°C'),
    # Intel GPU temps
    'TG0P': ('GPU Proximity', 'temperature', '°C'),
    'TG0D': ('GPU Die', 'temperature', '°C'),
    # Apple Silicon GPU temps
    'Tg04': ('GPU Die 0', 'temperature', '°C'),
    'Tg05': ('GPU Die 1', 'temperature', '°C'),
    'Tg0f': ('GPU Die', 'temperature', '°C'),
    'Tg0j': ('GPU Die', 'temperature', '°C'),
    # Memory temps
    'Tm0P': ('Memory Proximity', 'temperature', '°C'),
    'Tm00': ('Memory Bank 0', 'temperature', '°C'),
    'Tm01': ('Memory Bank 1', 'temperature', '°C'),
    # Fans (same on Intel + Apple Silicon)
    'F0Ac': ('Fan 0', 'fan', 'RPM'),
    'F1Ac': ('Fan 1', 'fan', 'RPM'),
    'F2Ac': ('Fan 2', 'fan', 'RPM'),
    'F3Ac': ('Fan 3', 'fan', 'RPM'),
    # Misc Intel
    'TN0P': ('Northbridge', 'temperature', '°C'),
    'TB0T': ('Battery', 'temperature', '°C'),
}

# ── SMC structures ───────────────────────────────────────────────────


class SMCKeyData_vers_t(ctypes.Structure):
    _fields_ = [
        ('major', ctypes.c_uint8),
        ('minor', ctypes.c_uint8),
        ('build', ctypes.c_uint8),
        ('reserved', ctypes.c_uint8),
        ('release', ctypes.c_uint16),
    ]


class SMCKeyData_pLimitData_t(ctypes.Structure):
    _fields_ = [
        ('version', ctypes.c_uint16),
        ('length', ctypes.c_uint16),
        ('cpuPLimit', ctypes.c_uint32),
        ('gpuPLimit', ctypes.c_uint32),
        ('memPLimit', ctypes.c_uint32),
    ]


class SMCKeyData_keyInfo_t(ctypes.Structure):
    _fields_ = [
        ('dataSize', ctypes.c_uint32),
        ('dataType', ctypes.c_uint32),
        ('dataAttributes', ctypes.c_uint8),
    ]


class SMCKeyData_t(ctypes.Structure):
    _fields_ = [
        ('key', ctypes.c_uint32),
        ('vers', SMCKeyData_vers_t),
        ('pLimitData', SMCKeyData_pLimitData_t),
        ('keyInfo', SMCKeyData_keyInfo_t),
        ('result', ctypes.c_uint8),
        ('status', ctypes.c_uint8),
        ('data8', ctypes.c_uint8),
        ('data32', ctypes.c_uint32),
        ('bytes', ctypes.c_uint8 * 32),
    ]


# ── SMC byte parsing ────────────────────────────────────────────────

def _smc_key_to_int(key: str) -> int:
    """Convert 4-char SMC key to uint32."""
    return struct.unpack('>I', key.encode('ascii'))[0]


def _datatype_to_str(dt: int) -> str:
    """Convert dataType uint32 to 4-char string."""
    return struct.pack('>I', dt).decode('ascii', errors='replace')


def _parse_smc_bytes(data_type: int, raw: ctypes.Array, size: int) -> float:
    """Parse SMC raw bytes based on data type code."""
    dt = _datatype_to_str(data_type)
    b = bytes(raw[:size])
    if len(b) < 2:
        return float(b[0]) if b else 0.0

    match dt.rstrip():
        case 'sp78':  # signed 8.8 fixed-point (temps)
            return struct.unpack('>h', b[:2])[0] / 256.0
        case 'fpe2':  # unsigned 14.2 fixed-point (fan RPM)
            return struct.unpack('>H', b[:2])[0] / 4.0
        case 'flt':   # IEEE 754 float
            return struct.unpack('>f', b[:4])[0] if len(b) >= 4 else 0.0
        case 'ui8':
            return float(b[0])
        case 'ui16':
            return float(struct.unpack('>H', b[:2])[0])
        case 'ui32' if len(b) >= 4:
            return float(struct.unpack('>I', b[:4])[0])
        case 'fp1f':  # 1.15 fixed-point
            return struct.unpack('>H', b[:2])[0] / 32768.0
        case _:
            # Best effort: treat as big-endian unsigned
            return float(struct.unpack('>H', b[:2])[0]) / 256.0


# ── Enumerator ───────────────────────────────────────────────────────

class MacOSSensorEnumerator(SensorEnumeratorBase):
    """Discovers and reads hardware sensors on macOS.

    All Macs: reads SMC directly via IOKit for CPU/GPU temp and fan speed.
    Apple Silicon: additionally reads powermetrics for GPU active residency,
    power, and clock (not available from SMC).
    """

    def __init__(self) -> None:
        super().__init__()
        self._smc_conn: int = 0

    def discover(self) -> list[SensorInfo]:
        self._sensors.clear()
        self._discover_psutil_base()
        self._discover_smc()
        if IS_APPLE_SILICON:
            self._discover_apple_silicon_gpu()
        self._discover_nvidia()
        self._discover_computed()
        log.info("macOS sensor discovery: %d sensors", len(self._sensors))
        return self._sensors

    # ── SMC connection management ────────────────────────────────────

    def _open_smc(self) -> bool:
        """Open connection to AppleSMC IOKit service."""
        if self._smc_conn:
            return True
        if _iokit is None:
            return False
        try:
            matching = _iokit.IOServiceMatching(b"AppleSMC")
            if not matching:
                log.debug("IOServiceMatching('AppleSMC') returned NULL")
                return False
            service = _iokit.IOServiceGetMatchingService(0, matching)
            if not service:
                log.debug("AppleSMC service not found")
                return False
            conn = ctypes.c_uint(0)
            # mach_task_self() — use libc
            libc_path = ctypes.util.find_library('c') or 'libSystem.B.dylib'
            libc = ctypes.cdll.LoadLibrary(libc_path)
            task = libc.mach_task_self()
            ret = _iokit.IOServiceOpen(service, task, 0, ctypes.byref(conn))
            _iokit.IOObjectRelease(service)
            if ret != 0:
                log.warning("IOServiceOpen failed: %d (needs root?)", ret)
                return False
            self._smc_conn = conn.value
            log.info("SMC connection opened")
            return True
        except Exception as e:
            log.warning("SMC open failed: %s", e)
            return False

    def _close_smc(self) -> None:
        """Close SMC connection."""
        if self._smc_conn and _iokit:
            _iokit.IOServiceClose(self._smc_conn)
            self._smc_conn = 0
            log.debug("SMC connection closed")

    def _on_stop(self) -> None:
        """Close SMC connection when polling stops."""
        self._close_smc()

    # ── SMC direct reads ─────────────────────────────────────────────

    def _read_smc_direct(self, key: str) -> float | None:
        """Read a single SMC key via IOKit. Returns None on error."""
        if not self._smc_conn or _iokit is None:
            return None
        try:
            cmd = SMCKeyData_t()
            cmd.key = _smc_key_to_int(key)
            cmd.data8 = SMC_CMD_READ_KEYINFO
            out_size = ctypes.c_ulong(ctypes.sizeof(SMCKeyData_t))

            ret = _iokit.IOConnectCallStructMethod(
                self._smc_conn, KERNEL_INDEX_SMC,
                ctypes.byref(cmd), ctypes.sizeof(cmd),
                ctypes.byref(cmd), ctypes.byref(out_size),
            )
            if ret != 0:
                return None

            data_type = cmd.keyInfo.dataType
            data_size = cmd.keyInfo.dataSize
            if data_size == 0:
                return None

            # Read the actual bytes
            cmd.data8 = SMC_CMD_READ_BYTES
            ret = _iokit.IOConnectCallStructMethod(
                self._smc_conn, KERNEL_INDEX_SMC,
                ctypes.byref(cmd), ctypes.sizeof(cmd),
                ctypes.byref(cmd), ctypes.byref(out_size),
            )
            if ret != 0:
                return None

            return _parse_smc_bytes(data_type, cmd.bytes, data_size)
        except Exception:
            return None

    # ── Discovery ────────────────────────────────────────────────────

    def _discover_smc(self) -> None:
        """Discover SMC sensors — works on both Intel and Apple Silicon.

        Probes every key in _SMC_KEYS; registers only those returning
        valid values. Chip-specific keys that don't exist return None
        and are silently skipped.
        """
        if not self._open_smc():
            log.debug("SMC unavailable — skipping temp/fan sensors")
            return
        for key, (name, category, unit) in _SMC_KEYS.items():
            val = self._read_smc_direct(key)
            if val is not None and val > 0:
                self._sensors.append(
                    SensorInfo(f'smc:{key}', name, category, unit, 'smc'),
                )
        log.info("SMC discovery: %d sensors found",
                 sum(1 for s in self._sensors if s.source == 'smc'))

    def _discover_apple_silicon_gpu(self) -> None:
        """Register Apple Silicon GPU sensors (from powermetrics, not SMC)."""
        for sid, name, category, unit in (
            ('iokit:gpu_busy', 'GPU Usage', 'gpu_busy', '%'),
            ('iokit:gpu_clock', 'GPU Clock', 'clock', 'MHz'),
            ('iokit:gpu_power', 'GPU Power', 'power', 'W'),
        ):
            self._sensors.append(
                SensorInfo(sid, name, category, unit, 'iokit'),
            )

    # ── Polling ──────────────────────────────────────────────────────

    def _poll_once(self) -> None:
        """macOS poll: base psutil + SMC + powermetrics GPU + shared I/O."""
        readings: dict[str, float] = {}
        self._poll_psutil_base(readings)
        readings['computed:disk_percent'] = self._poll_apfs_disk_percent()
        self._poll_computed_io(readings)
        self._poll_smc(readings)
        if IS_APPLE_SILICON:
            self._poll_powermetrics_gpu(readings)
        self._poll_nvidia(readings)
        self._poll_datetime(readings)

        with self._lock:
            self._readings = readings

    def _poll_smc(self, readings: dict[str, float]) -> None:
        """Read all discovered SMC sensors via direct IOKit reads."""
        if not self._smc_conn:
            return
        for sensor in self._sensors:
            if sensor.source != 'smc':
                continue
            key = sensor.id.split(':', 1)[1]
            val = self._read_smc_direct(key)
            if val is not None:
                readings[sensor.id] = val

    def _poll_powermetrics_gpu(self, readings: dict[str, float]) -> None:
        """Read GPU metrics from powermetrics (Apple Silicon only).

        Uses gpu_power sampler only — smc sampler removed in macOS Tahoe.
        """
        try:
            result = subprocess.run(
                ['powermetrics', '--samplers', 'gpu_power',
                 '-n', '1', '-i', '100'],
                capture_output=True, text=True, timeout=5,
            )
            cpu_core_freqs: list[float] = []
            for line in result.stdout.splitlines():
                line = line.strip()
                lo = line.lower()

                # GPU active residency
                if (
                    'iokit:gpu_busy' not in readings
                    and 'gpu' in lo
                    and any(kw in lo for kw in (
                        'active residency', 'usage', 'utilization', 'busy',
                    ))
                ):
                    m = re.search(r'([\d.]+)\s*%', line)
                    if m:
                        v = float(m.group(1))
                        if 0.0 <= v <= 100.0:
                            readings['iokit:gpu_busy'] = v

                # GPU power
                elif (
                    'iokit:gpu_power' not in readings
                    and 'gpu' in lo
                    and 'power' in lo
                ):
                    mw = re.search(r'([\d.]+)\s*mW', line, re.IGNORECASE)
                    w = re.search(r'([\d.]+)\s*W\b', line, re.IGNORECASE)
                    if mw:
                        readings['iokit:gpu_power'] = float(mw.group(1)) / 1000.0
                    elif w:
                        readings['iokit:gpu_power'] = float(w.group(1))

                # GPU clock
                elif (
                    'iokit:gpu_clock' not in readings
                    and 'gpu' in lo
                    and 'mhz' in lo
                ):
                    m = re.search(r'([\d.]+)\s*MHz', line, re.IGNORECASE)
                    if m and 100.0 <= float(m.group(1)) <= 4000.0:
                        readings['iokit:gpu_clock'] = float(m.group(1))

                # Per-core CPU frequency
                else:
                    m = re.match(
                        r'cpu\s+\d+\s+frequency:\s*([\d.]+)\s*mhz', lo,
                    )
                    if m:
                        v = float(m.group(1))
                        if 1.0 <= v <= 8000.0:
                            cpu_core_freqs.append(v)

            if cpu_core_freqs:
                readings['psutil:cpu_freq'] = max(cpu_core_freqs)

        except Exception:
            log.debug("powermetrics failed (needs root)", exc_info=True)

    def _poll_apfs_disk_percent(self) -> float:
        """Read APFS container disk usage via diskutil."""
        try:
            result = subprocess.run(
                ['diskutil', 'apfs', 'list'],
                capture_output=True, text=True, timeout=5,
            )
            capacity = 0
            in_use = 0
            for line in result.stdout.splitlines():
                if 'Size (Capacity Ceiling)' in line:
                    m = re.search(r'(\d+)\s+B', line)
                    if m:
                        capacity = int(m.group(1))
                elif 'Capacity In Use By Volumes' in line:
                    m = re.search(r'(\d+)\s+B', line)
                    if m:
                        in_use = int(m.group(1))
            if capacity > 0:
                return round(in_use / capacity * 100, 1)
        except Exception:
            log.debug("diskutil apfs list failed", exc_info=True)
        return psutil.disk_usage('/').percent

    # ── GPU list ─────────────────────────────────────────────────────

    def get_gpu_list(self) -> list[tuple[str, str]]:
        """Return discovered GPUs on macOS."""
        gpus: list[tuple[str, str]] = []
        # Apple Silicon: single integrated GPU
        if IS_APPLE_SILICON:
            gpus.append(('iokit:gpu', 'Apple Silicon GPU'))
        else:
            # Intel Mac: try system_profiler for GPU name
            try:
                import json as _json
                result = subprocess.run(
                    ['system_profiler', 'SPDisplaysDataType', '-json'],
                    capture_output=True, text=True, timeout=5,
                )
                data = _json.loads(result.stdout)
                for item in data.get('SPDisplaysDataType', []):
                    name = item.get('sppci_model', 'Unknown GPU')
                    vram = item.get('sppci_vram', '')
                    label = f'{name} ({vram})' if vram else name
                    key = f'smc:{name.lower().replace(" ", "_")[:20]}'
                    gpus.append((key, label))
            except Exception:
                gpus.append(('smc:gpu', 'Intel Mac GPU'))
        # NVIDIA eGPU (rare but possible)
        nvidia_gpus = super().get_gpu_list()
        gpus.extend(nvidia_gpus)
        return gpus

    # ── Mapping ──────────────────────────────────────────────────────

    def _build_mapping(self) -> dict[str, str]:
        sensors = self._sensors
        _ff = self._find_first
        mapping: dict[str, str] = {}
        self._map_common(mapping)

        # CPU — SMC keys (Intel or Apple Silicon)
        mapping['cpu_temp'] = (
            _ff(sensors, source='smc', name_contains='CPU', category='temperature')
            or _ff(sensors, source='iokit', name_contains='cpu', category='temperature')
        )

        # GPU — SMC for temp, iokit (powermetrics) for usage/clock/power
        mapping['gpu_temp'] = (
            _ff(sensors, source='smc', name_contains='GPU', category='temperature')
            or _ff(sensors, source='iokit', name_contains='gpu', category='temperature')
            or _ff(sensors, source='nvidia', category='temperature')
        )
        mapping['gpu_usage'] = (
            _ff(sensors, source='nvidia', category='gpu_busy')
            or _ff(sensors, source='iokit', category='gpu_busy')
        )
        mapping['gpu_clock'] = _ff(sensors, source='iokit', category='clock')
        mapping['gpu_power'] = (
            _ff(sensors, source='nvidia', category='power')
            or _ff(sensors, source='iokit', category='power')
        )

        # Memory — SMC
        mapping['mem_temp'] = (
            _ff(sensors, source='smc', name_contains='Memory', category='temperature')
        )

        # Fans — SMC
        self._map_fans(mapping, fan_sources=('smc', 'iokit', 'nvidia'))

        return mapping


def _parse_metric(line: str) -> float:
    """Extract numeric value from powermetrics output line."""
    match = re.search(r'([\d.]+)', line.split(':')[-1])
    if match:
        return float(match.group(1))
    return 0.0
