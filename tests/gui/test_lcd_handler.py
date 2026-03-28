"""Tests for gui/lcd_handler.py — LCDHandler lifecycle and routing.

Covers:
- Construction, properties, widget dict wiring
- apply_device_config: brightness, rotation, split mode restoration + RestoreLastThemeCommand dispatch
- Theme selection: path-based, cloud, animated, persist flag
- Mask application: apply_mask, persist mask_path (mask restore logic tested in test_lcd_device.py)
- Video: play_pause, stop, seek, tick routing
- Overlay: on_overlay_changed, on_overlay_tick, flash_element
- Display settings: set_brightness, set_rotation, set_split_mode
- Background toggle, screencast frame routing
- Slideshow: update state, tick, timer management
- Lifecycle: stop_timers, cleanup
"""
from __future__ import annotations

import os

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QTimer

from trcc.core.commands.lcd import (
    EnableOverlayCommand,
    PauseVideoCommand,
    RenderAndSendCommand,
    SendFrameCommand,
    SetBrightnessCommand,
    SetFlashIndexCommand,
    SetMaskPositionCommand,
    StopVideoCommand,
    UpdateVideoCacheTextCommand,
)

# =========================================================================
# Construction
# =========================================================================


class TestConstruction:
    """LCDHandler construction and properties."""

    def test_stores_lcd(self, make_lcd_handler, mock_lcd_device):
        h = make_lcd_handler(lcd=mock_lcd_device)
        assert h.display is mock_lcd_device

    def test_device_key_starts_empty(self, lcd_handler):
        assert lcd_handler.device_key == ''

    def test_brightness_level_default(self, lcd_handler):
        assert lcd_handler.brightness_level == 100  # DEFAULT_BRIGHTNESS_LEVEL = 100%

    def test_split_mode_default(self, lcd_handler):
        assert lcd_handler.split_mode == 0

    def test_ldd_is_split_default_false(self, lcd_handler):
        assert lcd_handler.ldd_is_split is False

    def test_is_background_active_default_false(self, lcd_handler):
        assert lcd_handler.is_background_active is False

    def test_is_background_active_setter(self, lcd_handler):
        lcd_handler.is_background_active = True
        assert lcd_handler.is_background_active is True

    def test_three_timers_created(self, make_lcd_handler):
        calls = []
        def track_timer(cb, single_shot=False):
            calls.append((cb, single_shot))
            return MagicMock(spec=QTimer)
        make_lcd_handler(make_timer=track_timer)
        assert len(calls) == 3
        # Flash timer is single_shot
        assert calls[2][1] is True


# =========================================================================
# apply_device_config
# =========================================================================


class TestApplyDeviceConfig:
    """apply_device_config — restore brightness, rotation, split, theme."""

    def _device(self, resolution=(320, 320)):
        dev = MagicMock()
        dev.device_index = 0
        dev.vid = 0x0402
        dev.pid = 0x3922
        dev.resolution = resolution
        return dev

    @patch('trcc.gui.lcd_handler.Settings')
    def test_sets_device_key(self, mock_settings, lcd_handler):
        mock_settings.device_config_key.return_value = 'test_key'
        mock_settings.get_device_config.return_value = {}
        lcd_handler.apply_device_config(self._device(), 320, 320)
        assert lcd_handler.device_key == 'test_key'

    @patch('trcc.gui.lcd_handler.Settings')
    def test_restores_brightness(self, mock_settings, lcd_handler, dispatched_commands):
        """Stored percent value restored directly — no level mapping."""
        mock_settings.device_config_key.return_value = 'k'
        mock_settings.get_device_config.return_value = {'brightness_level': 50}
        lcd_handler.apply_device_config(self._device(), 320, 320)
        assert lcd_handler.brightness_level == 50
        cmds = dispatched_commands(lcd_handler, SetBrightnessCommand)
        assert cmds and cmds[0].level == 50

    @patch('trcc.gui.lcd_handler.Settings')
    def test_restores_rotation(self, mock_settings, make_lcd_handler, mock_lcd_device):
        mock_settings.device_config_key.return_value = 'k'
        mock_settings.get_device_config.return_value = {'rotation': 90}
        h = make_lcd_handler()
        h.apply_device_config(self._device(), 320, 320)
        mock_lcd_device.set_rotation.assert_called_with(90)
        h._w['rotation_combo'].setCurrentIndex.assert_called_with(1)

    @patch('trcc.gui.lcd_handler.Settings')
    def test_resolution_change_updates_widgets(self, mock_settings, make_lcd_handler, mock_lcd_device):
        mock_settings.device_config_key.return_value = 'k'
        mock_settings.get_device_config.return_value = {}
        mock_lcd_device.lcd_size = (320, 320)
        h = make_lcd_handler(lcd=mock_lcd_device)
        h.apply_device_config(self._device(), 480, 480)
        # Widgets always updated — InitializeDeviceCommand is owned by _wire_bus, not here
        h._w['preview'].set_resolution.assert_called_with(480, 480)

    @patch('trcc.gui.lcd_handler.Settings')
    def test_widgets_always_updated(self, mock_settings, lcd_handler):
        mock_settings.device_config_key.return_value = 'k'
        mock_settings.get_device_config.return_value = {}
        lcd_handler.apply_device_config(self._device(), 320, 320)
        # Widget updates are unconditional — InitializeDeviceCommand not dispatched here
        lcd_handler._w['preview'].set_resolution.assert_called_with(320, 320)
        lcd_handler._w['image_cut'].set_resolution.assert_called_with(320, 320)
        lcd_handler._w['video_cut'].set_resolution.assert_called_with(320, 320)
        lcd_handler._w['theme_setting'].set_resolution.assert_called_with(320, 320)

    @patch('trcc.gui.lcd_handler.Settings')
    def test_split_mode_restored_for_split_resolution(self, mock_settings, make_lcd_handler, mock_lcd_device):
        mock_settings.device_config_key.return_value = 'k'
        mock_settings.get_device_config.return_value = {'split_mode': 1}
        mock_lcd_device.lcd_size = (320, 320)  # different from target to trigger change
        h = make_lcd_handler(lcd=mock_lcd_device)
        # 1600x720 is the split resolution (SPLIT_MODE_RESOLUTIONS)
        h.apply_device_config(self._device((1600, 720)), 1600, 720)
        assert h.ldd_is_split is True


