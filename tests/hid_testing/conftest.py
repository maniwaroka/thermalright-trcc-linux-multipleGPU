"""Shared fixtures for all HID device tests."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from trcc.adapters.device.registry_detector import enable_hid_testing
from trcc.adapters.device.template_method_hid import UsbTransport


@pytest.fixture(autouse=True)
def _enable_hid_for_tests():
    """Auto-enable HID testing for every test in hid_testing/."""
    enable_hid_testing()


@pytest.fixture(autouse=True)
def _patch_hid_sleep():
    """Disable time.sleep in HID device modules for fast tests."""
    with patch("trcc.adapters.device.template_method_hid.time.sleep"), \
         patch("trcc.adapters.device.adapter_led.time.sleep"):
        yield


def make_mock_transport() -> MagicMock:
    """Create a MagicMock that satisfies the UsbTransport interface."""
    t = MagicMock(spec=UsbTransport)
    t.is_open = True
    return t
