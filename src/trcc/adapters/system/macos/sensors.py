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

Apple Silicon temperature key list derived from iSMC (GPL-3.0):
    https://github.com/dkorunic/iSMC — smc/sensors.go
    Copyright (c) Dinko Korunic. Used under GPL-3.0.
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

# SMC key table — Intel + Apple Silicon temperature keys.
# Discovery probes every key; only those returning valid values are registered.
# Apple Silicon keys vary by chip generation — trial-and-error handles this.
# Fans are discovered dynamically via FNum (see _discover_fans).
_SMC_KEYS: dict[str, tuple[str, str, str]] = {
    # Intel CPU temps
    'TC0P': ('CPU Proximity', 'temperature', '°C'),
    'TC0D': ('CPU Die', 'temperature', '°C'),
    'TC0E': ('CPU Core 0', 'temperature', '°C'),
    'TC1C': ('CPU Core 1', 'temperature', '°C'),
    'TC2C': ('CPU Core 2', 'temperature', '°C'),
    'TC3C': ('CPU Core 3', 'temperature', '°C'),
    # Apple Silicon CPU temps (performance cores — common across M1-M4)
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
    # Misc Intel
    'TN0P': ('Northbridge', 'temperature', '°C'),
    'TB0T': ('Battery', 'temperature', '°C'),
}

# Apple Silicon extended temperature keys (M1 through M5).
# Derived from iSMC smc/sensors.go (GPL-3.0, Copyright Dinko Korunic).
# Discovery probes all; only keys present on the actual chip are registered.
_AS_TEMP_KEYS: frozenset[str] = frozenset({
    # CPU P-cores (die/cluster temps across M1-M5 variants)
    'Tp00', 'Tp04', 'Tp05', 'Tp06', 'Tp08', 'Tp0C', 'Tp0D', 'Tp0E',
    'Tp0G', 'Tp0K', 'Tp0L', 'Tp0M', 'Tp0O', 'Tp0R', 'Tp0U', 'Tp0W',
    'Tp0X', 'Tp0a', 'Tp0b', 'Tp0c', 'Tp0d', 'Tp0g', 'Tp0h', 'Tp0i',
    'Tp0j', 'Tp0m', 'Tp0n', 'Tp0o', 'Tp0p', 'Tp0u', 'Tp0y',
    'Tp12', 'Tp16', 'Tp1E', 'Tp1F', 'Tp1G', 'Tp1K', 'Tp1Q', 'Tp1R',
    'Tp1S', 'Tp1j', 'Tp1n', 'Tp1t', 'Tp1w', 'Tp1z',
    'Tp22', 'Tp25', 'Tp28', 'Tp2B', 'Tp2E', 'Tp2J', 'Tp2M', 'Tp2Q',
    'Tp2T', 'Tp2W', 'Tp3P', 'Tp3X',
    # CPU package sensors
    'Tpx8', 'Tpx9', 'TpxA', 'TpxB', 'TpxC', 'TpxD',
    # CPU E-cores (efficiency cluster temps)
    'Te04', 'Te05', 'Te06', 'Te09', 'Te0G', 'Te0H', 'Te0I', 'Te0L',
    'Te0P', 'Te0Q', 'Te0R', 'Te0S', 'Te0T', 'Te0U', 'Te0V',
    # GPU dies
    'Tg0G', 'Tg0H', 'Tg0K', 'Tg0L', 'Tg0U', 'Tg0X', 'Tg0d', 'Tg0e',
    'Tg0g', 'Tg0k', 'Tg1U', 'Tg1Y', 'Tg1c', 'Tg1g', 'Tg1k',
    # Die fabric / interconnect
    'Tf14', 'Tf18', 'Tf19', 'Tf1A', 'Tf1D', 'Tf1E',
    'Tf24', 'Tf28', 'Tf29', 'Tf2A', 'Tf2D', 'Tf2E',
    # Memory (lowercase variant keys on some chips)
    'Tm0p', 'Tm1p', 'Tm2p',
})

# Max fan index to probe when FNum is unavailable.
_FNUM_FALLBACK = 4