# =========================================================================
# _update_theme_directories — first-install auto-load guard
# =========================================================================


class TestUpdateThemeDirectories:
    """_update_theme_directories first-install auto-load and skip-if-saved-theme guard."""

    def _make_theme_dir(self, tmp_path: Path) -> MagicMock:
        """Build a mock ThemeDir with one valid theme subfolder."""
        theme1 = tmp_path / 'Theme1'
        theme1.mkdir()
        (theme1 / '00.png').touch()

        td = MagicMock()
        td.exists.return_value = True
        td.path = tmp_path
        return td

    @patch('trcc.gui.lcd_handler.Settings')
    @patch('trcc.gui.lcd_handler._conf')
    def test_auto_loads_first_theme_on_first_install(self, mock_conf, mock_settings, make_lcd_handler, mock_lcd_device, dispatched_commands, tmp_path):
        """With no current image and no saved theme_path, auto-loads the first theme folder."""
        from trcc.core.commands.lcd import SelectThemeCommand

        td = self._make_theme_dir(tmp_path)
        mock_conf.settings.width = 320
        mock_conf.settings.height = 320
        mock_conf.settings.theme_dir = td
        mock_conf.settings.web_dir = None
        mock_conf.settings.masks_dir = None

        mock_settings.get_device_config.return_value = {}  # no saved theme_path

        mock_lcd_device.current_image = None  # no image loaded yet

        with patch('trcc.gui.lcd_handler.ThemeInfo') as mock_ti:
            mock_ti.from_directory.return_value = MagicMock()
            h = make_lcd_handler(lcd=mock_lcd_device)
            h._device_key = 'dev0'
            h._update_theme_directories()

        assert dispatched_commands(h, SelectThemeCommand), "Should auto-load first theme on first install"

    @patch('trcc.gui.lcd_handler.Settings')
    @patch('trcc.gui.lcd_handler._conf')
    def test_skips_auto_load_when_saved_theme_exists(self, mock_conf, mock_settings, make_lcd_handler, mock_lcd_device, dispatched_commands, tmp_path):
        """With no current image but a saved theme_path, skips auto-load to preserve user selection."""
        from trcc.core.commands.lcd import SelectThemeCommand

        td = self._make_theme_dir(tmp_path)
        mock_conf.settings.width = 320
        mock_conf.settings.height = 320
        mock_conf.settings.theme_dir = td
        mock_conf.settings.web_dir = None
        mock_conf.settings.masks_dir = None

        # User has a saved theme — must not overwrite it
        mock_settings.get_device_config.return_value = {'theme_path': '/themes/MyTheme'}

        mock_lcd_device.current_image = None

        h = make_lcd_handler(lcd=mock_lcd_device)
        h._device_key = 'dev0'
        h._update_theme_directories()

        assert not dispatched_commands(h, SelectThemeCommand), \
            "Must not auto-load theme1 when user already has a saved theme_path"

    @patch('trcc.gui.lcd_handler.Settings')
    @patch('trcc.gui.lcd_handler._conf')
    def test_skips_auto_load_when_image_already_showing(self, mock_conf, mock_settings, make_lcd_handler, mock_lcd_device, dispatched_commands, tmp_path):
        """With a current image already loaded, skips auto-load regardless of saved config."""
        from trcc.core.commands.lcd import SelectThemeCommand

        td = self._make_theme_dir(tmp_path)
        mock_conf.settings.width = 320
        mock_conf.settings.height = 320
        mock_conf.settings.theme_dir = td
        mock_conf.settings.web_dir = None
        mock_conf.settings.masks_dir = None
        mock_settings.get_device_config.return_value = {}

        mock_lcd_device.current_image = MagicMock()  # image already showing

        h = make_lcd_handler(lcd=mock_lcd_device)
        h._device_key = 'dev0'
        h._update_theme_directories()

        assert not dispatched_commands(h, SelectThemeCommand), \
            "Must not auto-load when current_image is already set"


