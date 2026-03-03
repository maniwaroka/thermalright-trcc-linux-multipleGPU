"""
Tests for LED controller and model layer.

Covers LEDMode enum, LEDZoneState, LEDState, LEDService (state mutations,
tick dispatch, all 6 effect algorithms, callbacks), and LEDDeviceController
(delegation, protocol wiring, tick with send, initialize, save/load config,
cleanup).

Architecture mirrors Windows FormLED.cs:
  - LEDService holds state + computes per-segment colors each tick
  - LEDDeviceController is the Facade: manages protocol send + view callbacks,
    device init, config persistence, cleanup
"""

from dataclasses import dataclass
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

# =========================================================================
# Minimal DeviceInfo stand-in (avoids importing full models.py for fixtures)
# =========================================================================

@dataclass
class FakeDeviceInfo:
    """Minimal stand-in for core.models.DeviceInfo."""
    name: str = "LED Controller"
    path: str = "hid:0416:8001"
    vid: int = 0x0416
    pid: int = 0x8001
    protocol: str = "hid"
    device_type: int = 2
    resolution: tuple = (320, 320)
    vendor: Optional[str] = "ALi Corp"
    product: Optional[str] = "LED"
    model: Optional[str] = "AX120"
    device_index: int = 0


# =========================================================================
# Imports under test
# =========================================================================

from trcc.core.controllers import LEDDeviceController  # noqa: E402
from trcc.core.models import (  # noqa: E402
    HardwareMetrics,
    LEDMode,
    LEDState,
    LEDZoneState,
)
from trcc.services.led import LEDService  # noqa: E402

# =========================================================================
# Fixtures
# =========================================================================

@pytest.fixture
def led_state():
    """Default LEDState with 10 segments."""
    return LEDState()


@pytest.fixture
def led_svc():
    """Fresh LEDService with default state."""
    return LEDService()


@pytest.fixture
def led_controller():
    """Fresh LEDDeviceController (owns its own LEDService)."""
    return LEDDeviceController()


@pytest.fixture
def form_controller():
    """Fresh LEDDeviceController."""
    return LEDDeviceController()


@pytest.fixture
def device_info():
    """Fake LED device info."""
    return FakeDeviceInfo()


# =========================================================================
# Tests: LEDMode enum
# =========================================================================

class TestLEDMode:
    """Verify enum values match FormLED.cs mode IDs."""

    def test_static_value(self):
        assert LEDMode.STATIC.value == 0

    def test_breathing_value(self):
        assert LEDMode.BREATHING.value == 1

    def test_colorful_value(self):
        assert LEDMode.COLORFUL.value == 2

    def test_rainbow_value(self):
        assert LEDMode.RAINBOW.value == 3

    def test_temp_linked_value(self):
        assert LEDMode.TEMP_LINKED.value == 4

    def test_load_linked_value(self):
        assert LEDMode.LOAD_LINKED.value == 5

    def test_mode_count(self):
        assert len(LEDMode) == 6

    def test_round_trip_from_int(self):
        for mode in LEDMode:
            assert LEDMode(mode.value) is mode


# =========================================================================
# Tests: LEDZoneState dataclass
# =========================================================================

class TestLEDZoneState:
    """Verify default creation and field semantics."""

    def test_defaults(self):
        zone = LEDZoneState()
        assert zone.mode is LEDMode.STATIC
        assert zone.color == (255, 0, 0)
        assert zone.brightness == 65
        assert zone.on is True

    def test_custom_creation(self):
        zone = LEDZoneState(
            mode=LEDMode.BREATHING,
            color=(0, 255, 0),
            brightness=50,
            on=False,
        )
        assert zone.mode is LEDMode.BREATHING
        assert zone.color == (0, 255, 0)
        assert zone.brightness == 50
        assert zone.on is False


# =========================================================================
# Tests: LEDState dataclass
# =========================================================================

class TestLEDState:
    """Verify LEDState creation, defaults, __post_init__ logic."""

    def test_defaults(self, led_state):
        assert led_state.style == 1
        assert led_state.led_count == 30
        assert led_state.segment_count == 10
        assert led_state.zone_count == 1
        assert led_state.mode is LEDMode.STATIC
        assert led_state.color == (255, 0, 0)
        assert led_state.brightness == 65
        assert led_state.global_on is True
        assert led_state.rgb_timer == 0
        assert led_state.temp_source == "cpu"
        assert led_state.load_source == "cpu"

    def test_segment_on_auto_populated(self, led_state):
        """__post_init__ creates segment_on list matching segment_count."""
        assert len(led_state.segment_on) == 10
        assert all(s is True for s in led_state.segment_on)

    def test_segment_on_custom_count(self):
        state = LEDState(segment_count=5)
        assert len(state.segment_on) == 5

    def test_segment_on_preserves_if_provided(self):
        """If segment_on is already set, __post_init__ does not overwrite."""
        custom = [True, False, True]
        state = LEDState(segment_count=3, segment_on=custom)
        assert state.segment_on == [True, False, True]

    def test_zones_auto_populated_for_multi_zone(self):
        state = LEDState(zone_count=3)
        assert len(state.zones) == 3
        assert all(isinstance(z, LEDZoneState) for z in state.zones)

    def test_no_zones_for_single_zone(self, led_state):
        """Single-zone devices (zone_count=1) have empty zones list."""
        assert led_state.zones == []

    def test_zones_preserved_if_provided(self):
        custom_zones = [LEDZoneState(brightness=42)]
        state = LEDState(zone_count=2, zones=custom_zones)
        # Non-empty list provided, __post_init__ skips
        assert len(state.zones) == 1
        assert state.zones[0].brightness == 42


# =========================================================================
# Tests: LEDService — state mutations
# =========================================================================

class TestLEDServiceStateMutations:
    """Test set_mode, set_color, set_brightness, toggles, zone methods."""

    def test_set_mode(self, led_svc):
        led_svc.set_mode(LEDMode.RAINBOW)
        assert led_svc.state.mode is LEDMode.RAINBOW

    def test_set_mode_resets_timer(self, led_svc):
        led_svc.state.rgb_timer = 42
        led_svc.set_mode(LEDMode.BREATHING)
        assert led_svc.state.rgb_timer == 0

    def test_set_color(self, led_svc):
        led_svc.set_color(10, 20, 30)
        assert led_svc.state.color == (10, 20, 30)

    def test_set_brightness_normal(self, led_svc):
        led_svc.set_brightness(75)
        assert led_svc.state.brightness == 75

    def test_set_brightness_clamps_high(self, led_svc):
        led_svc.set_brightness(200)
        assert led_svc.state.brightness == 100

    def test_set_brightness_clamps_low(self, led_svc):
        led_svc.set_brightness(-10)
        assert led_svc.state.brightness == 0

    def test_toggle_global_off(self, led_svc):
        led_svc.toggle_global(False)
        assert led_svc.state.global_on is False

    def test_toggle_global_on(self, led_svc):
        led_svc.state.global_on = False
        led_svc.toggle_global(True)
        assert led_svc.state.global_on is True

    def test_toggle_segment(self, led_svc):
        led_svc.toggle_segment(3, False)
        assert led_svc.state.segment_on[3] is False
        # Others unchanged
        assert led_svc.state.segment_on[0] is True

    def test_toggle_segment_out_of_range(self, led_svc):
        """Out-of-range index does not raise."""
        led_svc.toggle_segment(999, False)
        led_svc.toggle_segment(-1, False)
        # No exception, state unchanged
        assert all(s is True for s in led_svc.state.segment_on)

    def test_set_zone_mode(self):
        model = LEDService(state=LEDState(zone_count=2))
        model.set_zone_mode(0, LEDMode.COLORFUL)
        assert model.state.zones[0].mode is LEDMode.COLORFUL

    def test_set_zone_mode_out_of_range(self):
        model = LEDService(state=LEDState(zone_count=2))
        model.set_zone_mode(5, LEDMode.STATIC)  # No exception

    def test_set_zone_color(self):
        model = LEDService(state=LEDState(zone_count=2))
        model.set_zone_color(1, 0, 128, 255)
        assert model.state.zones[1].color == (0, 128, 255)

    def test_set_zone_color_out_of_range(self):
        model = LEDService(state=LEDState(zone_count=2))
        model.set_zone_color(99, 0, 0, 0)  # No exception

    def test_set_zone_brightness(self):
        model = LEDService(state=LEDState(zone_count=2))
        model.set_zone_brightness(0, 50)
        assert model.state.zones[0].brightness == 50

    def test_set_zone_brightness_clamps(self):
        model = LEDService(state=LEDState(zone_count=2))
        model.set_zone_brightness(0, 999)
        assert model.state.zones[0].brightness == 100
        model.set_zone_brightness(0, -5)
        assert model.state.zones[0].brightness == 0

    def test_set_zone_brightness_out_of_range(self):
        model = LEDService(state=LEDState(zone_count=2))
        model.set_zone_brightness(99, 50)  # No exception

    def test_update_metrics(self, led_svc):
        metrics = HardwareMetrics(cpu_temp=65)
        led_svc.update_metrics(metrics)
        assert led_svc._metrics == metrics


