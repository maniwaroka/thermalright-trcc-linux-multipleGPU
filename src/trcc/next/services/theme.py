"""ThemeService — theme discovery and metadata parsing.

A theme in TRCC is a directory containing:
    config.json       element layout, fonts, colors, overlay config
    background.png    (or .jpg) the base image
    optional extras   mask images, animation frames, fonts

This service provides:
    load(path)          → Theme (metadata, not pixels; pixels rendered later)
    list(directory)     → list[Theme] of themes found under a directory
    export(src, dst)    → zip/archive a theme for sharing
    import_(src, dst)   → unpack a shared theme archive

Phase 5 scope: load + list.  Export/import stubbed pending Phase 12.
Rendering (turning a Theme into frame bytes) is DisplayService's job
and needs the Renderer port — lands with Phase 6.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional, Tuple

from ..core.errors import ThemeError
from ..core.models import Theme

log = logging.getLogger(__name__)


_CONFIG_FILE = "config.json"
_BACKGROUND_CANDIDATES = ("background.png", "background.jpg", "background.jpeg")


class ThemeService:
    """Theme discovery + parsing.

    Pure file I/O + JSON parsing — no rendering, no device talk.  Builds
    Theme metadata that later services consume.
    """

    def load(self, path: Path) -> Theme:
        """Load a theme directory into a Theme dataclass.

        Raises ThemeError if the directory is missing, unreadable, or
        the config.json is invalid.
        """
        if not path.exists():
            raise ThemeError(f"Theme directory does not exist: {path}")
        if not path.is_dir():
            raise ThemeError(f"Theme path is not a directory: {path}")

        config = self._load_config(path)
        resolution = self._resolution_from_config(config)
        name = config.get("name") or path.name

        return Theme(
            path=path,
            name=name,
            resolution=resolution,
            config=config,
        )

    def list(self, directory: Path) -> List[Theme]:
        """Return every theme found directly under *directory*.

        A subdirectory is a theme iff it contains config.json.  Invalid
        themes are skipped with a warning, not raised — list() never
        fails on one bad theme.
        """
        if not directory.exists() or not directory.is_dir():
            return []

        themes: List[Theme] = []
        for entry in sorted(directory.iterdir()):
            if not entry.is_dir():
                continue
            if not (entry / _CONFIG_FILE).exists():
                continue
            try:
                themes.append(self.load(entry))
            except ThemeError as e:
                log.warning("Skipping invalid theme %s: %s", entry, e)
        return themes

    def background_path(self, theme: Theme) -> Optional[Path]:
        """Return the theme's background image path, or None if absent."""
        for candidate in _BACKGROUND_CANDIDATES:
            path = theme.path / candidate
            if path.exists():
                return path
        return None

    def export(self, theme_path: Path, archive_path: Path) -> None:
        """Archive a theme directory for sharing.  Phase 12."""
        raise ThemeError("ThemeService.export not yet implemented (Phase 12)")

    def import_(self, archive_path: Path, into_dir: Path) -> Theme:
        """Unpack a shared theme archive.  Phase 12."""
        raise ThemeError("ThemeService.import_ not yet implemented (Phase 12)")

    # ── internals ─────────────────────────────────────────────────────

    def _load_config(self, path: Path) -> dict:
        cfg_path = path / _CONFIG_FILE
        if not cfg_path.exists():
            raise ThemeError(f"Missing {_CONFIG_FILE} in {path}")
        try:
            return json.loads(cfg_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise ThemeError(f"Invalid theme config {cfg_path}: {e}") from e

    def _resolution_from_config(self, config: dict) -> Tuple[int, int]:
        """Extract (width, height) from config; fall back to (0, 0) if absent."""
        width = int(config.get("width", 0))
        height = int(config.get("height", 0))
        return (width, height)
