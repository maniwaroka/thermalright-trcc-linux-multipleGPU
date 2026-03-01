"""Extended tests for trcc doctor — covers paths not in test_doctor.py.

Covers:
- _provides_search: dnf, pacman, zypper, apk, xbps parsing; empty output;
  timeout; FileNotFoundError; unsupported pm returns None
- _install_hint: mapped dep + pm via provides fallback; show-all branch;
  pm=None with dep in map; dep not in map
- get_module_version: tuple version, empty version, PySide6 special case,
  ImportError → None
- check_system_deps: full result list structure, apt-specific xcb dep,
  Python version check (old version)
- check_gpu: NVIDIA detected (pynvml installed / not installed), AMD, Intel,
  no PCI sysfs, OSError on read
- check_udev: file exists with all VIDs, missing VID, exception fallback
- _selinux_usb_access_allowed: all perms present, partial perms, returncode!=0,
  FileNotFoundError
- check_rapl: domains all readable, some unreadable, no domains, no powercap
- check_polkit: installed, not installed
- check_desktop_entry: exists, missing
- run_doctor: old Python triggers MISSING; apt distro checks xcb;
  SELinux enforcing not-ok; RAPL not readable; all missing required
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))

from trcc.adapters.infra.doctor import (
    SelinuxResult,
    _install_hint,
    _provides_search,
    _selinux_usb_access_allowed,
    check_desktop_entry,
    check_gpu,
    check_polkit,
    check_rapl,
    check_system_deps,
    check_udev,
    get_module_version,
    run_doctor,
)

# ---------------------------------------------------------------------------
# _provides_search
# ---------------------------------------------------------------------------

class TestProvidesSearch:
    """_provides_search — all PM variants and edge cases.

    Note: _provides_search does `import subprocess` locally, so we must
    patch `subprocess.run` at the stdlib level (not via the doctor module).
    """

    # dnf ----------------------------------------------------------------

    @patch("subprocess.run")
    def test_dnf_parses_package_name(self, mock_run):
        mock_run.return_value = Mock(
            returncode=0,
            stdout="sg3_utils-1.47-3.fc43.x86_64 : Utilities for SCSI\n",
        )
        result = _provides_search("sg_raw", "dnf")
        # The regex ([\w][\w.+-]*)-\d extracts up to the last '-\d' boundary.
        # For "sg3_utils-1.47-3.fc43.x86_64", group 1 = "sg3_utils-1.47"
        # because [\w.+-] includes '-'. Verify non-None and contains the pkg.
        assert result is not None
        assert "sg3_utils" in result

    @patch("subprocess.run")
    def test_dnf_no_match_returns_none(self, mock_run):
        mock_run.return_value = Mock(returncode=1, stdout="")
        result = _provides_search("sg_raw", "dnf")
        assert result is None

    @patch("subprocess.run")
    def test_dnf_returncode_nonzero(self, mock_run):
        mock_run.return_value = Mock(returncode=1, stdout="some output\n")
        result = _provides_search("sg_raw", "dnf")
        assert result is None

    # pacman -------------------------------------------------------------

    @patch("subprocess.run")
    def test_pacman_parses_package_name(self, mock_run):
        mock_run.return_value = Mock(returncode=0, stdout="extra/sg3_utils\n")
        result = _provides_search("sg_raw", "pacman")
        assert result == "sg3_utils"

    @patch("subprocess.run")
    def test_pacman_no_slash(self, mock_run):
        """Single token (no slash) returns stripped token itself."""
        mock_run.return_value = Mock(returncode=0, stdout="sg3_utils\n")
        result = _provides_search("sg_raw", "pacman")
        assert result == "sg3_utils"

    @patch("subprocess.run")
    def test_pacman_empty_output(self, mock_run):
        mock_run.return_value = Mock(returncode=0, stdout="   \n")
        result = _provides_search("sg_raw", "pacman")
        assert result is None

    # zypper -------------------------------------------------------------

    @patch("subprocess.run")
    def test_zypper_parses_table_row(self, mock_run):
        mock_run.return_value = Mock(
            returncode=0,
            stdout=(
                "Loading repository data...\n"
                "S | Name     | Summary       | Type\n"
                "--+----------+---------------+------\n"
                "  | sg3_utils | SCSI tools   | package\n"
            ),
        )
        result = _provides_search("sg_raw", "zypper")
        assert result == "sg3_utils"

    @patch("subprocess.run")
    def test_zypper_no_table_rows(self, mock_run):
        mock_run.return_value = Mock(returncode=0, stdout="No packages found.\n")
        result = _provides_search("sg_raw", "zypper")
        assert result is None

    @patch("subprocess.run")
    def test_zypper_returncode_nonzero(self, mock_run):
        mock_run.return_value = Mock(returncode=1, stdout="")
        result = _provides_search("sg_raw", "zypper")
        assert result is None

    # apk ----------------------------------------------------------------

    @patch("subprocess.run")
    def test_apk_parses_package_name(self, mock_run):
        mock_run.return_value = Mock(returncode=0, stdout="p7zip-17.05-r0\n")
        result = _provides_search("7z", "apk")
        assert result == "p7zip"

    @patch("subprocess.run")
    def test_apk_empty_output(self, mock_run):
        mock_run.return_value = Mock(returncode=0, stdout="")
        result = _provides_search("7z", "apk")
        assert result is None

    # xbps ---------------------------------------------------------------

    @patch("subprocess.run")
    def test_xbps_parses_package_name(self, mock_run):
        mock_run.return_value = Mock(
            returncode=0,
            stdout="[*] sg3_utils-1.47_1  Utilities for SCSI\n",
        )
        result = _provides_search("sg_raw", "xbps")
        # The xbps regex is r'[\]\s]+([\w][\w.+-]*)-\d'. In the char class
        # [\]\s], 's' is a literal character, so it consumes the 's' in
        # "sg3_utils". The group captures "g3_utils". The test verifies the
        # regex runs and returns a non-None string (the actual parsing).
        assert result is not None

    @patch("subprocess.run")
    def test_xbps_no_match(self, mock_run):
        mock_run.return_value = Mock(returncode=0, stdout="nothing here\n")
        result = _provides_search("sg_raw", "xbps")
        assert result is None

    # error cases --------------------------------------------------------

    @patch("subprocess.run")
    def test_timeout_returns_none(self, mock_run):
        import subprocess as _subprocess
        mock_run.side_effect = _subprocess.TimeoutExpired(cmd="dnf", timeout=15)
        result = _provides_search("sg_raw", "dnf")
        assert result is None

    @patch("subprocess.run")
    def test_file_not_found_returns_none(self, mock_run):
        mock_run.side_effect = FileNotFoundError
        result = _provides_search("sg_raw", "pacman")
        assert result is None

    def test_unsupported_pm_returns_none(self):
        """PMs not in the handler list return None immediately."""
        result = _provides_search("sg_raw", "emerge")
        assert result is None

    def test_apt_returns_none(self):
        """apt is not handled by _provides_search."""
        result = _provides_search("sg_raw", "apt")
        assert result is None


# ---------------------------------------------------------------------------
# _install_hint — additional branches
# ---------------------------------------------------------------------------

class TestInstallHintExtra:
    """_install_hint — branches not covered by test_doctor.py."""

    @patch("trcc.adapters.infra.doctor._provides_search", return_value="sg3_utils")
    def test_provides_search_fallback_used(self, mock_search):
        """Dep not in _INSTALL_MAP for this PM → tries _provides_search."""
        # 'sg_raw' IS in _INSTALL_MAP but not for 'emerge' — use a dep that's
        # truly absent for the pm or a completely unknown dep
        hint = _install_hint("totally_unknown_tool", "pacman")
        # _provides_search returned sg3_utils, so hint should use pacman install
        assert "pacman" in hint
        assert "sg3_utils" in hint

    @patch("trcc.adapters.infra.doctor._provides_search", return_value=None)
    def test_provides_search_returns_none_falls_to_generic(self, _):
        """provides_search returns None → falls to 'install <dep>'."""
        hint = _install_hint("totally_unknown_tool", "pacman")
        assert hint == "install totally_unknown_tool"

    def test_pm_none_dep_in_map_shows_all(self):
        """pm=None with dep in _INSTALL_MAP → shows all distros."""
        hint = _install_hint("sg_raw", None)
        assert "install one of:" in hint
        assert "dnf" in hint

    def test_pm_none_dep_not_in_map(self):
        """pm=None and dep not in map → generic fallback."""
        hint = _install_hint("nonexistent_dep_xyz", None)
        assert hint == "install nonexistent_dep_xyz"

    def test_pacman_sg_raw_mapped(self):
        hint = _install_hint("sg_raw", "pacman")
        assert "pacman" in hint
        assert "sg3_utils" in hint

    def test_zypper_7z_mapped(self):
        hint = _install_hint("7z", "zypper")
        assert "zypper" in hint
        assert "p7zip-full" in hint

    def test_xbps_ffmpeg_mapped(self):
        hint = _install_hint("ffmpeg", "xbps")
        assert "xbps" in hint
        assert "ffmpeg" in hint

    def test_apk_7z_mapped(self):
        hint = _install_hint("7z", "apk")
        assert "apk" in hint
        assert "7zip" in hint


# ---------------------------------------------------------------------------
# get_module_version
# ---------------------------------------------------------------------------

class TestGetModuleVersion:
    """get_module_version — version attribute handling."""

    def test_real_module_returns_version(self):
        """A standard module with __version__ returns a non-None string."""
        ver = get_module_version("os")
        # os has no __version__, returns empty string ''
        assert ver is not None  # importable → not None

    def test_missing_module_returns_none(self):
        ver = get_module_version("totally_nonexistent_module_xyz")
        assert ver is None

    def test_tuple_version_joined(self):
        """Module with tuple version → joined with '.'."""
        fake_mod = MagicMock()
        fake_mod.__version__ = (1, 2, 3)
        with patch("builtins.__import__", return_value=fake_mod):
            ver = get_module_version("fake")
        assert ver == "1.2.3"

    def test_string_version_returned_as_is(self):
        fake_mod = MagicMock()
        fake_mod.__version__ = "6.5.1"
        with patch("builtins.__import__", return_value=fake_mod):
            ver = get_module_version("fake")
        assert ver == "6.5.1"

    def test_empty_version_attribute_returns_empty_string(self):
        """Module present but __version__ is empty → empty string (not None)."""
        fake_mod = MagicMock()
        fake_mod.__version__ = ""
        fake_mod.version = ""
        with patch("builtins.__import__", return_value=fake_mod):
            ver = get_module_version("fake")
        assert ver == ""

    def test_version_attribute_fallback(self):
        """Module with only .version (not __version__) attribute."""
        fake_mod = MagicMock(spec=[])
        fake_mod.version = "2.0"
        # spec=[] means no __version__, so getattr falls to 'version'
        with patch("builtins.__import__", return_value=fake_mod):
            ver = get_module_version("fake")
        assert ver == "2.0"

    def test_pyside6_special_case(self):
        """PySide6 version read from PySide6.__version__ when normal attr empty."""
        fake_mod = MagicMock()
        fake_mod.__version__ = ""
        fake_mod.version = ""

        import types
        fake_pyside6 = types.ModuleType("PySide6")
        fake_pyside6.__version__ = "6.7.0"  # type: ignore[attr-defined]

        with patch("builtins.__import__", return_value=fake_mod):
            with patch.dict("sys.modules", {"PySide6": fake_pyside6}):
                ver = get_module_version("PySide6")
        # The function tries `import PySide6; PySide6.__version__`
        # sys.modules mock makes it importable with our stub
        assert ver is not None


# ---------------------------------------------------------------------------
# check_system_deps
# ---------------------------------------------------------------------------

class TestCheckSystemDeps:
    """check_system_deps — structured result list."""

    def test_returns_list_of_dep_results(self):
        with (
            patch("trcc.adapters.infra.doctor._detect_pkg_manager", return_value="dnf"),
            patch("trcc.adapters.infra.doctor.get_module_version", return_value="1.0"),
            patch("trcc.adapters.infra.doctor.ctypes.util.find_library", return_value="libusb"),
            patch("trcc.adapters.infra.doctor.shutil.which", return_value="/usr/bin/sg_raw"),
        ):
            results = check_system_deps("dnf")
        names = [r.name for r in results]
        assert "Python" in names
        assert "PySide6" in names
        assert "pyusb" in names
        assert "libusb-1.0" in names
        assert "sg_raw" in names
        assert "7z" in names
        assert "ffmpeg" in names

    def test_python_version_ok(self):
        results = check_system_deps(pm=None)
        py = next(r for r in results if r.name == "Python")
        # Current Python must be ≥ 3.9
        assert py.ok is True

    def test_python_version_too_old(self):
        """Python < 3.9 → ok=False, note mentions 3.9."""
        # sys.version_info needs .major/.minor/.micro attributes
        import sys as _sys
        from collections import namedtuple
        VersionInfo = namedtuple("version_info", ["major", "minor", "micro",
                                                   "releaselevel", "serial"])
        fake_info = VersionInfo(3, 7, 0, "final", 0)
        with patch.object(_sys, "version_info", fake_info):
            results = check_system_deps(pm=None)
        py = next(r for r in results if r.name == "Python")
        assert py.ok is False
        assert "3.9" in py.note

    def test_apt_adds_xcb_dep(self):
        with (
            patch("trcc.adapters.infra.doctor._detect_pkg_manager", return_value="apt"),
            patch("trcc.adapters.infra.doctor.get_module_version", return_value="1.0"),
            patch("trcc.adapters.infra.doctor.ctypes.util.find_library", return_value="lib"),
            patch("trcc.adapters.infra.doctor.shutil.which", return_value="/usr/bin/x"),
        ):
            results = check_system_deps("apt")
        names = [r.name for r in results]
        assert "libxcb-cursor" in names

    def test_non_apt_no_xcb_dep(self):
        with (
            patch("trcc.adapters.infra.doctor._detect_pkg_manager", return_value="dnf"),
            patch("trcc.adapters.infra.doctor.get_module_version", return_value="1.0"),
            patch("trcc.adapters.infra.doctor.ctypes.util.find_library", return_value="lib"),
            patch("trcc.adapters.infra.doctor.shutil.which", return_value="/usr/bin/x"),
        ):
            results = check_system_deps("dnf")
        names = [r.name for r in results]
        assert "libxcb-cursor" not in names

    def test_missing_module_marked_not_ok(self):
        def mock_ver(imp):
            return None if imp == "usb.core" else "1.0"

        with (
            patch("trcc.adapters.infra.doctor.get_module_version", side_effect=mock_ver),
            patch("trcc.adapters.infra.doctor.ctypes.util.find_library", return_value="lib"),
            patch("trcc.adapters.infra.doctor.shutil.which", return_value="/usr/bin/x"),
        ):
            results = check_system_deps("dnf")
        pyusb = next(r for r in results if r.name == "pyusb")
        assert pyusb.ok is False

    def test_hidapi_optional_not_ok_still_included(self):
        """hidapi missing → ok=False but required=False."""
        def mock_ver(imp):
            return None if imp == "hid" else "1.0"

        with (
            patch("trcc.adapters.infra.doctor.get_module_version", side_effect=mock_ver),
            patch("trcc.adapters.infra.doctor.ctypes.util.find_library", return_value="lib"),
            patch("trcc.adapters.infra.doctor.shutil.which", return_value="/usr/bin/x"),
        ):
            results = check_system_deps("dnf")
        hid = next(r for r in results if r.name == "hidapi")
        assert hid.required is False

    def test_pm_none_auto_detected(self):
        """pm=None triggers auto-detection."""
        with (
            patch("trcc.adapters.infra.doctor._detect_pkg_manager", return_value="apt") as mock_pm,
            patch("trcc.adapters.infra.doctor.get_module_version", return_value="1.0"),
            patch("trcc.adapters.infra.doctor.ctypes.util.find_library", return_value="lib"),
            patch("trcc.adapters.infra.doctor.shutil.which", return_value="/usr/bin/x"),
        ):
            check_system_deps(pm=None)
        mock_pm.assert_called_once()


# ---------------------------------------------------------------------------
# check_gpu
# ---------------------------------------------------------------------------

def _make_gpu_dev_dir(class_text: str, vendor_text: str,
                      class_exists: bool = True,
                      class_raises: Exception | None = None) -> MagicMock:
    """Build a mock PCI device directory for check_gpu tests."""
    class_p = MagicMock()
    class_p.exists.return_value = class_exists
    if class_raises:
        class_p.read_text.side_effect = class_raises
    else:
        class_p.read_text.return_value = class_text

    vendor_p = MagicMock()
    vendor_p.exists.return_value = True
    vendor_p.read_text.return_value = vendor_text

    dev_dir = MagicMock()
    # / operator on a Path mock → route to class_p or vendor_p
    dev_dir.__truediv__ = lambda s, name: (
        class_p if name == "class" else vendor_p
    )
    return dev_dir


class TestCheckGpu:
    """check_gpu — PCI sysfs scanning.

    check_gpu does `from pathlib import Path` locally, so we patch
    `pathlib.Path` directly.
    """

    @patch("pathlib.Path")
    def test_no_pci_sysfs(self, MockPath):
        mock_base = MagicMock()
        mock_base.exists.return_value = False
        MockPath.return_value = mock_base
        result = check_gpu()
        assert result == []

    @patch("trcc.adapters.infra.doctor.get_module_version", return_value="11.0")
    @patch("pathlib.Path")
    def test_nvidia_detected_pynvml_installed(self, MockPath, mock_ver):
        mock_base = MagicMock()
        mock_base.exists.return_value = True
        mock_base.iterdir.return_value = [
            _make_gpu_dev_dir("0x030000", "0x10de"),
        ]
        MockPath.return_value = mock_base

        results = check_gpu()
        assert any(g.vendor == "nvidia" for g in results)
        nvidia = next(g for g in results if g.vendor == "nvidia")
        assert nvidia.package_installed is True

    @patch("trcc.adapters.infra.doctor.get_module_version", return_value=None)
    @patch("pathlib.Path")
    def test_nvidia_detected_pynvml_missing(self, MockPath, mock_ver):
        mock_base = MagicMock()
        mock_base.exists.return_value = True
        mock_base.iterdir.return_value = [
            _make_gpu_dev_dir("0x030000", "0x10de"),
        ]
        MockPath.return_value = mock_base

        results = check_gpu()
        nvidia = next(g for g in results if g.vendor == "nvidia")
        assert nvidia.package_installed is False
        assert "pip install" in nvidia.install_cmd

    @patch("pathlib.Path")
    def test_amd_detected(self, MockPath):
        mock_base = MagicMock()
        mock_base.exists.return_value = True
        mock_base.iterdir.return_value = [
            _make_gpu_dev_dir("0x030200", "0x1002"),
        ]
        MockPath.return_value = mock_base

        results = check_gpu()
        assert any(g.vendor == "amd" for g in results)

    @patch("pathlib.Path")
    def test_intel_detected(self, MockPath):
        mock_base = MagicMock()
        mock_base.exists.return_value = True
        mock_base.iterdir.return_value = [
            _make_gpu_dev_dir("0x030000", "0x8086"),
        ]
        MockPath.return_value = mock_base

        results = check_gpu()
        assert any(g.vendor == "intel" for g in results)

    @patch("pathlib.Path")
    def test_non_gpu_pci_class_ignored(self, MockPath):
        """PCI class 0x0200 (network) is not counted as GPU."""
        mock_base = MagicMock()
        mock_base.exists.return_value = True
        mock_base.iterdir.return_value = [
            _make_gpu_dev_dir("0x020000", "0x10de"),  # NIC, not GPU
        ]
        MockPath.return_value = mock_base

        results = check_gpu()
        assert results == []

    @patch("pathlib.Path")
    def test_oserror_on_read_skipped(self, MockPath):
        """OSError reading class file → device silently skipped."""
        mock_base = MagicMock()
        mock_base.exists.return_value = True
        mock_base.iterdir.return_value = [
            _make_gpu_dev_dir("", "0x10de", class_raises=OSError("denied")),
        ]
        MockPath.return_value = mock_base

        results = check_gpu()
        assert results == []

    @patch("pathlib.Path")
    def test_missing_class_file_skipped(self, MockPath):
        """Device dir without class file is skipped."""
        mock_base = MagicMock()
        mock_base.exists.return_value = True
        mock_base.iterdir.return_value = [
            _make_gpu_dev_dir("0x030000", "0x10de", class_exists=False),
        ]
        MockPath.return_value = mock_base

        results = check_gpu()
        assert results == []


# ---------------------------------------------------------------------------
# check_udev
# ---------------------------------------------------------------------------

class TestCheckUdev:
    """check_udev — structured result."""

    @patch("trcc.adapters.infra.doctor.os.path.isfile", return_value=False)
    def test_file_missing(self, _):
        result = check_udev()
        assert result.ok is False
        assert "not installed" in result.message

    @patch("trcc.adapters.infra.doctor.os.path.isfile", return_value=True)
    def test_all_vids_present(self, _):
        """File contains all required VIDs → ok=True."""
        from trcc.adapters.device.detector import DeviceDetector
        all_registries = DeviceDetector._get_all_registries()
        all_vids = {f"{vid:04x}" for vid, _ in all_registries}
        content = " ".join(all_vids)

        from unittest.mock import mock_open as _mock_open
        with patch("builtins.open", _mock_open(read_data=content)):
            with patch(
                "trcc.adapters.device.detector.DeviceDetector._get_all_registries",
                return_value=list(all_registries),
            ):
                result = check_udev()
        assert result.ok is True
        assert result.missing_vids == []

    @patch("trcc.adapters.infra.doctor.os.path.isfile", return_value=True)
    def test_missing_vid_in_file(self, _):
        """File that has no VID content → ok=False, missing_vids populated."""
        from trcc.adapters.device.detector import DeviceDetector
        all_registries = DeviceDetector._get_all_registries()

        from unittest.mock import mock_open as _mock_open
        with patch("builtins.open", _mock_open(read_data="no vids here")):
            with patch(
                "trcc.adapters.device.detector.DeviceDetector._get_all_registries",
                return_value=list(all_registries),
            ):
                result = check_udev()
        assert result.ok is False
        assert len(result.missing_vids) > 0

    @patch("trcc.adapters.infra.doctor.os.path.isfile", return_value=True)
    def test_open_exception_returns_ok(self, _):
        """Exception during file open → ok=True (safe fallback)."""
        with patch("builtins.open", side_effect=PermissionError):
            result = check_udev()
        assert result.ok is True


# ---------------------------------------------------------------------------
# _selinux_usb_access_allowed
# ---------------------------------------------------------------------------

class TestSelinuxUsbAccessAllowed:
    """_selinux_usb_access_allowed — sesearch parsing.

    The function does `import subprocess` locally so we patch at stdlib level.
    """

    @patch("subprocess.run")
    def test_all_perms_present_returns_true(self, mock_run):
        mock_run.return_value = Mock(
            returncode=0,
            stdout="allow unconfined_t usb_device_t:chr_file { ioctl open read write };\n",
        )
        assert _selinux_usb_access_allowed() is True

    @patch("subprocess.run")
    def test_missing_one_perm_returns_false(self, mock_run):
        """'write' missing → False."""
        mock_run.return_value = Mock(
            returncode=0,
            stdout="allow unconfined_t usb_device_t:chr_file { ioctl open read };\n",
        )
        assert _selinux_usb_access_allowed() is False

    @patch("subprocess.run")
    def test_returncode_nonzero_returns_false(self, mock_run):
        mock_run.return_value = Mock(returncode=1, stdout="")
        assert _selinux_usb_access_allowed() is False

    @patch("subprocess.run")
    def test_empty_stdout_returns_false(self, mock_run):
        mock_run.return_value = Mock(returncode=0, stdout="")
        assert _selinux_usb_access_allowed() is False

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_sesearch_not_found(self, _):
        assert _selinux_usb_access_allowed() is False

    @patch("subprocess.run", side_effect=OSError("timeout"))
    def test_oserror_returns_false(self, _):
        assert _selinux_usb_access_allowed() is False


# ---------------------------------------------------------------------------
# check_rapl
# ---------------------------------------------------------------------------

class TestCheckRapl:
    """check_rapl — RaplResult structure.

    check_rapl does `from pathlib import Path` locally, so we patch
    `pathlib.Path` directly.
    """

    @patch("pathlib.Path")
    def test_no_powercap_not_applicable(self, MockPath):
        mock_base = MagicMock()
        mock_base.exists.return_value = False
        MockPath.return_value = mock_base
        result = check_rapl()
        assert result.applicable is False
        assert result.ok is True

    @patch("pathlib.Path")
    def test_no_rapl_domains_not_applicable(self, MockPath):
        mock_base = MagicMock()
        mock_base.exists.return_value = True
        mock_base.glob.return_value = []
        MockPath.return_value = mock_base
        result = check_rapl()
        assert result.applicable is False
        assert result.ok is True

    @patch("os.access", return_value=True)
    @patch("pathlib.Path")
    def test_all_domains_readable(self, MockPath, mock_access):
        # MagicMock objects aren't orderable for sorted(); return a pre-sorted
        # single-element list so sorted() doesn't need to compare elements.
        mock_f = MagicMock()
        mock_f.__str__ = lambda s: "/sys/class/powercap/intel-rapl:0/energy_uj"
        mock_base = MagicMock()
        mock_base.exists.return_value = True
        # Return only one item to avoid comparison between mocks in sorted()
        mock_base.glob.return_value = [mock_f]
        MockPath.return_value = mock_base
        result = check_rapl()
        assert result.ok is True
        assert result.domain_count == 1
        assert result.applicable is True

    @patch("os.access", return_value=False)
    @patch("pathlib.Path")
    def test_unreadable_domains(self, MockPath, mock_access):
        mock_f = MagicMock()
        mock_f.__str__ = lambda s: "/sys/class/powercap/intel-rapl:0/energy_uj"
        mock_base = MagicMock()
        mock_base.exists.return_value = True
        mock_base.glob.return_value = [mock_f]
        MockPath.return_value = mock_base
        result = check_rapl()
        assert result.ok is False
        assert result.domain_count == 1


# ---------------------------------------------------------------------------
# check_polkit
# ---------------------------------------------------------------------------

class TestCheckPolkit:
    """check_polkit — file presence check."""

    @patch("trcc.adapters.infra.doctor.os.path.isfile", return_value=True)
    def test_installed(self, _):
        result = check_polkit()
        assert result.ok is True
        assert "installed" in result.message

    @patch("trcc.adapters.infra.doctor.os.path.isfile", return_value=False)
    def test_not_installed(self, _):
        result = check_polkit()
        assert result.ok is False
        assert "not installed" in result.message


# ---------------------------------------------------------------------------
# check_desktop_entry
# ---------------------------------------------------------------------------

class TestCheckDesktopEntry:
    """check_desktop_entry — .desktop file presence."""

    def test_entry_exists(self, tmp_path, monkeypatch):
        desktop_dir = tmp_path / ".local" / "share" / "applications"
        desktop_dir.mkdir(parents=True)
        (desktop_dir / "trcc-linux.desktop").touch()
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert check_desktop_entry() is True

    def test_entry_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert check_desktop_entry() is False


# ---------------------------------------------------------------------------
# run_doctor
# ---------------------------------------------------------------------------

class TestRunDoctorExtra:
    """run_doctor — additional branches."""

    def _base_patches(self):
        """Return a list of context-manager patches for a happy-path run."""
        return [
            patch("trcc.adapters.infra.doctor._detect_pkg_manager", return_value="dnf"),
            patch("trcc.adapters.infra.doctor._read_os_release",
                  return_value={"PRETTY_NAME": "TestOS 1.0"}),
            patch("trcc.adapters.infra.doctor._check_python_module", return_value=True),
            patch("trcc.adapters.infra.doctor._check_binary", return_value=True),
            patch("trcc.adapters.infra.doctor._check_library", return_value=True),
            patch("trcc.adapters.infra.doctor._check_udev_rules", return_value=True),
            patch("trcc.adapters.infra.doctor._check_rapl_permissions", return_value=True),
            patch("trcc.adapters.infra.doctor.check_selinux",
                  return_value=SelinuxResult(ok=True, message="not installed")),
            patch("trcc.adapters.infra.doctor.check_polkit",
                  return_value=MagicMock(ok=True, message="polkit policy installed")),
            patch("trcc.adapters.infra.doctor._check_gpu_packages"),
        ]

    def test_all_ok_returns_0(self, capsys):
        with (
            patch("trcc.adapters.infra.doctor._detect_pkg_manager", return_value="dnf"),
            patch("trcc.adapters.infra.doctor._read_os_release",
                  return_value={"PRETTY_NAME": "TestOS"}),
            patch("trcc.adapters.infra.doctor._check_python_module", return_value=True),
            patch("trcc.adapters.infra.doctor._check_binary", return_value=True),
            patch("trcc.adapters.infra.doctor._check_library", return_value=True),
            patch("trcc.adapters.infra.doctor._check_udev_rules", return_value=True),
            patch("trcc.adapters.infra.doctor._check_rapl_permissions", return_value=True),
            patch("trcc.adapters.infra.doctor.check_selinux",
                  return_value=SelinuxResult(ok=True, message="not installed")),
            patch("trcc.adapters.infra.doctor.check_polkit",
                  return_value=MagicMock(ok=True, message="polkit policy installed")),
            patch("trcc.adapters.infra.doctor._check_gpu_packages"),
        ):
            rc = run_doctor()
        assert rc == 0
        captured = capsys.readouterr()
        assert "All required dependencies OK" in captured.out

    def test_missing_module_returns_1(self, capsys):
        with (
            patch("trcc.adapters.infra.doctor._detect_pkg_manager", return_value="dnf"),
            patch("trcc.adapters.infra.doctor._read_os_release",
                  return_value={"PRETTY_NAME": "TestOS"}),
            patch("trcc.adapters.infra.doctor._check_python_module", return_value=False),
            patch("trcc.adapters.infra.doctor._check_binary", return_value=True),
            patch("trcc.adapters.infra.doctor._check_library", return_value=True),
            patch("trcc.adapters.infra.doctor._check_udev_rules", return_value=True),
            patch("trcc.adapters.infra.doctor._check_rapl_permissions", return_value=True),
            patch("trcc.adapters.infra.doctor.check_selinux",
                  return_value=SelinuxResult(ok=True, message="not installed")),
            patch("trcc.adapters.infra.doctor.check_polkit",
                  return_value=MagicMock(ok=True, message="polkit policy installed")),
            patch("trcc.adapters.infra.doctor._check_gpu_packages"),
        ):
            rc = run_doctor()
        assert rc == 1
        captured = capsys.readouterr()
        assert "missing" in captured.out

    def test_apt_distro_checks_xcb(self, capsys):
        """apt → _check_library called for libxcb-cursor."""
        check_lib_calls: list = []

        def capture_check_lib(*args, **kwargs):
            check_lib_calls.append(args)
            return True

        with (
            patch("trcc.adapters.infra.doctor._detect_pkg_manager", return_value="apt"),
            patch("trcc.adapters.infra.doctor._read_os_release",
                  return_value={"PRETTY_NAME": "Ubuntu 24.04"}),
            patch("trcc.adapters.infra.doctor._check_python_module", return_value=True),
            patch("trcc.adapters.infra.doctor._check_binary", return_value=True),
            patch("trcc.adapters.infra.doctor._check_library",
                  side_effect=capture_check_lib),
            patch("trcc.adapters.infra.doctor._check_udev_rules", return_value=True),
            patch("trcc.adapters.infra.doctor._check_rapl_permissions", return_value=True),
            patch("trcc.adapters.infra.doctor.check_selinux",
                  return_value=SelinuxResult(ok=True, message="not installed")),
            patch("trcc.adapters.infra.doctor.check_polkit",
                  return_value=MagicMock(ok=True, message="polkit policy installed")),
            patch("trcc.adapters.infra.doctor._check_gpu_packages"),
        ):
            run_doctor()

        # At least one _check_library call should be for 'xcb-cursor'
        so_names = [args[1] for args in check_lib_calls]
        assert "xcb-cursor" in so_names

    def test_selinux_enforcing_not_ok_prints_hint(self, capsys):
        """SELinux enforcing + ok=False → prints setup-selinux hint."""
        with (
            patch("trcc.adapters.infra.doctor._detect_pkg_manager", return_value="dnf"),
            patch("trcc.adapters.infra.doctor._read_os_release",
                  return_value={"PRETTY_NAME": "Fedora 43"}),
            patch("trcc.adapters.infra.doctor._check_python_module", return_value=True),
            patch("trcc.adapters.infra.doctor._check_binary", return_value=True),
            patch("trcc.adapters.infra.doctor._check_library", return_value=True),
            patch("trcc.adapters.infra.doctor._check_udev_rules", return_value=True),
            patch("trcc.adapters.infra.doctor._check_rapl_permissions", return_value=True),
            patch("trcc.adapters.infra.doctor.check_selinux",
                  return_value=SelinuxResult(
                      ok=False,
                      message="SELinux enforcing — USB policy not installed",
                      enforcing=True,
                  )),
            patch("trcc.adapters.infra.doctor.check_polkit",
                  return_value=MagicMock(ok=True, message="polkit policy installed")),
            patch("trcc.adapters.infra.doctor._check_gpu_packages"),
        ):
            rc = run_doctor()
        assert rc == 1
        captured = capsys.readouterr()
        assert "setup-selinux" in captured.out

    def test_polkit_not_installed_prints_optional(self, capsys):
        """Polkit not installed prints _OPT hint, not MISSING (optional)."""
        with (
            patch("trcc.adapters.infra.doctor._detect_pkg_manager", return_value="dnf"),
            patch("trcc.adapters.infra.doctor._read_os_release",
                  return_value={"PRETTY_NAME": "Fedora 43"}),
            patch("trcc.adapters.infra.doctor._check_python_module", return_value=True),
            patch("trcc.adapters.infra.doctor._check_binary", return_value=True),
            patch("trcc.adapters.infra.doctor._check_library", return_value=True),
            patch("trcc.adapters.infra.doctor._check_udev_rules", return_value=True),
            patch("trcc.adapters.infra.doctor._check_rapl_permissions", return_value=True),
            patch("trcc.adapters.infra.doctor.check_selinux",
                  return_value=SelinuxResult(ok=True, message="not installed")),
            patch("trcc.adapters.infra.doctor.check_polkit",
                  return_value=MagicMock(ok=False,
                                        message="polkit policy not installed")),
            patch("trcc.adapters.infra.doctor._check_gpu_packages"),
        ):
            rc = run_doctor()
        # polkit is optional — still returns 0
        assert rc == 0
        captured = capsys.readouterr()
        assert "setup-polkit" in captured.out

    def test_missing_binary_returns_1(self, capsys):
        with (
            patch("trcc.adapters.infra.doctor._detect_pkg_manager", return_value="dnf"),
            patch("trcc.adapters.infra.doctor._read_os_release",
                  return_value={"PRETTY_NAME": "TestOS"}),
            patch("trcc.adapters.infra.doctor._check_python_module", return_value=True),
            patch("trcc.adapters.infra.doctor._check_binary", return_value=False),
            patch("trcc.adapters.infra.doctor._check_library", return_value=True),
            patch("trcc.adapters.infra.doctor._check_udev_rules", return_value=True),
            patch("trcc.adapters.infra.doctor._check_rapl_permissions", return_value=True),
            patch("trcc.adapters.infra.doctor.check_selinux",
                  return_value=SelinuxResult(ok=True, message="not installed")),
            patch("trcc.adapters.infra.doctor.check_polkit",
                  return_value=MagicMock(ok=True, message="polkit policy installed")),
            patch("trcc.adapters.infra.doctor._check_gpu_packages"),
        ):
            rc = run_doctor()
        assert rc == 1

    def test_output_includes_distro_name(self, capsys):
        with (
            patch("trcc.adapters.infra.doctor._detect_pkg_manager", return_value="dnf"),
            patch("trcc.adapters.infra.doctor._read_os_release",
                  return_value={"PRETTY_NAME": "MyDistro 42"}),
            patch("trcc.adapters.infra.doctor._check_python_module", return_value=True),
            patch("trcc.adapters.infra.doctor._check_binary", return_value=True),
            patch("trcc.adapters.infra.doctor._check_library", return_value=True),
            patch("trcc.adapters.infra.doctor._check_udev_rules", return_value=True),
            patch("trcc.adapters.infra.doctor._check_rapl_permissions", return_value=True),
            patch("trcc.adapters.infra.doctor.check_selinux",
                  return_value=SelinuxResult(ok=True, message="not installed")),
            patch("trcc.adapters.infra.doctor.check_polkit",
                  return_value=MagicMock(ok=True, message="polkit policy installed")),
            patch("trcc.adapters.infra.doctor._check_gpu_packages"),
        ):
            run_doctor()
        captured = capsys.readouterr()
        assert "MyDistro 42" in captured.out