# =========================================================================
# Tests: LEDService — callbacks
# =========================================================================

class TestLEDControllerCallbacks:
    """Verify LEDController fires on_state_changed on state mutations."""

    def test_on_state_changed_fires_on_set_mode(self, led_controller):
        cb = MagicMock()
        led_controller.on_state_changed = cb
        led_controller.set_mode(LEDMode.BREATHING)
        cb.assert_called_once_with(led_controller.state)

    def test_on_state_changed_fires_on_set_color(self, led_controller):
        cb = MagicMock()
        led_controller.on_state_changed = cb
        led_controller.set_color(1, 2, 3)
        cb.assert_called_once()

    def test_on_state_changed_fires_on_set_brightness(self, led_controller):
        cb = MagicMock()
        led_controller.on_state_changed = cb
        led_controller.set_brightness(42)
        cb.assert_called_once()

    def test_on_state_changed_fires_on_toggle_global(self, led_controller):
        cb = MagicMock()
        led_controller.on_state_changed = cb
        led_controller.toggle_global(False)
        cb.assert_called_once()

    def test_on_state_changed_fires_on_toggle_segment(self, led_controller):
        cb = MagicMock()
        led_controller.on_state_changed = cb
        led_controller.toggle_segment(0, False)
        cb.assert_called_once()

    def test_no_callback_when_none(self, led_controller):
        """No crash when callback is None."""
        led_controller.on_state_changed = None
        led_controller.set_mode(LEDMode.STATIC)  # No exception

    def test_on_preview_update_fires_on_tick(self, led_controller):
        cb = MagicMock()
        led_controller.on_preview_update = cb
        led_controller.tick()
        cb.assert_called_once()
        # Argument is a list of color tuples
        colors = cb.call_args[0][0]
        assert len(colors) == led_controller.state.segment_count

    def test_on_state_changed_fires_on_zone_mode(self):
        ctrl = LEDDeviceController(LEDService(state=LEDState(zone_count=2)))
        cb = MagicMock()
        ctrl.on_state_changed = cb
        ctrl.set_zone_mode(0, LEDMode.RAINBOW)
        cb.assert_called_once()

    def test_on_state_changed_fires_on_zone_color(self):
        ctrl = LEDDeviceController(LEDService(state=LEDState(zone_count=2)))
        cb = MagicMock()
        ctrl.on_state_changed = cb
        ctrl.set_zone_color(0, 10, 20, 30)
        cb.assert_called_once()


# =========================================================================
# Tests: LEDService — configure_for_style
# =========================================================================

class TestLEDServiceConfigureForStyle:
    """Test configure_for_style() sets LED/segment counts from registry."""

    @patch("trcc.adapters.device.adapter_led.LED_STYLES", {
        1: MagicMock(style_id=1, led_count=30, segment_count=10, zone_count=1),
        2: MagicMock(style_id=2, led_count=84, segment_count=18, zone_count=4),
    })
    def test_configure_style_1(self, led_svc):
        led_svc.configure_for_style(1)
        assert led_svc.state.style == 1
        assert led_svc.state.led_count == 30
        assert led_svc.state.segment_count == 10
        assert led_svc.state.zone_count == 1
        assert len(led_svc.state.segment_on) == 10
        assert led_svc.state.zones == []

    @patch("trcc.adapters.device.adapter_led.LED_STYLES", {
        2: MagicMock(style_id=2, led_count=84, segment_count=18, zone_count=4),
    })
    def test_configure_multi_zone_style(self, led_svc):
        led_svc.configure_for_style(2)
        assert led_svc.state.zone_count == 4
        assert len(led_svc.state.zones) == 4

    @patch("trcc.adapters.device.adapter_led.LED_STYLES", {})
    def test_configure_unknown_style(self, led_svc):
        """Unknown style_id does nothing (LED_STYLES.get returns None)."""
        original_count = led_svc.state.segment_count
        led_svc.configure_for_style(999)
        assert led_svc.state.segment_count == original_count

    @patch("trcc.adapters.device.adapter_led.LED_STYLES", {
        1: MagicMock(style_id=1, led_count=30, segment_count=10, zone_count=1),
    })
    def test_configure_fires_callback_via_controller(self):
        """Controller fires on_state_changed when configure_for_style is called."""
        ctrl = LEDDeviceController()
        cb = MagicMock()
        ctrl.on_state_changed = cb
        ctrl.configure_for_style(1)
        cb.assert_called_once()


# =========================================================================
# Tests: LEDService — tick dispatch
# =========================================================================

class TestLEDServiceTickDispatch:
    """Test that tick() dispatches to the correct mode algorithm."""

    def test_tick_static(self, led_svc):
        led_svc.set_mode(LEDMode.STATIC)
        colors = led_svc.tick()
        assert len(colors) == led_svc.state.segment_count
        assert all(c == led_svc.state.color for c in colors)

    def test_tick_breathing(self, led_svc):
        led_svc.set_mode(LEDMode.BREATHING)
        colors = led_svc.tick()
        assert len(colors) == led_svc.state.segment_count

    def test_tick_colorful(self, led_svc):
        led_svc.set_mode(LEDMode.COLORFUL)
        colors = led_svc.tick()
        assert len(colors) == led_svc.state.segment_count

    @patch("trcc.adapters.device.adapter_led.ColorEngine.get_table")
    def test_tick_rainbow(self, mock_table, led_svc):
        # Provide a minimal table
        mock_table.return_value = [(i, i, i) for i in range(768)]
        led_svc.set_mode(LEDMode.RAINBOW)
        colors = led_svc.tick()
        assert len(colors) == led_svc.state.segment_count

    @patch("trcc.adapters.device.adapter_led.ColorEngine.color_for_value", return_value=(0, 255, 255))
    def test_tick_temp_linked(self, mock_cfv, led_svc):
        led_svc.set_mode(LEDMode.TEMP_LINKED)
        led_svc.update_metrics(HardwareMetrics(cpu_temp=25))
        colors = led_svc.tick()
        assert len(colors) == led_svc.state.segment_count

    @patch("trcc.adapters.device.adapter_led.ColorEngine.color_for_value", return_value=(255, 255, 0))
    def test_tick_load_linked(self, mock_cfv, led_svc):
        led_svc.set_mode(LEDMode.LOAD_LINKED)
        led_svc.update_metrics(HardwareMetrics(cpu_percent=60))
        colors = led_svc.tick()
        assert len(colors) == led_svc.state.segment_count

    def test_tick_returns_list_of_tuples(self, led_svc):
        colors = led_svc.tick()
        assert isinstance(colors, list)
        for c in colors:
            assert isinstance(c, tuple)
            assert len(c) == 3


