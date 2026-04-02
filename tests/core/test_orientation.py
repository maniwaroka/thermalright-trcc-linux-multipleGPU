"""Tests for trcc.core.orientation — pure display geometry functions."""
from __future__ import annotations

import pytest

from trcc.core.orientation import effective_resolution, image_rotation

# =========================================================================
# effective_resolution
# =========================================================================


class TestEffectiveResolution:
    """effective_resolution(w, h, rotation) — swaps dims for non-square at 90/270."""

    # Non-square landscape
    @pytest.mark.parametrize("rot,expected", [
        (0, (1280, 480)),
        (90, (480, 1280)),
        (180, (1280, 480)),
        (270, (480, 1280)),
    ])
    def test_non_square_1280x480(self, rot, expected):
        assert effective_resolution(1280, 480, rot) == expected

    @pytest.mark.parametrize("rot,expected", [
        (0, (800, 480)),
        (90, (480, 800)),
        (180, (800, 480)),
        (270, (480, 800)),
    ])
    def test_non_square_800x480(self, rot, expected):
        assert effective_resolution(800, 480, rot) == expected

    @pytest.mark.parametrize("rot,expected", [
        (0, (1600, 720)),
        (90, (720, 1600)),
        (180, (1600, 720)),
        (270, (720, 1600)),
    ])
    def test_non_square_1600x720(self, rot, expected):
        assert effective_resolution(1600, 720, rot) == expected

    # Square — never swaps
    @pytest.mark.parametrize("rot", [0, 90, 180, 270])
    def test_square_320x320_never_swaps(self, rot):
        assert effective_resolution(320, 320, rot) == (320, 320)

    @pytest.mark.parametrize("rot", [0, 90, 180, 270])
    def test_square_480x480_never_swaps(self, rot):
        assert effective_resolution(480, 480, rot) == (480, 480)

    @pytest.mark.parametrize("rot", [0, 90, 180, 270])
    def test_square_240x240_never_swaps(self, rot):
        assert effective_resolution(240, 240, rot) == (240, 240)

    def test_zero_resolution(self):
        assert effective_resolution(0, 0, 90) == (0, 0)


# =========================================================================
# image_rotation
# =========================================================================


class TestImageRotation:
    """image_rotation(w, h, rotation) — 0 when canvas already portrait."""

    # Non-square: 90/270 return 0 (canvas already swapped)
    def test_non_square_0(self):
        assert image_rotation(800, 480, 0) == 0

    def test_non_square_90_returns_0(self):
        assert image_rotation(800, 480, 90) == 0

    def test_non_square_180(self):
        assert image_rotation(800, 480, 180) == 180

    def test_non_square_270_returns_0(self):
        assert image_rotation(800, 480, 270) == 0

    # Square: always returns actual rotation
    @pytest.mark.parametrize("rot", [0, 90, 180, 270])
    def test_square_returns_actual(self, rot):
        assert image_rotation(320, 320, rot) == rot