# =========================================================================
# Theme selection
# =========================================================================


class TestThemeSelection:
    """select_theme_from_path, select_cloud_theme — routing + state."""

    @patch('trcc.gui.lcd_handler.Settings')
    @patch('trcc.gui.lcd_handler.ThemeInfo')
    def test_select_theme_from_path_calls_select(self, mock_ti, mock_settings, lcd_handler, dispatched_commands):
        from trcc.core.commands.lcd import SelectThemeCommand
        mock_ti.from_directory.return_value = MagicMock()
        path = MagicMock(spec=Path)
        path.exists.return_value = True
        path.__truediv__ = lambda self, x: MagicMock(exists=lambda: False)

        lcd_handler.select_theme_from_path(path)
        assert dispatched_commands(lcd_handler, SelectThemeCommand)

    def test_select_theme_nonexistent_path_noop(self, lcd_handler, dispatched_commands):
        from trcc.core.commands.lcd import SelectThemeCommand
        path = MagicMock(spec=Path)
        path.exists.return_value = False
        lcd_handler.select_theme_from_path(path)
        assert not dispatched_commands(lcd_handler, SelectThemeCommand)

    @patch('trcc.gui.lcd_handler.Settings')
    @patch('trcc.gui.lcd_handler.ThemeInfo')
    def test_select_theme_stops_video(self, mock_ti, mock_settings, lcd_handler, dispatched_commands):
        """Theme selection dispatches StopVideoCommand through the bus."""
        mock_ti.from_directory.return_value = MagicMock()
        path = MagicMock(spec=Path)
        path.exists.return_value = True
        path.__truediv__ = lambda self, x: MagicMock(exists=lambda: False)

        lcd_handler.select_theme_from_path(path)
        assert dispatched_commands(lcd_handler, StopVideoCommand)

    @patch('trcc.gui.lcd_handler.Settings')
    @patch('trcc.gui.lcd_handler.ThemeInfo')
    def test_select_theme_persists_path(self, mock_ti, mock_settings, lcd_handler):
        mock_ti.from_directory.return_value = MagicMock()
        lcd_handler._device_key = 'dev0'
        path = MagicMock(spec=Path)
        path.exists.return_value = True
        path.__truediv__ = lambda self, x: MagicMock(exists=lambda: False)
        path.__str__ = lambda self: '/themes/TestTheme'

        lcd_handler.select_theme_from_path(path, persist=True)
        mock_settings.save_device_setting.assert_any_call(
            'dev0', 'theme_path', '/themes/TestTheme')

    @patch('trcc.gui.lcd_handler.Settings')
    @patch('trcc.gui.lcd_handler.ThemeInfo')
    def test_select_theme_no_persist(self, mock_ti, mock_settings, lcd_handler):
        mock_ti.from_directory.return_value = MagicMock()
        lcd_handler._device_key = 'dev0'
        path = MagicMock(spec=Path)
        path.exists.return_value = True
        path.__truediv__ = lambda self, x: MagicMock(exists=lambda: False)

        lcd_handler.select_theme_from_path(path, persist=False)
        mock_settings.save_device_setting.assert_not_called()

    @patch('trcc.gui.lcd_handler.Settings')
    @patch('trcc.gui.lcd_handler.ThemeInfo')
    def test_no_double_send_when_overlay_follows(self, mock_ti, mock_settings, lcd_handler, dispatched_commands):
        """select_theme_from_path must not dispatch SendFrameCommand when
        overlay config will follow — avoids double-send blink on theme switch."""
        mock_ti.from_directory.return_value = MagicMock()
        path = MagicMock(spec=Path)
        path.exists.return_value = True
        path.__truediv__ = lambda self, x: MagicMock(exists=lambda: False)
        # overlay_config=True (default) and load_overlay_config_from_dir returns None
        # (theme has no overlay) — still must not SendFrameCommand; RenderAndSend owns the send
        lcd_handler._lcd.load_overlay_config_from_dir.return_value = None

        lcd_handler.select_theme_from_path(path)

        assert not dispatched_commands(lcd_handler, SendFrameCommand), (
            "SendFrameCommand must not fire when overlay_config=True — "
            "_load_theme_overlay_config owns the single send to avoid blink"
        )

    @patch('trcc.gui.lcd_handler.Settings')
    @patch('trcc.gui.lcd_handler.ThemeInfo')
    def test_single_render_and_send_on_theme_switch(self, mock_ti, mock_settings, lcd_handler, dispatched_commands):
        """Exactly one RenderAndSendCommand on a normal theme switch with overlay config."""
        mock_ti.from_directory.return_value = MagicMock()
        path = MagicMock(spec=Path)
        path.exists.return_value = True
        path.__truediv__ = lambda self, x: MagicMock(exists=lambda: False)
        # Theme has an overlay config — full overlay path
        lcd_handler._lcd.load_overlay_config_from_dir.return_value = {'elements': []}

        lcd_handler.select_theme_from_path(path)

        renders = dispatched_commands(lcd_handler, RenderAndSendCommand)
        sends = dispatched_commands(lcd_handler, SendFrameCommand)
        assert len(renders) == 1, f"Expected 1 RenderAndSendCommand, got {len(renders)}"
        assert len(sends) == 0, f"Expected 0 SendFrameCommand, got {len(sends)}"