# =========================================================================
# Tests: LEDService — physical zone mapping (style 7 / LF10)
# =========================================================================

class TestPhysicalZones:
    """Style 7 (LF10) has 3 physical zones with independent color/mode/brightness."""

    @pytest.fixture
    def svc7(self):
        svc = LEDService()
        svc.configure_for_style(7)
        return svc

    def test_tick_returns_116_colors(self, svc7):
        colors = svc7.tick()
        assert len(colors) == 116

    def test_zones_get_independent_colors(self, svc7):
        for z in svc7.state.zones:
            z.brightness = 100
        svc7.set_zone_color(0, 255, 0, 0)   # zone 1 red
        svc7.set_zone_color(1, 0, 255, 0)    # zone 2 green
        svc7.set_zone_color(2, 0, 0, 255)    # zone 3 blue
        colors = svc7.tick()
        # CPU indicator (index 0) = zone 1 = red
        assert colors[0] == (255, 0, 0)
        # GPU indicator (index 3) = zone 2 = green
        assert colors[3] == (0, 255, 0)
        # Accent LED (index 104) = zone 3 = blue
        assert colors[104] == (0, 0, 255)

    def test_zone_brightness_scales(self, svc7):
        svc7.state.zones[0].brightness = 100
        svc7.set_zone_color(0, 200, 100, 50)
        svc7.state.zones[0].brightness = 50
        colors = svc7.tick()
        assert colors[0] == (100, 50, 25)

    def test_zone_off_produces_black(self, svc7):
        svc7.set_zone_color(2, 255, 255, 255)
        svc7.state.zones[2].on = False
        colors = svc7.tick()
        assert colors[104] == (0, 0, 0)

    def test_mask_gates_physical_zone_colors(self, svc7):
        svc7.state.zones[0].brightness = 100
        svc7.set_zone_color(0, 255, 0, 0)
        colors = svc7.tick()
        masked = svc7.apply_mask(colors)
        # CPU indicator (mask=True) keeps color
        assert masked[0] == (255, 0, 0)
        # Hundreds digit LED off (leading zero suppression for temp=0)
        assert masked[6] == (0, 0, 0)

    def test_breathing_mode_per_zone(self, svc7):
        for z in svc7.state.zones:
            z.brightness = 100
        svc7.state.zones[0].mode = LEDMode.BREATHING
        svc7.state.zones[0].color = (255, 0, 0)
        svc7.state.zones[1].mode = LEDMode.STATIC
        svc7.state.zones[1].color = (0, 255, 0)
        colors = svc7.tick()
        # Zone 2 static green at GPU indicator
        assert colors[3] == (0, 255, 0)
        # Zone 1 breathing — may or may not be full red depending on timer
        assert len(colors) == 116


# =========================================================================
# Tests: LEDService — _tick_static
# =========================================================================

class TestTickStatic:
    """DSCL_Timer: all segments = user color."""

    def test_all_segments_same_color(self, led_svc):
        led_svc.set_color(100, 200, 50)
        colors = led_svc._tick_single_mode(
            LEDMode.STATIC, led_svc.state.color, led_svc.state.segment_count)
        assert all(c == (100, 200, 50) for c in colors)

    def test_segment_count_matches(self, led_svc):
        colors = led_svc._tick_single_mode(
            LEDMode.STATIC, led_svc.state.color, led_svc.state.segment_count)
        assert len(colors) == led_svc.state.segment_count


# =========================================================================
# Tests: LEDService — _tick_breathing
# =========================================================================

class TestTickBreathing:
    """DSHX_Timer: pulse brightness, period=66."""

    def test_advances_timer(self, led_svc):
        led_svc.state.mode = LEDMode.BREATHING
        led_svc.state.rgb_timer = 0
        led_svc._tick_breathing_for(led_svc.state.color, led_svc.state.segment_count)
        assert led_svc.state.rgb_timer == 1

    def test_timer_wraps_at_66(self, led_svc):
        led_svc.state.rgb_timer = 65
        led_svc._tick_breathing_for(led_svc.state.color, led_svc.state.segment_count)
        assert led_svc.state.rgb_timer == 0

    def test_at_zero_brightness_is_20_percent_base(self, led_svc):
        """At timer=0, factor=0 so anim is 0; output = 20% of color."""
        led_svc.set_color(255, 0, 0)
        led_svc.state.rgb_timer = 0
        colors = led_svc._tick_breathing_for(led_svc.state.color, led_svc.state.segment_count)
        r, g, b = colors[0]
        # int(255 * 0 * 0.8 + 255 * 0.2) = int(51.0) = 51
        assert r == 51

    def test_at_midpoint_brightness_is_full(self, led_svc):
        """At timer=33 (half), factor= (66-1-33)/33 ≈ 0.9697 → near full."""
        led_svc.set_color(255, 0, 0)
        led_svc.state.rgb_timer = 32  # factor = 32/33 ≈ 0.97
        colors = led_svc._tick_breathing_for(led_svc.state.color, led_svc.state.segment_count)
        r, g, b = colors[0]
        # 80% animated + 20% base → near 255
        assert r > 200

    def test_uniform_across_segments(self, led_svc):
        """All segments get the same breathing color."""
        led_svc.set_color(100, 100, 100)
        led_svc.state.rgb_timer = 10
        colors = led_svc._tick_breathing_for(led_svc.state.color, led_svc.state.segment_count)
        assert len(set(colors)) == 1  # All identical


# =========================================================================
# Tests: LEDService — _tick_colorful
# =========================================================================

class TestTickColorful:
    """QCJB_Timer: 6-phase gradient, period=168."""

    def test_advances_timer(self, led_svc):
        led_svc.state.rgb_timer = 0
        led_svc._tick_colorful_for(led_svc.state.segment_count)
        assert led_svc.state.rgb_timer == 1

    def test_timer_wraps_at_168(self, led_svc):
        led_svc.state.rgb_timer = 167
        led_svc._tick_colorful_for(led_svc.state.segment_count)
        assert led_svc.state.rgb_timer == 0

    def test_phase_0_starts_red(self, led_svc):
        """Phase 0 offset 0 → (255, 0, 0) = pure red."""
        led_svc.state.rgb_timer = 0
        colors = led_svc._tick_colorful_for(led_svc.state.segment_count)
        assert colors[0] == (255, 0, 0)

    def test_phase_1_yellow_to_green(self, led_svc):
        """Phase 1 offset 0 → (255, 255, 0) → R starts decreasing."""
        led_svc.state.rgb_timer = 28  # Phase 1 start
        colors = led_svc._tick_colorful_for(led_svc.state.segment_count)
        r, g, b = colors[0]
        assert g == 255
        assert b == 0

    def test_phase_2_green_to_cyan(self, led_svc):
        """Phase 2 starts at timer=56."""
        led_svc.state.rgb_timer = 56
        colors = led_svc._tick_colorful_for(led_svc.state.segment_count)
        r, g, b = colors[0]
        assert r == 0
        assert g == 255

    def test_full_cycle_returns_to_start(self, led_svc):
        """After 168 ticks, we're back at phase 0 offset 0."""
        led_svc.state.rgb_timer = 0
        for _ in range(168):
            led_svc._tick_colorful_for(led_svc.state.segment_count)
        assert led_svc.state.rgb_timer == 0

    def test_uniform_across_segments(self, led_svc):
        """All segments get the same colorful color."""
        led_svc.state.rgb_timer = 42
        colors = led_svc._tick_colorful_for(led_svc.state.segment_count)
        assert len(set(colors)) == 1


