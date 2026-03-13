"""Tests for core/i18n.py — translation tables and tr() helper."""
from __future__ import annotations

import pytest

from trcc.core.i18n import (
    ABOUT_AUTOSTART,
    ABOUT_HDD,
    ABOUT_HDD_WARNING,
    ABOUT_LANG_SELECT,
    ABOUT_MULTI_THREAD,
    ABOUT_REFRESH_TIME,
    ABOUT_RUNNING_MODE,
    ABOUT_SINGLE_THREAD,
    ABOUT_UNIT,
    ABOUT_UPDATE,
    ABOUT_VERSION_LABEL,
    BACKGROUND_LOAD_IMG,
    BACKGROUND_LOAD_VIDEO,
    BACKGROUND_TITLE,
    DISPLAY_ANGLE,
    EXPORT_IMPORT,
    GALLERY_AESTHETIC,
    GALLERY_ALL,
    GALLERY_LIGHT,
    GALLERY_NATURE,
    GALLERY_OTHER,
    GALLERY_TECH,
    GALLERY_TITLE,
    LANGUAGE_NAMES,
    LOCAL_THEME,
    MASK_DESCRIPTION,
    MASK_LOAD,
    MASK_TITLE,
    MEDIA_PLAYER_LOAD,
    MEDIA_PLAYER_TITLE,
    NO_DEVICE_CONNECT,
    NO_DEVICE_TITLE,
    ONLINE_THEME,
    OVERLAY_GRID_HINT,
    OVERLAY_GRID_TITLE,
    PARAM_COLOUR,
    PARAM_COORDINATE,
    PARAM_FONT,
    SAVE_AS,
    SCREENCAST_TITLE,
    SHORTCUTS_COORDINATE,
    SYSINFO_NAME,
    SYSINFO_VALUE,
    TITLE_BAR_POS,
    TITLE_BAR_TEXT,
    tr,
)

# Every translation table in i18n.py
ALL_TABLES: list[tuple[str, dict[str, str]]] = [
    ('LANGUAGE_NAMES', LANGUAGE_NAMES),
    ('MASK_TITLE', MASK_TITLE),
    ('MASK_LOAD', MASK_LOAD),
    ('MASK_DESCRIPTION', MASK_DESCRIPTION),
    ('BACKGROUND_TITLE', BACKGROUND_TITLE),
    ('BACKGROUND_LOAD_IMG', BACKGROUND_LOAD_IMG),
    ('BACKGROUND_LOAD_VIDEO', BACKGROUND_LOAD_VIDEO),
    ('MEDIA_PLAYER_TITLE', MEDIA_PLAYER_TITLE),
    ('MEDIA_PLAYER_LOAD', MEDIA_PLAYER_LOAD),
    ('SCREENCAST_TITLE', SCREENCAST_TITLE),
    ('OVERLAY_GRID_TITLE', OVERLAY_GRID_TITLE),
    ('OVERLAY_GRID_HINT', OVERLAY_GRID_HINT),
    ('PARAM_COORDINATE', PARAM_COORDINATE),
    ('PARAM_FONT', PARAM_FONT),
    ('PARAM_COLOUR', PARAM_COLOUR),
    ('LOCAL_THEME', LOCAL_THEME),
    ('ONLINE_THEME', ONLINE_THEME),
    ('GALLERY_TITLE', GALLERY_TITLE),
    ('GALLERY_ALL', GALLERY_ALL),
    ('GALLERY_TECH', GALLERY_TECH),
    ('GALLERY_LIGHT', GALLERY_LIGHT),
    ('GALLERY_NATURE', GALLERY_NATURE),
    ('GALLERY_AESTHETIC', GALLERY_AESTHETIC),
    ('GALLERY_OTHER', GALLERY_OTHER),
    ('DISPLAY_ANGLE', DISPLAY_ANGLE),
    ('SAVE_AS', SAVE_AS),
    ('EXPORT_IMPORT', EXPORT_IMPORT),
    ('NO_DEVICE_TITLE', NO_DEVICE_TITLE),
    ('NO_DEVICE_CONNECT', NO_DEVICE_CONNECT),
    ('SHORTCUTS_COORDINATE', SHORTCUTS_COORDINATE),
    ('ABOUT_AUTOSTART', ABOUT_AUTOSTART),
    ('ABOUT_UNIT', ABOUT_UNIT),
    ('ABOUT_HDD', ABOUT_HDD),
    ('ABOUT_HDD_WARNING', ABOUT_HDD_WARNING),
    ('ABOUT_REFRESH_TIME', ABOUT_REFRESH_TIME),
    ('ABOUT_RUNNING_MODE', ABOUT_RUNNING_MODE),
    ('ABOUT_SINGLE_THREAD', ABOUT_SINGLE_THREAD),
    ('ABOUT_MULTI_THREAD', ABOUT_MULTI_THREAD),
    ('ABOUT_UPDATE', ABOUT_UPDATE),
    ('ABOUT_LANG_SELECT', ABOUT_LANG_SELECT),
    ('ABOUT_VERSION_LABEL', ABOUT_VERSION_LABEL),
    ('SYSINFO_NAME', SYSINFO_NAME),
    ('SYSINFO_VALUE', SYSINFO_VALUE),
]