# =========================================================================
# Mask
# =========================================================================


class TestMask:
    """apply_mask — loads mask, updates preview, persists."""

    @patch('trcc.gui.lcd_handler.Settings')
    def test_apply_mask_with_path(self, mock_settings, lcd_handler, dispatched_commands):
        from trcc.core.commands.lcd import LoadMaskCommand
        lcd_handler._device_key = 'dev0'
        mask_info = MagicMock()
        mask_info.path = '/masks/01'
        lcd_handler._lcd.load_mask_standalone.return_value = {
            'success': True, 'image': MagicMock()}
        lcd_handler.apply_mask(mask_info)
        assert dispatched_commands(lcd_handler, LoadMaskCommand)
        mock_settings.save_device_setting.assert_called_with(
            'dev0', 'mask_path', '/masks/01')

    def test_apply_mask_no_path_sets_status(self, lcd_handler):
        mask_info = MagicMock()
        mask_info.path = None
        mask_info.name = "Empty"
        lcd_handler.apply_mask(mask_info)
        lcd_handler._w['preview'].set_status.assert_called_once()

    @patch('trcc.gui.lcd_handler.Settings')
    def test_restore_dispatches_restore_last_theme_command(self, mock_settings, make_lcd_handler, mock_lcd_device, dispatched_commands):
        """apply_device_config dispatches RestoreLastThemeCommand — shared path with CLI/API."""
        from trcc.core.commands.lcd import RestoreLastThemeCommand
        mock_settings.device_config_key.return_value = 'k'
        mock_settings.get_device_config.return_value = {}
        mock_lcd_device.restore_last_theme.return_value = {'success': False, 'error': 'No saved theme'}
        h = make_lcd_handler(lcd=mock_lcd_device)
        h.apply_device_config(
            MagicMock(device_index=0, vid=0x0402, pid=0x3922), 320, 320)
        assert dispatched_commands(h, RestoreLastThemeCommand)

    @patch('trcc.gui.lcd_handler.Settings')
    def test_restore_updates_preview_on_success(self, mock_settings, make_lcd_handler, mock_lcd_device):
        """apply_device_config updates preview widget when RestoreLastThemeCommand succeeds."""
        mock_settings.device_config_key.return_value = 'k'
        mock_settings.get_device_config.return_value = {}
        img = MagicMock()
        mock_lcd_device.restore_last_theme.return_value = {
            'success': True, 'image': img, 'is_animated': False,
            'overlay_config': None, 'overlay_enabled': False,
        }
        h = make_lcd_handler(lcd=mock_lcd_device)
        h.apply_device_config(
            MagicMock(device_index=0, vid=0x0402, pid=0x3922), 320, 320)
        h._w['preview'].set_image.assert_called_once_with(img, fast=False)

    @patch('trcc.gui.lcd_handler.Settings')
    def test_restore_starts_animation_timer_for_video_theme(self, mock_settings, make_lcd_handler, mock_lcd_device):
        """apply_device_config starts animation timer when restoring a video theme."""
        mock_settings.device_config_key.return_value = 'k'
        mock_settings.get_device_config.return_value = {}
        mock_lcd_device.playing = True
        img = MagicMock()
        mock_lcd_device.restore_last_theme.return_value = {
            'success': True, 'image': img, 'is_animated': True,
            'overlay_config': None, 'overlay_enabled': False,
        }
        h = make_lcd_handler(lcd=mock_lcd_device)
        h.apply_device_config(
            MagicMock(device_index=0, vid=0x0402, pid=0x3922), 320, 320)
        h._animation_timer.start.assert_called_with(33)  # lcd.interval = 33
        h._w['preview'].set_playing.assert_called_with(True)

    @patch('trcc.gui.lcd_handler.Settings')
    def test_restore_no_animation_timer_for_static_theme(self, mock_settings, make_lcd_handler, mock_lcd_device):
        """apply_device_config does NOT start animation timer for a static theme."""
        mock_settings.device_config_key.return_value = 'k'
        mock_settings.get_device_config.return_value = {}
        mock_lcd_device.playing = False
        img = MagicMock()
        mock_lcd_device.restore_last_theme.return_value = {
            'success': True, 'image': img, 'is_animated': False,
            'overlay_config': None, 'overlay_enabled': True,
        }
        h = make_lcd_handler(lcd=mock_lcd_device)
        h.apply_device_config(
            MagicMock(device_index=0, vid=0x0402, pid=0x3922), 320, 320)
        h._animation_timer.start.assert_not_called()