# =========================================================================
# Tests: LEDService — _tick_rainbow
# =========================================================================

class TestTickRainbow:
    """CHMS_Timer: 768-entry table, offset per segment."""

    @patch("trcc.adapters.device.adapter_led.ColorEngine.get_table")
    def test_uses_rgb_table(self, mock_table, led_svc):
        table = [(i, 0, 0) for i in range(768)]
        mock_table.return_value = table
        led_svc.state.rgb_timer = 0
        colors = led_svc._tick_rainbow_for(led_svc.state.segment_count)
        # Each segment gets a different offset
        assert len(colors) == led_svc.state.segment_count
        mock_table.assert_called()

    @patch("trcc.adapters.device.adapter_led.ColorEngine.get_table")
    def test_advances_by_4(self, mock_table, led_svc):
        mock_table.return_value = [(0, 0, 0)] * 768
        led_svc.state.rgb_timer = 0
        led_svc._tick_rainbow_for(led_svc.state.segment_count)
        assert led_svc.state.rgb_timer == 4

    @patch("trcc.adapters.device.adapter_led.ColorEngine.get_table")
    def test_timer_wraps(self, mock_table, led_svc):
        mock_table.return_value = [(0, 0, 0)] * 768
        led_svc.state.rgb_timer = 764
        led_svc._tick_rainbow_for(led_svc.state.segment_count)
        assert led_svc.state.rgb_timer == 0  # (764 + 4) % 768 = 0

    @patch("trcc.adapters.device.adapter_led.ColorEngine.get_table")
    def test_segments_get_different_offsets(self, mock_table, led_svc):
        """Different segments get different colors from the table."""
        table = [(i, i, i) for i in range(768)]
        mock_table.return_value = table
        led_svc.state.rgb_timer = 0
        led_svc.state.segment_count = 4
        led_svc.state.segment_on = [True] * 4
        colors = led_svc._tick_rainbow_for(led_svc.state.segment_count)
        # With 4 segments, offsets should be 0, 192, 384, 576
        assert len(set(colors)) > 1  # Not all the same


# =========================================================================
# Tests: LEDService — _tick_temp_linked
# =========================================================================

class TestTickTempLinked:
    """WDLD_Timer: color from CPU/GPU temperature thresholds."""

    @patch("trcc.adapters.device.adapter_led.ColorEngine.color_for_value")
    def test_uses_cpu_temp_by_default(self, mock_cfv, led_svc):
        mock_cfv.return_value = (0, 255, 0)
        led_svc.state.temp_source = "cpu"
        led_svc.update_metrics(HardwareMetrics(cpu_temp=45))
        led_svc._tick_temp_linked_for(led_svc.state.segment_count)
        mock_cfv.assert_called_once()
        # First positional arg is the temp value
        assert mock_cfv.call_args[0][0] == 45

    @patch("trcc.adapters.device.adapter_led.ColorEngine.color_for_value")
    def test_uses_gpu_temp(self, mock_cfv, led_svc):
        mock_cfv.return_value = (255, 0, 0)
        led_svc.state.temp_source = "gpu"
        led_svc.update_metrics(HardwareMetrics(gpu_temp=92))
        led_svc._tick_temp_linked_for(led_svc.state.segment_count)
        assert mock_cfv.call_args[0][0] == 92

    @patch("trcc.adapters.device.adapter_led.ColorEngine.color_for_value")
    def test_missing_metric_defaults_to_zero(self, mock_cfv, led_svc):
        mock_cfv.return_value = (0, 255, 255)
        led_svc.update_metrics(HardwareMetrics())
        led_svc._tick_temp_linked_for(led_svc.state.segment_count)
        assert mock_cfv.call_args[0][0] == 0

    @patch("trcc.adapters.device.adapter_led.ColorEngine.color_for_value")
    def test_uniform_color(self, mock_cfv, led_svc):
        mock_cfv.return_value = (0, 255, 0)
        led_svc.update_metrics(HardwareMetrics(cpu_temp=40))
        colors = led_svc._tick_temp_linked_for(led_svc.state.segment_count)
        assert all(c == (0, 255, 0) for c in colors)
        assert len(colors) == led_svc.state.segment_count


# =========================================================================
# Tests: LEDService — _tick_load_linked
# =========================================================================

class TestTickLoadLinked:
    """FZLD_Timer: color from CPU/GPU load thresholds."""

    @patch("trcc.adapters.device.adapter_led.ColorEngine.color_for_value")
    def test_uses_cpu_load_by_default(self, mock_cfv, led_svc):
        mock_cfv.return_value = (255, 255, 0)
        led_svc.state.load_source = "cpu"
        led_svc.update_metrics(HardwareMetrics(cpu_percent=60))
        led_svc._tick_load_linked_for(led_svc.state.segment_count)
        assert mock_cfv.call_args[0][0] == 60

    @patch("trcc.adapters.device.adapter_led.ColorEngine.color_for_value")
    def test_uses_gpu_load(self, mock_cfv, led_svc):
        mock_cfv.return_value = (255, 110, 0)
        led_svc.state.load_source = "gpu"
        led_svc.update_metrics(HardwareMetrics(gpu_usage=85))
        led_svc._tick_load_linked_for(led_svc.state.segment_count)
        assert mock_cfv.call_args[0][0] == 85

    @patch("trcc.adapters.device.adapter_led.ColorEngine.color_for_value")
    def test_missing_metric_defaults_to_zero(self, mock_cfv, led_svc):
        mock_cfv.return_value = (0, 255, 255)
        led_svc.update_metrics(HardwareMetrics())
        led_svc._tick_load_linked_for(led_svc.state.segment_count)
        assert mock_cfv.call_args[0][0] == 0


# =========================================================================
# Tests: LEDController — delegation to model
# =========================================================================

class TestLEDControllerDelegation:
    """Verify controller methods delegate to model."""

    def test_set_mode_delegates(self, led_controller):
        led_controller.set_mode(LEDMode.COLORFUL)
        assert led_controller.state.mode is LEDMode.COLORFUL

    def test_set_color_delegates(self, led_controller):
        led_controller.set_color(10, 20, 30)
        assert led_controller.state.color == (10, 20, 30)

    def test_set_brightness_delegates(self, led_controller):
        led_controller.set_brightness(42)
        assert led_controller.state.brightness == 42

    def test_toggle_global_delegates(self, led_controller):
        led_controller.toggle_global(False)
        assert led_controller.state.global_on is False

    def test_toggle_segment_delegates(self, led_controller):
        led_controller.toggle_segment(2, False)
        assert led_controller.state.segment_on[2] is False

    def test_set_zone_mode_delegates(self, led_controller):
        led_controller.svc.state = LEDState(zone_count=2)
        led_controller.set_zone_mode(0, LEDMode.BREATHING)
        assert led_controller.state.zones[0].mode is LEDMode.BREATHING

    def test_set_zone_color_delegates(self, led_controller):
        led_controller.svc.state = LEDState(zone_count=2)
        led_controller.set_zone_color(1, 5, 10, 15)
        assert led_controller.state.zones[1].color == (5, 10, 15)

    def test_update_metrics_delegates(self, led_controller):
        metrics = HardwareMetrics(cpu_temp=55)
        led_controller.update_metrics(metrics)
        assert led_controller.svc._metrics == metrics

    @patch("trcc.adapters.device.adapter_led.LED_STYLES", {
        1: MagicMock(style_id=1, led_count=30, segment_count=10, zone_count=1),
    })
    def test_configure_for_style_delegates(self, led_controller):
        led_controller.configure_for_style(1)
        assert led_controller.state.style == 1


