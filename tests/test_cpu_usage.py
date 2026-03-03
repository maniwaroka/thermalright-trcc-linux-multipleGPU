"""CPU usage gate — blocks release if mediator tick exceeds budget.

Runs the MetricsMediator tick loop 20 times with mocked metrics
and verifies each tick stays under 5ms. No GUI, no device, no Qt event loop.
"""
from __future__ import annotations

import time


def _make_metrics():
    """Build a realistic HardwareMetrics mock."""
    from trcc.core.models import HardwareMetrics
    return HardwareMetrics(
        cpu_temp=55.0, cpu_percent=42.0, cpu_freq=3600,
        gpu_temp=62.0, gpu_usage=35.0, gpu_clock=1800,
        mem_percent=50.0, mem_available=8192,
    )


class TestOverlayTickBudget:
    """Overlay render cycle must stay under 5ms per tick."""

    def test_render_overlay_under_budget(self):
        """render_overlay() with cache hit must be < 5ms."""
        from trcc.services.overlay import OverlayService

        svc = OverlayService(320, 320)
        svc.enabled = True
        metrics = _make_metrics()

        # Prime the cache
        svc.render(metrics=metrics)

        # Measure 20 cached ticks
        times = []
        for _ in range(20):
            t0 = time.perf_counter()
            svc.render(metrics=metrics)
            times.append((time.perf_counter() - t0) * 1000)

        avg = sum(times) / len(times)
        worst = max(times)
        assert avg < 5.0, f"Average overlay tick {avg:.1f}ms exceeds 5ms budget"
        assert worst < 10.0, f"Worst overlay tick {worst:.1f}ms exceeds 10ms budget"

    def test_render_overlay_with_config_under_budget(self):
        """render_overlay() with overlay config must be < 5ms."""
        from trcc.services.overlay import OverlayService

        svc = OverlayService(320, 320)
        svc.enabled = True
        svc.set_config({
            "0": {"metric": "cpu_temp", "x": 10, "y": 10, "size": 24,
                  "color": "#ffffff", "font": ""},
            "1": {"metric": "gpu_temp", "x": 10, "y": 50, "size": 24,
                  "color": "#ffffff", "font": ""},
            "2": {"metric": "time", "x": 10, "y": 90, "size": 20,
                  "color": "#ffffff", "font": ""},
        })
        metrics = _make_metrics()

        # Prime
        svc.render(metrics=metrics)

        times = []
        for _ in range(20):
            t0 = time.perf_counter()
            svc.render(metrics=metrics)
            times.append((time.perf_counter() - t0) * 1000)

        avg = sum(times) / len(times)
        worst = max(times)
        assert avg < 5.0, f"Average tick {avg:.1f}ms exceeds 5ms budget"
        assert worst < 10.0, f"Worst tick {worst:.1f}ms exceeds 10ms budget"

    def test_metrics_poll_under_budget(self):
        """get_all_metrics() must be < 50ms."""
        from trcc.services.system import get_all_metrics

        # Warm up
        get_all_metrics()

        times = []
        for _ in range(10):
            t0 = time.perf_counter()
            get_all_metrics()
            times.append((time.perf_counter() - t0) * 1000)

        avg = sum(times) / len(times)
        assert avg < 50.0, f"Average metrics poll {avg:.1f}ms exceeds 50ms budget"

    def test_rgb565_encode_under_budget(self):
        """RGB565 encoding (320x320) must be < 10ms."""
        import numpy as np

        from trcc.services.image import ImageService

        img = np.zeros((320, 320, 3), dtype=np.uint8)
        img[:, :, 0] = 128  # some color data

        # Warm up
        ImageService.to_rgb565(img)

        times = []
        for _ in range(20):
            t0 = time.perf_counter()
            ImageService.to_rgb565(img)
            times.append((time.perf_counter() - t0) * 1000)

        avg = sum(times) / len(times)
        assert avg < 10.0, f"Average RGB565 encode {avg:.1f}ms exceeds 10ms budget"
