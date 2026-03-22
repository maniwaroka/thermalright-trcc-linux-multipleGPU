#!/usr/bin/env python
"""Diagnose tool — drive protocol tests from a ``trcc report`` file.

Usage
-----
    python tools/diagnose.py path/to/report.txt
    python tools/diagnose.py -       # read from stdin (paste report)

What it does
------------
1. Parses the ``trcc report`` text (sections: Version, Detected devices,
   Handshakes, Recent log).
2. Extracts: trcc version, OS, each device's VID:PID, protocol, PM byte,
   SUB byte, and resolution.
3. For each detected device, sets TRCC_DIAGNOSE_* env vars and runs pytest
   against the matching test file(s).
4. Reports which tests pass / fail — pinpoints the broken layer without
   needing real hardware.

Supported protocols
-------------------
    scsi  → tests/adapters/device/test_scsi.py
    bulk  → tests/adapters/device/test_bulk.py
    ly    → tests/adapters/device/test_ly.py
    hid   → tests/adapters/device/test_hid.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class DeviceProfile:
    protocol: str
    vid: int
    pid: int
    pm: int = 0
    sub: int = 0
    width: int = 0
    height: int = 0
    path: str = ""


@dataclass
class ParsedReport:
    trcc_version: str = ""
    os_name: str = ""
    python_version: str = ""
    devices: list[DeviceProfile] = field(default_factory=list)
    log_tail: list[str] = field(default_factory=list)
    ebusy_in_log: bool = False


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_PROTO_MAP = {
    "SCSI": "scsi",
    "BULK": "bulk",
    "LY":   "ly",
    "HID":  "hid",
    "LED":  "hid",
}

_TEST_MAP = {
    "scsi": "tests/adapters/device/test_scsi.py",
    "bulk": "tests/adapters/device/test_bulk.py",
    "ly":   "tests/adapters/device/test_ly.py",
    "hid":  "tests/adapters/device/test_hid.py",
}


def parse_report(text: str) -> ParsedReport:
    """Extract device profile(s) from ``trcc report`` text output."""
    report = ParsedReport()
    lines = text.splitlines()

    section = ""
    for line in lines:
        stripped = line.strip()

        # Track current section — titles have no leading whitespace; content has 2+ spaces
        if re.match(r"^─{10,}", stripped) or re.match(r"^={10,}", stripped):
            continue
        if stripped and not line[0].isspace():
            section = stripped
            continue

        # Version section
        if section.startswith("Version"):
            m = re.match(r"\s*trcc-linux:\s+(.+)", line)
            if m:
                report.trcc_version = m.group(1).strip()
            m = re.match(r"\s*Python:\s+(.+)", line)
            if m:
                report.python_version = m.group(1).strip()
            m = re.match(r"\s*OS:\s+(.+)", line)
            if m:
                report.os_name = m.group(1).strip()

        # Detected devices section — lines like:
        #   [1] 87ad:70db  GrandVision 360  (BULK)  path=bulk:87ad:70db
        elif section.startswith("Detected devices"):
            m = re.match(
                r"\s*\[\d+\]\s+([0-9a-fA-F]{4}):([0-9a-fA-F]{4})\s+.*"
                r"\((\w+)\).*path=(\S+)",
                line,
            )
            if m:
                vid = int(m.group(1), 16)
                pid = int(m.group(2), 16)
                proto_raw = m.group(3).upper()
                path = m.group(4)
                proto = _PROTO_MAP.get(proto_raw, proto_raw.lower())
                report.devices.append(DeviceProfile(
                    protocol=proto, vid=vid, pid=pid, path=path,
                ))

        # Handshakes section — extract PM, SUB, resolution per device
        elif section.startswith("Handshake"):
            # PM / SUB line: "PM=7  SUB=0  FBL=64  resolution=(640, 480)"
            m = re.search(r"PM=(\d+)", line)
            if m and report.devices:
                report.devices[-1].pm = int(m.group(1))
            m = re.search(r"SUB=(\d+)", line)
            if m and report.devices:
                report.devices[-1].sub = int(m.group(1))
            m = re.search(r"resolution=\((\d+),\s*(\d+)\)", line)
            if m and report.devices:
                report.devices[-1].width = int(m.group(1))
                report.devices[-1].height = int(m.group(2))

        # Recent log section
        elif "Recent log" in section:
            if stripped:
                report.log_tail.append(stripped)
                if "EBUSY" in stripped or "claim_interface" in stripped:
                    report.ebusy_in_log = True

    return report


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent


def _run_tests(device: DeviceProfile, extra_markers: list[str]) -> int:
    """Run pytest for the given device profile. Returns exit code."""
    test_file = _TEST_MAP.get(device.protocol)
    if not test_file:
        print(f"  [SKIP] No test file for protocol '{device.protocol}'")
        return 0

    env = os.environ.copy()
    env["TRCC_DIAGNOSE_VID"] = f"{device.vid:04X}"
    env["TRCC_DIAGNOSE_PID"] = f"{device.pid:04X}"
    env["TRCC_DIAGNOSE_PM"] = str(device.pm)
    env["TRCC_DIAGNOSE_SUB"] = str(device.sub)
    env["TRCC_DIAGNOSE_PROTOCOL"] = device.protocol
    env["PYTHONPATH"] = str(_REPO_ROOT / "src")

    # Run only the diagnose-aware tests (functions, not unittest classes)
    # plus the EBUSY test if the log shows it fired
    k_filters = ["test_bulk_handshake_profile", "test_bulk_send_frame_profile"]
    if "EBUSY" in extra_markers or device.protocol == "bulk":
        k_filters.append("test_bulk_open_ebusy_no_reset")

    k_expr = " or ".join(k_filters)

    cmd = [
        sys.executable, "-m", "pytest",
        str(_REPO_ROOT / test_file),
        "-k", k_expr,
        "-v", "--tb=short", "--no-header",
    ]

    print(f"\n  Running: {' '.join(cmd[-4:])}")
    print(f"  VID={device.vid:04X} PID={device.pid:04X} PM={device.pm} "
          f"SUB={device.sub} protocol={device.protocol}")
    print()

    result = subprocess.run(cmd, env=env, cwd=_REPO_ROOT)
    return result.returncode


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    path = sys.argv[1]
    if path == "-":
        text = sys.stdin.read()
    else:
        text = Path(path).read_text(errors="replace")

    report = parse_report(text)

    print("=" * 60)
    print("TRCC Diagnose Tool")
    print("=" * 60)
    print(f"  trcc-linux : {report.trcc_version or '(not found)'}")
    print(f"  OS         : {report.os_name or '(not found)'}")
    print(f"  Python     : {report.python_version or '(not found)'}")
    print(f"  Devices    : {len(report.devices)}")
    if report.ebusy_in_log:
        print("  !! EBUSY detected in log — running claim_interface test")

    if not report.devices:
        print("\n[ERROR] No devices found in report. Check the 'Detected devices' section.")
        sys.exit(2)

    extra = ["EBUSY"] if report.ebusy_in_log else []
    failed = 0
    for i, device in enumerate(report.devices, 1):
        print(f"\n{'─' * 60}")
        print(f"Device {i}: {device.vid:04X}:{device.pid:04X} ({device.protocol.upper()})")
        rc = _run_tests(device, extra)
        if rc != 0:
            failed += 1

    print(f"\n{'=' * 60}")
    if failed:
        print(f"RESULT: {failed}/{len(report.devices)} device(s) FAILED — see output above")
        sys.exit(1)
    else:
        print(f"RESULT: All {len(report.devices)} device(s) PASSED")


if __name__ == "__main__":
    main()