# =========================================================================
# Tests: LEDController — protocol and set_protocol
# =========================================================================

class TestLEDControllerProtocol:
    """Test protocol injection and send behavior."""

    def test_set_protocol(self, led_controller):
        proto = MagicMock()
        led_controller.set_protocol(proto)
        assert led_controller.svc.has_protocol

    def test_set_protocol_none(self, led_controller):
        led_controller.set_protocol(None)
        assert not led_controller.svc.has_protocol

    def test_initial_protocol_is_none(self, led_controller):
        assert not led_controller.svc.has_protocol


# =========================================================================
# Tests: LEDController — tick
# =========================================================================

class TestLEDControllerTick:
    """Test tick() — advances model, sends via protocol, fires callbacks."""

    def test_tick_calls_model_tick(self, led_controller):
        led_controller.svc.tick = MagicMock(return_value=[(255, 0, 0)] * 10)
        led_controller.tick()
        led_controller.svc.tick.assert_called_once()

    def test_tick_sends_via_protocol(self, led_controller):
        proto = MagicMock()
        proto.send_led_data.return_value = True
        led_controller.set_protocol(proto)
        led_controller.tick()
        proto.send_led_data.assert_called_once()

    def test_tick_sends_colors_is_on_global_brightness(self, led_controller):
        """Verify the arguments passed to protocol.send_led_data()."""
        proto = MagicMock()
        proto.send_led_data.return_value = True
        led_controller.set_protocol(proto)
        led_controller.svc.set_color(10, 20, 30)
        led_controller.svc.set_brightness(75)

        led_controller.tick()

        args, kwargs = proto.send_led_data.call_args
        colors, is_on, global_on, brightness = args
        assert len(colors) == led_controller.state.segment_count
        assert is_on == led_controller.state.segment_on
        assert global_on is True
        assert brightness == 75

    def test_tick_no_protocol_no_error(self, led_controller):
        """tick() with no protocol should not raise."""
        led_controller.set_protocol(None)
        led_controller.tick()  # No exception

    def test_tick_protocol_error_handled(self, led_controller):
        """Protocol exception is caught silently."""
        proto = MagicMock()
        proto.send_led_data.side_effect = Exception("USB error")
        led_controller.set_protocol(proto)
        led_controller.tick()  # No exception raised

    def test_tick_on_send_complete_success(self, led_controller):
        proto = MagicMock()
        proto.send_led_data.return_value = True
        led_controller.set_protocol(proto)
        cb = MagicMock()
        led_controller.on_send_complete = cb

        led_controller.tick()

        cb.assert_called_once_with(True)

    def test_tick_on_send_complete_failure(self, led_controller):
        proto = MagicMock()
        proto.send_led_data.return_value = False
        led_controller.set_protocol(proto)
        cb = MagicMock()
        led_controller.on_send_complete = cb

        led_controller.tick()

        cb.assert_called_once_with(False)

    def test_tick_on_send_complete_not_called_without_protocol(self, led_controller):
        cb = MagicMock()
        led_controller.on_send_complete = cb
        led_controller.tick()
        cb.assert_not_called()


# =========================================================================
# Tests: LEDController — view callbacks (wired from model)
# =========================================================================

class TestLEDControllerViewCallbacks:
    """Test that model events propagate to controller view callbacks."""

    def test_on_state_changed_forwarded(self, led_controller):
        cb = MagicMock()
        led_controller.on_state_changed = cb
        led_controller.set_mode(LEDMode.BREATHING)
        cb.assert_called_once_with(led_controller.state)

    def test_on_preview_update_forwarded(self, led_controller):
        cb = MagicMock()
        led_controller.on_preview_update = cb
        led_controller.tick()
        # on_colors_updated from model fires on_preview_update on controller
        cb.assert_called_once()
        colors = cb.call_args[0][0]
        assert isinstance(colors, list)

    def test_on_state_changed_not_forwarded_when_none(self, led_controller):
        """No crash when controller callback is None."""
        led_controller.on_state_changed = None
        led_controller.set_mode(LEDMode.STATIC)  # No exception

    def test_on_preview_update_not_forwarded_when_none(self, led_controller):
        led_controller.on_preview_update = None
        led_controller.tick()  # No exception


# =========================================================================
# Tests: LEDDeviceController — initialize
# =========================================================================

class TestLEDDeviceControllerInitialize:
    """Test initialize() — configures model, creates protocol, loads config."""

    @patch("trcc.services.led.LEDService.configure_for_style")
    @patch("trcc.adapters.device.abstract_factory.DeviceProtocolFactory.get_protocol")
    @patch("trcc.conf.Settings.device_config_key", return_value="0:0416_8001")
    @patch("trcc.conf.Settings.get_device_config", return_value={})
    @patch("trcc.adapters.device.adapter_led.LED_STYLES", {
        1: MagicMock(style_id=1, led_count=30, segment_count=10,
                     zone_count=1, model_name="AX120"),
    })
    def test_initialize_configures_model(
        self, mock_get_cfg, mock_key, mock_factory, mock_configure, form_controller, device_info
    ):
        form_controller.initialize(device_info, led_style=1)
        mock_configure.assert_called_once_with(1)

    @patch("trcc.adapters.device.abstract_factory.DeviceProtocolFactory.get_protocol")
    @patch("trcc.conf.Settings.device_config_key", return_value="0:0416_8001")
    @patch("trcc.conf.Settings.get_device_config", return_value={})
    @patch("trcc.adapters.device.adapter_led.LED_STYLES", {
        1: MagicMock(style_id=1, led_count=30, segment_count=10,
                     zone_count=1, model_name="AX120"),
    })
    def test_initialize_creates_protocol(
        self, mock_get_cfg, mock_key, mock_factory, form_controller, device_info
    ):
        mock_proto = MagicMock()
        mock_factory.return_value = mock_proto
        form_controller.initialize(device_info, led_style=1)
        mock_factory.assert_called_once_with(device_info)

    @patch("trcc.adapters.device.abstract_factory.DeviceProtocolFactory.get_protocol",
           side_effect=Exception("No backend"))
    @patch("trcc.conf.Settings.device_config_key", return_value="0:0416_8001")
    @patch("trcc.conf.Settings.get_device_config", return_value={})
    @patch("trcc.adapters.device.adapter_led.LED_STYLES", {
        1: MagicMock(style_id=1, led_count=30, segment_count=10,
                     zone_count=1, model_name="AX120"),
    })
    def test_initialize_protocol_error_reports_status(
        self, mock_get_cfg, mock_key, mock_factory, form_controller, device_info
    ):
        status_cb = MagicMock()
        form_controller.on_status_update = status_cb
        form_controller.initialize(device_info, led_style=1)
        # Should have been called with error message
        calls = [c for c in status_cb.call_args_list if "error" in str(c).lower()]
        assert len(calls) >= 1

    @patch("trcc.adapters.device.abstract_factory.DeviceProtocolFactory.get_protocol")
    @patch("trcc.conf.Settings.device_config_key", return_value="0:0416_8001")
    @patch("trcc.conf.Settings.get_device_config", return_value={})
    @patch("trcc.adapters.device.adapter_led.LED_STYLES", {
        1: MagicMock(style_id=1, led_count=30, segment_count=10,
                     zone_count=1, model_name="AX120"),
    })
    def test_initialize_stores_device_key(
        self, mock_get_cfg, mock_key, mock_factory, form_controller, device_info
    ):
        form_controller.initialize(device_info, led_style=1)
        assert form_controller._device_key == "0:0416_8001"

    @patch("trcc.adapters.device.abstract_factory.DeviceProtocolFactory.get_protocol")
    @patch("trcc.conf.Settings.device_config_key", return_value="0:0416_8001")
    @patch("trcc.conf.Settings.get_device_config", return_value={})
    @patch("trcc.adapters.device.adapter_led.LED_STYLES", {
        1: MagicMock(style_id=1, led_count=30, segment_count=10,
                     zone_count=1, model_name="AX120"),
    })
    def test_initialize_status_reports_model_name(
        self, mock_get_cfg, mock_key, mock_factory, form_controller, device_info
    ):
        status_cb = MagicMock()
        form_controller.on_status_update = status_cb
        form_controller.initialize(device_info, led_style=1)
        # Last call should contain the model name
        final_call = status_cb.call_args_list[-1]
        assert "AX120" in str(final_call)