def _as_key_metadata(key: str) -> tuple[str, str, str]:
    """Derive (name, category, unit) for an Apple Silicon temp key."""
    suffix = key[2:]
    match key[:2]:
        case 'Tp':
            name = f'CPU P-Core {suffix}' if suffix != 'x' else f'CPU Package {suffix}'
        case 'Te':
            name = f'CPU E-Core {suffix}'
        case 'Tg':
            name = f'GPU Die {suffix}'
        case 'Tf':
            name = f'Die Fabric {suffix}'
        case 'Tm':
            name = f'Memory {suffix}'
        case _:
            name = f'Sensor {key}'
    return (name, 'temperature', '°C')

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
        case 'flt':   # IEEE 754 float (little-endian on all Macs)
            return struct.unpack('<f', b[:4])[0] if len(b) >= 4 else 0.0
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
            log.debug("SMC unknown data type '%s' (size=%d) — fallback BE uint",
                      dt.rstrip(), size)
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
                log.debug("SMC KEYINFO %s: IOKit returned %d", key, ret)
                return None

            data_type = cmd.keyInfo.dataType
            data_size = cmd.keyInfo.dataSize
            if data_size == 0:
                log.debug("SMC KEYINFO %s: dataSize=0 — key not present", key)
                return None

            # Read the actual bytes
            cmd.data8 = SMC_CMD_READ_BYTES
            ret = _iokit.IOConnectCallStructMethod(
                self._smc_conn, KERNEL_INDEX_SMC,
                ctypes.byref(cmd), ctypes.sizeof(cmd),
                ctypes.byref(cmd), ctypes.byref(out_size),
            )
            if ret != 0:
                log.debug("SMC READ %s: IOKit returned %d", key, ret)
                return None

            return _parse_smc_bytes(data_type, cmd.bytes, data_size)
        except Exception:
            log.debug("SMC read %s failed", key, exc_info=True)
            return None

    # ── Discovery ────────────────────────────────────────────────────

    def _discover_smc(self) -> None:
        """Discover SMC sensors — works on both Intel and Apple Silicon.

        Probes every key in _SMC_KEYS (temps); on Apple Silicon also probes
        _AS_TEMP_KEYS for chip-specific die/cluster temps. Fans discovered
        dynamically via FNum key.
        """
        if not self._open_smc():
            log.debug("SMC unavailable — skipping temp/fan sensors")
            return

        found = 0
        # ── Temperature keys from curated table ──
        for key, (name, category, unit) in _SMC_KEYS.items():
            val = self._read_smc_direct(key)
            if val is not None and val > 0:
                self._sensors.append(
                    SensorInfo(f'smc:{key}', name, category, unit, 'smc'),
                )
                log.debug("SMC key %s: %.1f %s (%s)", key, val, unit, name)
                found += 1
            else:
                log.debug("SMC key %s: not present or zero", key)

        # ── Apple Silicon extended temperature keys ──
        if IS_APPLE_SILICON:
            as_found = 0
            as_probed = _AS_TEMP_KEYS - _SMC_KEYS.keys()
            for key in sorted(as_probed):
                val = self._read_smc_direct(key)
                if val is not None and val > 0:
                    name, category, unit = _as_key_metadata(key)
                    self._sensors.append(
                        SensorInfo(f'smc:{key}', name, category, unit, 'smc'),
                    )
                    log.debug("SMC AS key %s: %.1f %s (%s)",
                              key, val, unit, name)
                    as_found += 1
            log.info("SMC AS temp discovery: %d/%d keys present",
                     as_found, len(as_probed))
            found += as_found

        # ── Fans via FNum ──
        found += self._discover_fans()

        log.info("SMC discovery complete: %d sensors total", found)

    def _discover_fans(self) -> int:
        """Discover fan sensors dynamically via the FNum SMC key.

        Reads FNum to get the actual fan count, then probes F{i}Ac for
        each fan. Falls back to probing F0Ac–F3Ac if FNum is unreadable.
        """
        fnum_val = self._read_smc_direct('FNum')
        if fnum_val is not None and fnum_val > 0:
            fan_count = int(fnum_val)
            log.info("SMC FNum reports %d fan(s)", fan_count)
        else:
            fan_count = _FNUM_FALLBACK
            log.debug("SMC FNum unavailable — probing F0Ac..F%dAc", fan_count - 1)

        found = 0
        for i in range(fan_count):
            key = f'F{i}Ac'
            val = self._read_smc_direct(key)
            if val is not None and val >= 0:
                self._sensors.append(
                    SensorInfo(f'smc:{key}', f'Fan {i}', 'fan', 'RPM', 'smc'),
                )
                log.debug("SMC fan %s: %.0f RPM", key, val)
                found += 1
            else:
                log.debug("SMC fan %s: not present", key)

        if found == 0:
            log.debug("No fan sensors discovered via SMC")
        return found

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
            log.debug("SMC poll skipped — no connection")
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