# =========================================================================
# Video
# =========================================================================


class TestVideo:
    """play_pause, stop, seek, tick."""

    def test_play_pause_toggles(self, make_lcd_handler, mock_lcd_device, dispatched_commands):
        """play_pause dispatches PauseVideoCommand; result state drives timer."""
        mock_lcd_device.pause.return_value = {'state': 'playing', 'success': True}
        h = make_lcd_handler(lcd=mock_lcd_device)
        h.play_pause()
        assert dispatched_commands(h, PauseVideoCommand)
        h._w['preview'].set_playing.assert_called_with(True)
        h._animation_timer.start.assert_called_with(33)  # lcd.interval = 33

    def test_play_pause_pauses(self, make_lcd_handler, mock_lcd_device, dispatched_commands):
        """play_pause dispatches PauseVideoCommand; paused state stops timer."""
        mock_lcd_device.pause.return_value = {'state': 'paused', 'success': True}
        h = make_lcd_handler(lcd=mock_lcd_device)
        h.play_pause()
        h._w['preview'].set_playing.assert_called_with(False)
        h._animation_timer.stop.assert_called()

    def test_stop_video_dispatches_command(self, lcd_handler, dispatched_commands):
        """stop_video dispatches StopVideoCommand through the bus."""
        lcd_handler.stop_video()
        assert dispatched_commands(lcd_handler, StopVideoCommand)
        lcd_handler._animation_timer.stop.assert_called()
        lcd_handler._w['preview'].set_playing.assert_called_with(False)
        lcd_handler._w['preview'].show_video_controls.assert_called_with(False)

    def test_seek_dispatches_command(self, lcd_handler, dispatched_commands):
        """seek dispatches SeekVideoCommand with correct percent."""
        from trcc.core.commands.lcd import SeekVideoCommand
        lcd_handler.seek(50.0)
        cmds = dispatched_commands(lcd_handler, SeekVideoCommand)
        assert cmds and cmds[0].percent == 50.0


# =========================================================================
# Overlay
# =========================================================================