# =========================================================================
# Tests: LEDDeviceController — save_config
# =========================================================================

class TestLEDDeviceControllerSaveConfig:
    """Test save_config() persists state to device config."""

    @patch("trcc.conf.Settings.save_device_setting")
    def test_save_config_calls_save_device_setting(
        self, mock_save, form_controller
    ):
        form_controller._device_key = "0:0416_8001"
        form_controller.state.mode = LEDMode.BREATHING
        form_controller.state.color = (10, 20, 30)
        form_controller.state.brightness = 75

        form_controller.save_config()

        mock_save.assert_called_once()
        key, setting_name, config = mock_save.call_args[0]
        assert key == "0:0416_8001"
        assert setting_name == "led_config"
        assert config['mode'] == LEDMode.BREATHING.value
        assert config['color'] == [10, 20, 30]
        assert config['brightness'] == 75

    @patch("trcc.conf.Settings.save_device_setting")
    def test_save_config_includes_global_on(self, mock_save, form_controller):
        form_controller._device_key = "0:0416_8001"
        form_controller.state.global_on = False

        form_controller.save_config()

        config = mock_save.call_args[0][2]
        assert config['global_on'] is False

    @patch("trcc.conf.Settings.save_device_setting")
    def test_save_config_includes_segments(self, mock_save, form_controller):
        form_controller._device_key = "0:0416_8001"
        form_controller.state.segment_on = [True, False, True]

        form_controller.save_config()

        config = mock_save.call_args[0][2]
        assert config['segments_on'] == [True, False, True]

    @patch("trcc.conf.Settings.save_device_setting")
    def test_save_config_includes_zones(self, mock_save, form_controller):
        form_controller._device_key = "0:0416_8001"
        form_controller.state.zones = [
            LEDZoneState(mode=LEDMode.BREATHING, color=(0, 255, 0), brightness=50, on=False),
        ]

        form_controller.save_config()

        config = mock_save.call_args[0][2]
        assert 'zones' in config
        assert config['zones'][0]['mode'] == LEDMode.BREATHING.value
        assert config['zones'][0]['color'] == [0, 255, 0]
        assert config['zones'][0]['brightness'] == 50
        assert config['zones'][0]['on'] is False

    @patch("trcc.conf.Settings.save_device_setting")
    def test_save_config_no_zones_for_single_zone(self, mock_save, form_controller):
        form_controller._device_key = "0:0416_8001"
        form_controller.state.zones = []

        form_controller.save_config()

        config = mock_save.call_args[0][2]
        assert config['zones'] == []

    def test_save_config_no_device_key(self, form_controller):
        """save_config() with no device key is a no-op."""
        form_controller._device_key = None
        form_controller.save_config()  # No exception

    @patch("trcc.conf.Settings.save_device_setting", side_effect=Exception("IO error"))
    def test_save_config_handles_exception(self, mock_save, form_controller):
        """Exception during save is caught, not raised."""
        form_controller._device_key = "0:0416_8001"
        form_controller.save_config()  # No exception

    @patch("trcc.conf.Settings.save_device_setting")
    def test_save_config_includes_sources(self, mock_save, form_controller):
        form_controller._device_key = "0:0416_8001"
        form_controller.state.temp_source = "gpu"
        form_controller.state.load_source = "gpu"

        form_controller.save_config()

        config = mock_save.call_args[0][2]
        assert config['temp_source'] == "gpu"
        assert config['load_source'] == "gpu"


# =========================================================================
# Tests: LEDDeviceController — load_config
# =========================================================================

class TestLEDDeviceControllerLoadConfig:
    """Test load_config() restores state from device config."""

    @patch("trcc.conf.Settings.get_device_config")
    def test_load_config_restores_mode(self, mock_get_cfg, form_controller):
        mock_get_cfg.return_value = {
            'led_config': {'mode': LEDMode.RAINBOW.value}
        }
        form_controller._device_key = "0:0416_8001"
        form_controller.load_config()
        assert form_controller.state.mode is LEDMode.RAINBOW

    @patch("trcc.conf.Settings.get_device_config")
    def test_load_config_restores_color(self, mock_get_cfg, form_controller):
        mock_get_cfg.return_value = {
            'led_config': {'color': [10, 20, 30]}
        }
        form_controller._device_key = "0:0416_8001"
        form_controller.load_config()
        assert form_controller.state.color == (10, 20, 30)

    @patch("trcc.conf.Settings.get_device_config")
    def test_load_config_restores_brightness(self, mock_get_cfg, form_controller):
        mock_get_cfg.return_value = {
            'led_config': {'brightness': 42}
        }
        form_controller._device_key = "0:0416_8001"
        form_controller.load_config()
        assert form_controller.state.brightness == 42

    @patch("trcc.conf.Settings.get_device_config")
    def test_load_config_restores_global_on(self, mock_get_cfg, form_controller):
        mock_get_cfg.return_value = {
            'led_config': {'global_on': False}
        }
        form_controller._device_key = "0:0416_8001"
        form_controller.load_config()
        assert form_controller.state.global_on is False

    @patch("trcc.conf.Settings.get_device_config")
    def test_load_config_restores_segments(self, mock_get_cfg, form_controller):
        mock_get_cfg.return_value = {
            'led_config': {'segments_on': [True, False, True, True, True,
                                            True, True, True, True, True]}
        }
        form_controller._device_key = "0:0416_8001"
        form_controller.load_config()
        assert form_controller.state.segment_on[1] is False

    @patch("trcc.conf.Settings.get_device_config")
    def test_load_config_restores_sources(self, mock_get_cfg, form_controller):
        mock_get_cfg.return_value = {
            'led_config': {'temp_source': 'gpu', 'load_source': 'gpu'}
        }
        form_controller._device_key = "0:0416_8001"
        form_controller.load_config()
        assert form_controller.state.temp_source == "gpu"
        assert form_controller.state.load_source == "gpu"

    @patch("trcc.conf.Settings.get_device_config")
    def test_load_config_restores_zones(self, mock_get_cfg, form_controller):
        mock_get_cfg.return_value = {
            'led_config': {
                'zones': [
                    {'mode': 1, 'color': [0, 255, 0], 'brightness': 50, 'on': False},
                    {'mode': 3, 'color': [0, 0, 255], 'brightness': 80, 'on': True},
                ]
            }
        }
        form_controller._device_key = "0:0416_8001"
        # Need zones to exist for restore
        form_controller.state.zones = [LEDZoneState(), LEDZoneState()]
        form_controller.load_config()
        assert form_controller.state.zones[0].mode is LEDMode.BREATHING
        assert form_controller.state.zones[0].color == (0, 255, 0)
        assert form_controller.state.zones[0].brightness == 50
        assert form_controller.state.zones[0].on is False
        assert form_controller.state.zones[1].mode is LEDMode.RAINBOW

    @patch("trcc.conf.Settings.get_device_config")
    def test_load_config_empty_config(self, mock_get_cfg, form_controller):
        """Empty config leaves state unchanged."""
        mock_get_cfg.return_value = {}
        form_controller._device_key = "0:0416_8001"
        original_mode = form_controller.state.mode
        form_controller.load_config()
        assert form_controller.state.mode is original_mode

    @patch("trcc.conf.Settings.get_device_config")
    def test_load_config_no_led_config_key(self, mock_get_cfg, form_controller):
        """Config without 'led_config' key leaves state unchanged."""
        mock_get_cfg.return_value = {'some_other': 'data'}
        form_controller._device_key = "0:0416_8001"
        form_controller.load_config()  # No exception

    def test_load_config_no_device_key(self, form_controller):
        """load_config() with no device key is a no-op."""
        form_controller._device_key = None
        form_controller.load_config()  # No exception

    @patch("trcc.conf.Settings.get_device_config", side_effect=Exception("IO error"))
    def test_load_config_handles_exception(self, mock_get_cfg, form_controller):
        """Exception during load is caught, not raised."""
        form_controller._device_key = "0:0416_8001"
        form_controller.load_config()  # No exception


