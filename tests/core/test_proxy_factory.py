"""Tests for ipc.py proxy factory functions."""

from trcc.core.instance import InstanceKind
from trcc.ipc import (
    DisplayProxy,
    LEDProxy,
    create_lcd_proxy,
    create_led_proxy,
)


class TestCreateLcdProxy:
    """create_lcd_proxy() returns DisplayProxy with correct transport."""

    def test_gui_returns_ipc_transport(self):
        proxy = create_lcd_proxy(InstanceKind.GUI)
        assert isinstance(proxy, DisplayProxy)
        assert proxy.is_ipc

    def test_api_returns_api_transport(self):
        proxy = create_lcd_proxy(InstanceKind.API)
        assert isinstance(proxy, DisplayProxy)
        assert not proxy.is_ipc


class TestCreateLedProxy:
    """create_led_proxy() returns LEDProxy with correct transport."""

    def test_gui_returns_ipc_transport(self):
        proxy = create_led_proxy(InstanceKind.GUI)
        assert isinstance(proxy, LEDProxy)
        assert proxy.is_ipc

    def test_api_returns_api_transport(self):
        proxy = create_led_proxy(InstanceKind.API)
        assert isinstance(proxy, LEDProxy)
        assert not proxy.is_ipc
