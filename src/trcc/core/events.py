"""EventBus — async notification surface for the universal TRCC command layer.

Commands emit events (frame ready, metrics updated, device connect/disconnect,
data ready, update available). Each UI bridges events to its own plumbing:
GUI → Qt signals, API → WebSocket messages, CLI → stdout streams.

Framework-neutral: no Qt, no asyncio. Thread-safe via a single lock so
background-thread publishes (video tick, sensor poll, USB hotplug) deliver
safely to subscribers.

Event names are strings by convention — keep them flat and stable:

    'frame'              → (device_idx: int, frame: Frame)
    'metrics'            → dict   (HardwareMetrics.__dict__ or adjacent)
    'device.connected'   → DeviceInfo
    'device.disconnected'→ DeviceInfo
    'data.ready'         → None   (theme/web/mask archives extracted)
    'update.available'   → UpdateResult
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from threading import Lock
from typing import Any

log = logging.getLogger(__name__)


class EventBus:
    """Minimal publish/subscribe bus.

    Callbacks run on the thread that calls `publish()`. UI adapters that
    need thread-hop (e.g., GUI → main thread) must do it themselves.
    A failing callback logs + continues; one broken subscriber never
    blocks the rest.
    """

    def __init__(self) -> None:
        self._subs: dict[int, tuple[str, Callable[..., Any]]] = {}
        self._next_id: int = 0
        self._lock = Lock()

    def subscribe(self, event: str, callback: Callable[..., Any]) -> int:
        """Register a callback for `event`. Returns a subscription id."""
        with self._lock:
            sub_id = self._next_id
            self._next_id += 1
            self._subs[sub_id] = (event, callback)
        log.debug("subscribe: id=%d event=%r", sub_id, event)
        return sub_id

    def unsubscribe(self, sub_id: int) -> None:
        """Remove a subscription. No-op if already gone."""
        with self._lock:
            removed = self._subs.pop(sub_id, None)
        if removed is not None:
            log.debug("unsubscribe: id=%d event=%r", sub_id, removed[0])

    def publish(self, event: str, *payload: Any) -> None:
        """Notify all subscribers of `event`. Payload is passed positionally."""
        with self._lock:
            targets = [
                cb for _sid, (ev, cb) in self._subs.items() if ev == event
            ]
        if not targets:
            return
        log.debug("publish: event=%r subscribers=%d", event, len(targets))
        for cb in targets:
            try:
                cb(*payload)
            except Exception:
                log.exception("EventBus subscriber for %r raised", event)

    def clear(self) -> None:
        """Drop every subscription — used during cleanup/teardown."""
        with self._lock:
            self._subs.clear()
            self._next_id = 0
        log.debug("clear: all subscriptions removed")