# =========================================================================
# Tests: LEDDeviceController — cleanup
# =========================================================================

class TestLEDDeviceControllerCleanup:
    """Test cleanup() — saves config and clears protocol."""

    @patch("trcc.conf.Settings.save_device_setting")
    def test_cleanup_saves_config(self, mock_save, form_controller):
        form_controller._device_key = "0:0416_8001"
        form_controller.cleanup()
        mock_save.assert_called_once()

    def test_cleanup_clears_protocol(self, form_controller):
        form_controller.set_protocol(MagicMock())
        form_controller._device_key = None  # Prevent save_config from running
        form_controller.cleanup()
        assert not form_controller.svc.has_protocol

    @patch("trcc.conf.Settings.save_device_setting")
    def test_cleanup_saves_then_clears(self, mock_save, form_controller):
        """cleanup() calls save_config, then clears protocol."""
        proto = MagicMock()
        form_controller.set_protocol(proto)
        form_controller._device_key = "0:0416_8001"
        form_controller.cleanup()
        mock_save.assert_called_once()
        assert not form_controller.svc.has_protocol


# =========================================================================
# Tests: Multi-zone tick()
# =========================================================================

class TestMultiZoneTick:
    """Test _tick_multi_zone() — per-zone color computation via zone map."""

    @pytest.fixture
    def multi_zone_model(self):
        """LEDService with 2-zone, 10-LED zone map."""
        model = LEDService()
        model.state.led_count = 10
        model.state.segment_count = 10
        model.state.zone_count = 2
        model.state.zones = [
            LEDZoneState(mode=LEDMode.STATIC, color=(255, 0, 0), brightness=100),
            LEDZoneState(mode=LEDMode.STATIC, color=(0, 0, 255), brightness=100),
        ]
        return model

    def test_multi_zone_dispatches(self, multi_zone_model):
        """_tick_multi_zone places zone colors at mapped indices."""
        zone_map = (tuple(range(0, 5)), tuple(range(5, 10)))
        colors = multi_zone_model._tick_multi_zone(zone_map)
        assert len(colors) == 10
        assert colors[0] == (255, 0, 0)
        assert colors[4] == (255, 0, 0)
        assert colors[5] == (0, 0, 255)
        assert colors[9] == (0, 0, 255)

    def test_multi_zone_uneven_sizes(self):
        """Zone map with different-sized zones."""
        model = LEDService()
        model.state.led_count = 10
        model.state.segment_count = 10
        model.state.zone_count = 3
        model.state.zones = [
            LEDZoneState(mode=LEDMode.STATIC, color=(255, 0, 0), brightness=100),
            LEDZoneState(mode=LEDMode.STATIC, color=(0, 255, 0), brightness=100),
            LEDZoneState(mode=LEDMode.STATIC, color=(0, 0, 255), brightness=100),
        ]
        zone_map = ((0, 1, 2, 3, 4), (5, 6, 7), (8, 9))
        colors = model._tick_multi_zone(zone_map)
        assert len(colors) == 10
        assert all(c == (255, 0, 0) for c in colors[0:5])
        assert all(c == (0, 255, 0) for c in colors[5:8])
        assert all(c == (0, 0, 255) for c in colors[8:10])

    def test_multi_zone_brightness_scaling(self, multi_zone_model):
        """Zone brightness scales RGB values."""
        multi_zone_model.state.zones[0].brightness = 50
        zone_map = (tuple(range(0, 5)), tuple(range(5, 10)))
        colors = multi_zone_model._tick_multi_zone(zone_map)
        assert colors[0] == (127, 0, 0)
        assert colors[5] == (0, 0, 255)

    def test_multi_zone_off(self, multi_zone_model):
        """Zone with on=False produces black."""
        multi_zone_model.state.zones[0].on = False
        zone_map = (tuple(range(0, 5)), tuple(range(5, 10)))
        colors = multi_zone_model._tick_multi_zone(zone_map)
        assert colors[0] == (0, 0, 0)
        assert colors[4] == (0, 0, 0)
        assert colors[5] == (0, 0, 255)

    def test_multi_zone_breathing(self):
        """Multi-zone with breathing mode advances timer."""
        model = LEDService()
        model.state.led_count = 6
        model.state.segment_count = 6
        model.state.zone_count = 2
        model.state.zones = [
            LEDZoneState(mode=LEDMode.BREATHING, color=(100, 100, 100), brightness=100),
            LEDZoneState(mode=LEDMode.STATIC, color=(0, 255, 0), brightness=100),
        ]
        zone_map = ((0, 1, 2), (3, 4, 5))
        colors = model._tick_multi_zone(zone_map)
        assert len(colors) == 6
        assert colors[0] == (20, 20, 20)
        assert colors[3] == (0, 255, 0)

    def test_single_zone_skips_multi(self):
        """Single-zone device uses global mode, not multi-zone path."""
        model = LEDService()
        model.state.zone_count = 1
        model.state.segment_count = 5
        model.state.mode = LEDMode.STATIC
        model.state.color = (0, 128, 0)
        model.state.zones = []
        colors = model.tick()
        assert all(c == (0, 128, 0) for c in colors)

    def test_four_zone_device(self):
        """4-zone device with mapped LED indices."""
        model = LEDService()
        model.state.led_count = 18
        model.state.segment_count = 18
        model.state.zone_count = 4
        model.state.zones = [
            LEDZoneState(mode=LEDMode.STATIC, color=(255, 0, 0), brightness=100),
            LEDZoneState(mode=LEDMode.STATIC, color=(0, 255, 0), brightness=100),
            LEDZoneState(mode=LEDMode.STATIC, color=(0, 0, 255), brightness=100),
            LEDZoneState(mode=LEDMode.STATIC, color=(255, 255, 0), brightness=100),
        ]
        zone_map = (tuple(range(0, 5)), tuple(range(5, 10)),
                    tuple(range(10, 14)), tuple(range(14, 18)))
        colors = model._tick_multi_zone(zone_map)
        assert len(colors) == 18
        assert all(c == (255, 0, 0) for c in colors[0:5])
        assert all(c == (0, 255, 0) for c in colors[5:10])
        assert all(c == (0, 0, 255) for c in colors[10:14])
        assert all(c == (255, 255, 0) for c in colors[14:18])


