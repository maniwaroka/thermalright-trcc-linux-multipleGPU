"""LCD config persistence — per-device settings save/load.

Parallel to led_config.py. Injected into LCDDevice as a single service
object instead of 4 separate callables. Builder wires the concrete
Settings methods at composition time.
"""
from __future__ import annotations

from typing import Any, Callable


class LCDConfigService:
    """LCD per-device config persistence — injected into LCDDevice.

    Wraps Settings static methods behind a device-aware interface.
    LCDDevice calls persist/get_config with a device object — this
    service computes the config key internally.
    """

    def __init__(
        self,
        config_key_fn: Callable[..., str],
        save_setting_fn: Callable[..., None],
        get_config_fn: Callable[..., dict],
        apply_format_prefs_fn: Callable[..., None],
    ) -> None:
        self._config_key_fn = config_key_fn
        self._save_fn = save_setting_fn
        self._get_fn = get_config_fn
        self._apply_prefs_fn = apply_format_prefs_fn

    def device_key(self, dev: Any) -> str:
        """Compute per-device config key from device info."""
        return self._config_key_fn(dev.device_index, dev.vid, dev.pid)

    def persist(self, dev: Any, field: str, value: Any) -> None:
        """Save a single setting for a device."""
        if dev:
            self._save_fn(self.device_key(dev), field, value)

    def get_config(self, dev: Any) -> dict:
        """Read full per-device config dict."""
        if not dev:
            return {}
        return self._get_fn(self.device_key(dev))

    def apply_format_prefs(self, overlay_cfg: dict) -> None:
        """Apply user format preferences to an overlay config."""
        self._apply_prefs_fn(overlay_cfg)