class TestOverlay:
    """on_overlay_changed, on_overlay_tick, flash_element."""

    @patch('trcc.gui.lcd_handler.Settings')
    def test_overlay_changed_dispatches_enable_and_config(self, mock_settings, lcd_handler, dispatched_commands):
        from trcc.core.commands.lcd import SetOverlayConfigCommand
        lcd_handler._device_key = 'dev0'
        lcd_handler._lcd.enabled = False  # overlay disabled — should trigger EnableOverlayCommand
        lcd_handler._w['theme_setting'].overlay_grid.overlay_enabled = True
        data = {'elements': []}
        lcd_handler.on_overlay_changed(data)
        assert dispatched_commands(lcd_handler, EnableOverlayCommand)
        cmds = dispatched_commands(lcd_handler, SetOverlayConfigCommand)
        assert cmds and cmds[0].config == data

    @patch('trcc.gui.lcd_handler.Settings')
    def test_overlay_changed_empty_data_noop(self, mock_settings, lcd_handler, dispatched_commands):
        from trcc.core.commands.lcd import SetOverlayConfigCommand
        lcd_handler.on_overlay_changed({})
        assert not dispatched_commands(lcd_handler, SetOverlayConfigCommand)

    @patch('trcc.gui.lcd_handler.Settings')
    def test_overlay_changed_none_data_noop(self, mock_settings, lcd_handler, dispatched_commands):
        from trcc.core.commands.lcd import SetOverlayConfigCommand
        lcd_handler.on_overlay_changed(None)
        assert not dispatched_commands(lcd_handler, SetOverlayConfigCommand)

    @patch('trcc.gui.lcd_handler.Settings')
    def test_overlay_changed_persists(self, mock_settings, lcd_handler):
        lcd_handler._device_key = 'dev0'
        lcd_handler._w['theme_setting'].overlay_grid.overlay_enabled = True
        data = {'elements': [{'type': 'text'}]}
        lcd_handler.on_overlay_changed(data)
        mock_settings.save_device_setting.assert_called_once()
        saved = mock_settings.save_device_setting.call_args[0]
        assert saved[0] == 'dev0'
        assert saved[1] == 'overlay'

    def test_overlay_tick_no_overlay_no_send(self, lcd_handler, dispatched_commands):
        """on_overlay_tick must NOT send for static no-overlay themes.

        Sending every refresh cycle (1–5 s) causes visible blinking.
        Static themes don't change — no periodic resend needed.
        """
        lcd_handler._lcd.playing = False
        lcd_handler._lcd.enabled = False
        lcd_handler._lcd.connected = True
        lcd_handler.on_overlay_tick(MagicMock())
        assert not dispatched_commands(lcd_handler, RenderAndSendCommand)
        assert not dispatched_commands(lcd_handler, UpdateVideoCacheTextCommand)

    def test_overlay_tick_overlay_enabled_no_send(self, lcd_handler, dispatched_commands):
        """on_overlay_tick must NOT send when overlay is enabled.

        LCDDevice.tick() (background thread) renders+sends whenever metrics
        change. If on_overlay_tick also sends, every refresh cycle causes two
        frames to hit the device — visible as a blink/flicker.
        """
        lcd_handler._lcd.playing = False
        lcd_handler._lcd.enabled = True
        lcd_handler._lcd.connected = True
        lcd_handler.on_overlay_tick(MagicMock())
        assert not dispatched_commands(lcd_handler, RenderAndSendCommand), (
            "on_overlay_tick must not send when overlay is enabled — "
            "tick() owns the send to avoid double-send blink"
        )
        assert not dispatched_commands(lcd_handler, UpdateVideoCacheTextCommand)

    def test_overlay_tick_noop_when_disconnected(self, lcd_handler, dispatched_commands):
        """on_overlay_tick does nothing when device is not connected."""
        lcd_handler._lcd.playing = False
        lcd_handler._lcd.connected = False
        lcd_handler.on_overlay_tick(MagicMock())
        assert not dispatched_commands(lcd_handler, RenderAndSendCommand)
        assert not dispatched_commands(lcd_handler, UpdateVideoCacheTextCommand)

    def test_update_preview_sets_preview_image(self, lcd_handler):
        """update_preview mirrors a frame rendered by tick() to the preview widget."""
        image = MagicMock()
        lcd_handler.update_preview(image)
        lcd_handler._w['preview'].set_image.assert_called_once_with(image)

    def test_overlay_tick_during_video_dispatches_update_cache_text(self, lcd_handler, dispatched_commands):
        """on_overlay_tick while video plays dispatches UpdateVideoCacheTextCommand directly."""
        lcd_handler._lcd.enabled = True
        lcd_handler._lcd.playing = True
        metrics = MagicMock()
        lcd_handler.on_overlay_tick(metrics)
        cmds = dispatched_commands(lcd_handler, UpdateVideoCacheTextCommand)
        assert cmds and cmds[0].metrics is metrics

    def test_overlay_tick_video_no_render_and_send(self, lcd_handler, dispatched_commands):
        """on_overlay_tick while video plays must not dispatch RenderAndSendCommand."""
        lcd_handler._lcd.enabled = True
        lcd_handler._lcd.playing = True
        lcd_handler.on_overlay_tick(MagicMock())
        assert not dispatched_commands(lcd_handler, RenderAndSendCommand)

    def test_flash_element_dispatches_set_flash_index(self, lcd_handler, dispatched_commands):
        """flash_element dispatches SetFlashIndexCommand with the correct index."""
        lcd_handler.flash_element(3)
        cmds = dispatched_commands(lcd_handler, SetFlashIndexCommand)
        assert cmds and cmds[0].index == 3
        lcd_handler._flash_timer.start.assert_called_with(980)

    def test_flash_timeout_clears_flash_index(self, lcd_handler, dispatched_commands):
        """_on_flash_timeout dispatches SetFlashIndexCommand(index=-1)."""
        lcd_handler._on_flash_timeout()
        cmds = dispatched_commands(lcd_handler, SetFlashIndexCommand)
        assert cmds and cmds[0].index == -1

    def test_update_mask_position_dispatches_command(self, lcd_handler, dispatched_commands):
        """update_mask_position dispatches SetMaskPositionCommand."""
        lcd_handler.update_mask_position(10, 20)
        cmds = dispatched_commands(lcd_handler, SetMaskPositionCommand)
        assert cmds and cmds[0].x == 10 and cmds[0].y == 20