# Original 10 C# language codes
ORIGINAL_LANGS = {'', 'tc', 'en', 'd', 'e', 'f', 'p', 'r', 'x', 'h'}

# Extended ISO 639-1 codes
EXTENDED_LANGS = {
    'it', 'nl', 'pl', 'tr', 'ar', 'hi', 'th', 'vi', 'id', 'cs',
    'sv', 'da', 'no', 'fi', 'hu', 'ro', 'uk', 'el', 'he', 'ms',
}

ALL_LANGS = ORIGINAL_LANGS | EXTENDED_LANGS

# Tables that intentionally have fewer keys (NO_DEVICE_TITLE is same all langs)
SPARSE_TABLES = {'NO_DEVICE_TITLE'}


class TestTranslationTables:
    """Verify structural integrity of all translation dicts."""

    @pytest.mark.parametrize('name,table', ALL_TABLES, ids=[t[0] for t in ALL_TABLES])
    def test_has_english(self, name: str, table: dict[str, str]) -> None:
        assert 'en' in table, f'{name} missing English translation'

    @pytest.mark.parametrize('name,table', ALL_TABLES, ids=[t[0] for t in ALL_TABLES])
    def test_no_empty_values(self, name: str, table: dict[str, str]) -> None:
        for lang, text in table.items():
            assert text.strip(), f'{name}[{lang!r}] is empty'

    @pytest.mark.parametrize('name,table', ALL_TABLES, ids=[t[0] for t in ALL_TABLES])
    def test_no_leading_trailing_whitespace(self, name: str, table: dict[str, str]) -> None:
        for lang, text in table.items():
            assert text == text.strip(), f'{name}[{lang!r}] has leading/trailing whitespace'

    @pytest.mark.parametrize(
        'name,table',
        [(n, t) for n, t in ALL_TABLES if n not in SPARSE_TABLES],
        ids=[n for n, _ in ALL_TABLES if n not in SPARSE_TABLES],
    )
    def test_all_30_langs_present(self, name: str, table: dict[str, str]) -> None:
        missing = ALL_LANGS - set(table.keys())
        assert not missing, f'{name} missing languages: {missing}'

    @pytest.mark.parametrize('name,table', ALL_TABLES, ids=[t[0] for t in ALL_TABLES])
    def test_no_unexpected_lang_keys(self, name: str, table: dict[str, str]) -> None:
        unexpected = set(table.keys()) - ALL_LANGS
        assert not unexpected, f'{name} has unexpected keys: {unexpected}'

    @pytest.mark.parametrize('name,table', ALL_TABLES, ids=[t[0] for t in ALL_TABLES])
    def test_values_are_strings(self, name: str, table: dict[str, str]) -> None:
        for lang, text in table.items():
            assert isinstance(text, str), f'{name}[{lang!r}] is {type(text)}, not str'


class TestLanguageNames:
    """LANGUAGE_NAMES — used for dropdown selector."""

    def test_covers_all_langs(self) -> None:
        assert set(LANGUAGE_NAMES.keys()) == ALL_LANGS

    def test_english_is_english(self) -> None:
        assert LANGUAGE_NAMES['en'] == 'English'

    def test_chinese_simplified(self) -> None:
        assert LANGUAGE_NAMES[''] == '简体中文'

    def test_all_names_unique(self) -> None:
        values = list(LANGUAGE_NAMES.values())
        assert len(values) == len(set(values)), 'Duplicate language names found'