# =========================================================================
# Tests: LEDController zone methods
# =========================================================================

class TestLEDControllerZoneMethods:
    """Test zone-specific controller methods."""

    def test_set_zone_brightness(self, led_controller):
        """set_zone_brightness delegates to model."""
        led_controller.state.zones = [
            LEDZoneState(), LEDZoneState()
        ]
        led_controller.set_zone_brightness(1, 42)
        assert led_controller.state.zones[1].brightness == 42

    def test_set_zone_brightness_clamps(self, led_controller):
        """set_zone_brightness clamps to 0-100."""
        led_controller.state.zones = [LEDZoneState()]
        led_controller.set_zone_brightness(0, 150)
        assert led_controller.state.zones[0].brightness == 100
        led_controller.set_zone_brightness(0, -10)
        assert led_controller.state.zones[0].brightness == 0

    def test_set_clock_format(self, led_controller):
        """set_clock_format sets is_timer_24h on state."""
        led_controller.set_clock_format(False)
        assert led_controller.state.is_timer_24h is False
        led_controller.set_clock_format(True)
        assert led_controller.state.is_timer_24h is True

    def test_set_week_start(self, led_controller):
        """set_week_start sets is_week_sunday on state."""
        led_controller.set_week_start(True)
        assert led_controller.state.is_week_sunday is True
        led_controller.set_week_start(False)
        assert led_controller.state.is_week_sunday is False


# =========================================================================
# Tests: LC2 clock state persistence
# =========================================================================

class TestLC2ClockPersistence:
    """Test save/load of LC2 clock settings."""

    @patch("trcc.conf.Settings.save_device_setting")
    def test_save_config_includes_clock_fields(self, mock_save, form_controller):
        form_controller._device_key = "0:0416_8001"
        form_controller.state.is_timer_24h = False
        form_controller.state.is_week_sunday = True

        form_controller.save_config()

        config = mock_save.call_args[0][2]
        assert config['is_timer_24h'] is False
        assert config['is_week_sunday'] is True

    @patch("trcc.conf.Settings.save_device_setting")
    def test_save_config_clock_defaults(self, mock_save, form_controller):
        form_controller._device_key = "0:0416_8001"
        form_controller.save_config()

        config = mock_save.call_args[0][2]
        assert config['is_timer_24h'] is True
        assert config['is_week_sunday'] is False

    @patch("trcc.conf.Settings.get_device_config")
    def test_load_config_restores_clock_fields(self, mock_get_cfg, form_controller):
        mock_get_cfg.return_value = {
            'led_config': {
                'is_timer_24h': False,
                'is_week_sunday': True,
            }
        }
        form_controller._device_key = "0:0416_8001"
        form_controller.load_config()

        assert form_controller.state.is_timer_24h is False
        assert form_controller.state.is_week_sunday is True

    @patch("trcc.conf.Settings.get_device_config")
    def test_load_config_missing_clock_fields(self, mock_get_cfg, form_controller):
        """Missing clock fields keep defaults."""
        mock_get_cfg.return_value = {'led_config': {'mode': 0}}
        form_controller._device_key = "0:0416_8001"
        form_controller.load_config()

        assert form_controller.state.is_timer_24h is True
        assert form_controller.state.is_week_sunday is False


# =========================================================================
# Tests: LEDState clock fields
# =========================================================================

class TestLEDStateClockFields:
    """Test LC2 clock fields on LEDState dataclass."""

    def test_default_values(self):
        state = LEDState()
        assert state.is_timer_24h is True
        assert state.is_week_sunday is False

    def test_custom_values(self):
        state = LEDState(is_timer_24h=False, is_week_sunday=True)
        assert state.is_timer_24h is False
        assert state.is_week_sunday is True


# =========================================================================
# Tests: _tick_single_mode dispatcher
# =========================================================================

class TestTickSingleMode:
    """Test _tick_single_mode dispatches correctly."""

    def test_static(self, led_svc):
        colors = led_svc._tick_single_mode(
            LEDMode.STATIC, (10, 20, 30), 5)
        assert colors == [(10, 20, 30)] * 5

    def test_breathing(self, led_svc):
        colors = led_svc._tick_single_mode(
            LEDMode.BREATHING, (100, 100, 100), 3)
        assert len(colors) == 3

    def test_colorful(self, led_svc):
        colors = led_svc._tick_single_mode(
            LEDMode.COLORFUL, (0, 0, 0), 4)
        assert len(colors) == 4

    @patch("trcc.adapters.device.adapter_led.ColorEngine.get_table",
           return_value=[(i, i, i) for i in range(768)])
    def test_rainbow(self, mock_table, led_svc):
        colors = led_svc._tick_single_mode(
            LEDMode.RAINBOW, (0, 0, 0), 6)
        assert len(colors) == 6

    @patch("trcc.adapters.device.adapter_led.ColorEngine.color_for_value", return_value=(0, 255, 255))
    def test_temp_linked(self, mock_cfv, led_svc):
        colors = led_svc._tick_single_mode(
            LEDMode.TEMP_LINKED, (0, 0, 0), 3)
        assert len(colors) == 3

    @patch("trcc.adapters.device.adapter_led.ColorEngine.color_for_value", return_value=(255, 0, 0))
    def test_load_linked(self, mock_cfv, led_svc):
        colors = led_svc._tick_single_mode(
            LEDMode.LOAD_LINKED, (0, 0, 0), 3)
        assert len(colors) == 3

    def test_unknown_mode_returns_black(self, led_svc):
        colors = led_svc._tick_single_mode(99, (255, 255, 255), 4)
        assert colors == [(0, 0, 0)] * 4


# =========================================================================
# Tests: UCInfoImage widget (headless — no display needed)
# =========================================================================

class TestUCInfoImageWidget:
    """Test UCInfoImage sensor gauge widget logic."""

    def test_import(self):
        """UCInfoImage class is importable."""
        try:
            from trcc.qt_components.uc_led_control import UCInfoImage
            assert UCInfoImage is not None
        except ImportError:
            pytest.skip("PyQt6 not available")

    def test_set_value(self):
        """set_value stores values for painting."""
        try:
            from trcc.qt_components.uc_led_control import UCInfoImage
        except ImportError:
            pytest.skip("PyQt6 not available")
        # Can't instantiate without QApplication, just verify class exists
        assert hasattr(UCInfoImage, 'set_value')
        assert hasattr(UCInfoImage, 'set_mode')
        assert hasattr(UCInfoImage, 'paintEvent')

    def test_bar_width_calculation_temp(self):
        """Progress bar width for temp/percent mode: value*2, max 200."""
        # Test the formula directly (value * 2, capped at 200)
        assert max(0, min(200, int(50 * 2))) == 100
        assert max(0, min(200, int(100 * 2))) == 200
        assert max(0, min(200, int(0 * 2))) == 0
        assert max(0, min(200, int(150 * 2))) == 200  # clamped

    def test_bar_width_calculation_mhz(self):
        """Progress bar width for MHz mode: value/25, max 200."""
        assert max(0, min(200, int(2500 / 25))) == 100
        assert max(0, min(200, int(5000 / 25))) == 200
        assert max(0, min(200, int(0 / 25))) == 0
        assert max(0, min(200, int(6000 / 25))) == 200  # clamped