# =========================================================================
# Display settings
# =========================================================================


class TestDisplaySettings:
    """set_brightness, set_rotation, set_split_mode."""

    def test_set_brightness_updates_level(self, lcd_handler):
        lcd_handler._lcd.set_brightness.return_value = {'success': True, 'image': MagicMock()}
        lcd_handler.set_brightness(50)
        assert lcd_handler.brightness_level == 50

    def test_set_brightness_updates_preview(self, lcd_handler):
        img = MagicMock()
        lcd_handler._lcd.set_brightness.return_value = {'success': True, 'image': img}
        lcd_handler.set_brightness(3)
        lcd_handler._w['preview'].set_image.assert_called_with(img)

    def test_set_brightness_sends_frame_if_auto_send(self, lcd_handler, dispatched_commands):
        """set_brightness dispatches SendFrameCommand when auto_send is on."""
        img = MagicMock()
        lcd_handler._lcd.set_brightness.return_value = {'success': True, 'image': img}
        lcd_handler._lcd.auto_send = True
        lcd_handler.set_brightness(2)
        cmds = dispatched_commands(lcd_handler, SendFrameCommand)
        assert cmds and cmds[0].image is img

    def test_set_brightness_no_send_if_no_auto(self, lcd_handler, dispatched_commands):
        img = MagicMock()
        lcd_handler._lcd.set_brightness.return_value = {'success': True, 'image': img}
        lcd_handler._lcd.auto_send = False
        lcd_handler.set_brightness(2)
        assert not dispatched_commands(lcd_handler, SendFrameCommand)

    @patch('trcc.gui.lcd_handler.Settings')
    def test_set_rotation_persists(self, mock_settings, lcd_handler):
        lcd_handler._device_key = 'dev0'
        lcd_handler._lcd.set_rotation.return_value = {'success': True, 'image': MagicMock()}
        lcd_handler.set_rotation(90)
        mock_settings.save_device_setting.assert_called_with('dev0', 'rotation', 90)

    @patch('trcc.gui.lcd_handler.Settings')
    @patch('trcc.conf.settings')
    def test_set_rotation_resolves_cloud_dirs(self, mock_conf, mock_settings, lcd_handler):
        lcd_handler._lcd.set_rotation.return_value = {'success': True}
        lcd_handler.set_rotation(270)
        mock_conf.resolve_cloud_dirs.assert_called_with(270)

    @patch('trcc.gui.lcd_handler.Settings')
    def test_set_split_mode_updates_state(self, mock_settings, lcd_handler):
        lcd_handler._device_key = 'dev0'
        lcd_handler._lcd.set_split_mode.return_value = {'success': True, 'image': MagicMock()}
        lcd_handler.set_split_mode(2)
        assert lcd_handler.split_mode == 2
        mock_settings.save_device_setting.assert_called_with('dev0', 'split_mode', 2)


# =========================================================================
# Background / Screencast
# =========================================================================