class TestTrFunction:
    """tr() — translation lookup with fallback chain."""

    def test_exact_match(self) -> None:
        assert tr(MASK_TITLE, 'en') == 'Layer Mask'

    def test_chinese_default_key(self) -> None:
        assert tr(MASK_TITLE, '') == '布局蒙板'

    def test_extended_lang(self) -> None:
        assert tr(MASK_TITLE, 'it') == 'Maschera livello'

    def test_fallback_to_english(self) -> None:
        assert tr(MASK_TITLE, 'zz_nonexistent') == 'Layer Mask'

    def test_fallback_to_first_entry_when_no_english(self) -> None:
        table = {'': '中文', 'tc': '繁體'}
        assert tr(table, 'zz') == '中文'

    def test_all_original_langs_resolve(self) -> None:
        for lang in ORIGINAL_LANGS:
            result = tr(MASK_TITLE, lang)
            assert result, f'tr(MASK_TITLE, {lang!r}) returned empty'

    def test_all_extended_langs_resolve(self) -> None:
        for lang in EXTENDED_LANGS:
            result = tr(MASK_TITLE, lang)
            assert result, f'tr(MASK_TITLE, {lang!r}) returned empty'

    def test_returns_str(self) -> None:
        assert isinstance(tr(MASK_TITLE, 'en'), str)


class TestCrossTableConsistency:
    """Verify tables that should share keys actually do."""

    def test_mask_tables_same_keys(self) -> None:
        assert set(MASK_TITLE.keys()) == set(MASK_LOAD.keys())
        assert set(MASK_TITLE.keys()) == set(MASK_DESCRIPTION.keys())

    def test_background_tables_same_keys(self) -> None:
        assert set(BACKGROUND_TITLE.keys()) == set(BACKGROUND_LOAD_IMG.keys())
        assert set(BACKGROUND_TITLE.keys()) == set(BACKGROUND_LOAD_VIDEO.keys())

    def test_media_player_tables_same_keys(self) -> None:
        assert set(MEDIA_PLAYER_TITLE.keys()) == set(MEDIA_PLAYER_LOAD.keys())

    def test_overlay_tables_same_keys(self) -> None:
        assert set(OVERLAY_GRID_TITLE.keys()) == set(OVERLAY_GRID_HINT.keys())

    def test_param_tables_same_keys(self) -> None:
        assert set(PARAM_COORDINATE.keys()) == set(PARAM_FONT.keys())
        assert set(PARAM_COORDINATE.keys()) == set(PARAM_COLOUR.keys())

    def test_gallery_tables_same_keys(self) -> None:
        gallery_tables = [
            GALLERY_TITLE, GALLERY_ALL, GALLERY_TECH, GALLERY_LIGHT,
            GALLERY_NATURE, GALLERY_AESTHETIC, GALLERY_OTHER,
        ]
        keys = set(gallery_tables[0].keys())
        for tbl in gallery_tables[1:]:
            assert set(tbl.keys()) == keys

    def test_main_view_tables_same_keys(self) -> None:
        assert set(DISPLAY_ANGLE.keys()) == set(SAVE_AS.keys())
        assert set(DISPLAY_ANGLE.keys()) == set(EXPORT_IMPORT.keys())

    def test_about_tables_same_keys(self) -> None:
        about_tables = [
            ABOUT_AUTOSTART, ABOUT_UNIT, ABOUT_HDD, ABOUT_HDD_WARNING,
            ABOUT_REFRESH_TIME, ABOUT_RUNNING_MODE, ABOUT_SINGLE_THREAD,
            ABOUT_MULTI_THREAD, ABOUT_UPDATE, ABOUT_LANG_SELECT,
            ABOUT_VERSION_LABEL,
        ]
        keys = set(about_tables[0].keys())
        for tbl in about_tables[1:]:
            assert set(tbl.keys()) == keys

    def test_sysinfo_tables_same_keys(self) -> None:
        assert set(SYSINFO_NAME.keys()) == set(SYSINFO_VALUE.keys())


class TestCoordinateTuples:
    """Verify _POS coordinate tuples have correct structure."""

    def test_all_pos_tuples_are_5_ints(self) -> None:
        from trcc.core import i18n
        for name in dir(i18n):
            if name.endswith('_POS'):
                val = getattr(i18n, name)
                assert isinstance(val, tuple), f'{name} is not a tuple'
                assert len(val) == 5, f'{name} has {len(val)} elements, expected 5'
                assert all(isinstance(v, int) for v in val), f'{name} has non-int elements'

    def test_pos_values_non_negative(self) -> None:
        from trcc.core import i18n
        for name in dir(i18n):
            if name.endswith('_POS'):
                val = getattr(i18n, name)
                assert all(v >= 0 for v in val), f'{name} has negative values'


class TestTitleBar:
    """Gold title bar — universal, not translated."""

    def test_text_is_trcc_linux(self) -> None:
        assert TITLE_BAR_TEXT == 'TRCC-Linux'

    def test_pos_tuple(self) -> None:
        assert isinstance(TITLE_BAR_POS, tuple)
        assert len(TITLE_BAR_POS) == 5
        assert TITLE_BAR_POS[4] == 28  # font size