class TestBackgroundScreencast:
    """on_background_toggle, on_screencast_frame."""

    def test_background_toggle_on_stops_video(self, lcd_handler, dispatched_commands):
        """Background toggle dispatches StopVideoCommand through the bus."""
        lcd_handler.on_background_toggle(True)
        assert lcd_handler.is_background_active is True
        lcd_handler._animation_timer.stop.assert_called()
        assert dispatched_commands(lcd_handler, StopVideoCommand)

    def test_background_toggle_off(self, lcd_handler):
        lcd_handler.on_background_toggle(False)
        assert lcd_handler.is_background_active is False

    def test_screencast_frame_dispatches_send_frame(self, lcd_handler, dispatched_commands):
        """on_screencast_frame dispatches SendFrameCommand."""
        img = MagicMock()
        lcd_handler.on_screencast_frame(img)
        lcd_handler._w['preview'].set_image.assert_called_with(img)
        cmds = dispatched_commands(lcd_handler, SendFrameCommand)
        assert cmds and cmds[0].image is img


# =========================================================================
# Save / Export / Import
# =========================================================================


class TestThemeIO:
    """save_theme, export_config, import_config."""

    @patch('trcc.conf.settings')
    @patch('trcc.gui.lcd_handler.Settings')
    def test_save_theme_success(self, mock_settings_cls, mock_conf, lcd_handler, dispatched_commands):
        from trcc.core.commands.lcd import SaveThemeCommand
        td = MagicMock()
        td.exists.return_value = True
        mock_conf.theme_dir = td
        lcd_handler.save_theme("MyTheme")
        cmds = dispatched_commands(lcd_handler, SaveThemeCommand)
        assert cmds and cmds[0].name == "MyTheme"
        lcd_handler._w['preview'].set_status.assert_called_with('Saved')

    def test_export_config(self, lcd_handler, dispatched_commands):
        from trcc.core.commands.lcd import ExportThemeCommand
        lcd_handler.export_config(Path('/out/theme.tr'))
        assert dispatched_commands(lcd_handler, ExportThemeCommand)

    @patch('trcc.conf.settings')
    def test_import_config_success_reloads(self, mock_conf, lcd_handler, dispatched_commands):
        from trcc.core.commands.lcd import ImportThemeCommand
        td = MagicMock()
        td.exists.return_value = True
        mock_conf.theme_dir = td
        lcd_handler.import_config(Path('/in/theme.tr'))
        assert dispatched_commands(lcd_handler, ImportThemeCommand)
        lcd_handler._w['theme_local'].set_theme_directory.assert_called_once()
        lcd_handler._w['theme_local'].load_themes.assert_called_once()


# =========================================================================
# Render
# =========================================================================


class TestRender:
    """render_and_preview, _render_and_send."""

    def test_render_and_preview_returns_image(self, lcd_handler):
        """render_and_preview calls lcd.render() directly (read-only, no bus)."""
        img = MagicMock()
        lcd_handler._lcd.render.return_value = {'image': img}
        result = lcd_handler.render_and_preview()
        assert result is img
        lcd_handler._w['preview'].set_image.assert_called_with(img)

    def test_render_and_preview_no_image(self, lcd_handler):
        lcd_handler._lcd.render.return_value = {}
        result = lcd_handler.render_and_preview()
        assert result is None

    def test_render_and_send_dispatches_command(self, lcd_handler, dispatched_commands):
        """_render_and_send dispatches RenderAndSendCommand and updates preview."""
        img = MagicMock()
        lcd_handler._lcd.render_and_send.return_value = {'success': True, 'image': img}
        lcd_handler._render_and_send()
        assert dispatched_commands(lcd_handler, RenderAndSendCommand)
        lcd_handler._w['preview'].set_image.assert_called_with(img)


# =========================================================================
# Lifecycle
# =========================================================================


class TestLifecycle:
    """stop_timers, cleanup."""

    def test_stop_timers(self, lcd_handler):
        lcd_handler.stop_timers()
        lcd_handler._animation_timer.stop.assert_called()
        lcd_handler._slideshow_timer.stop.assert_called()

    def test_cleanup_dispatches_stop_video(self, lcd_handler, dispatched_commands):
        """cleanup dispatches StopVideoCommand — ensures video state is consistent."""
        lcd_handler.cleanup()
        lcd_handler._animation_timer.stop.assert_called()
        lcd_handler._slideshow_timer.stop.assert_called()
        lcd_handler._flash_timer.stop.assert_called()
        assert dispatched_commands(lcd_handler, StopVideoCommand)
        lcd_handler._lcd.cleanup.assert_called()
