"""Tests for L5: message-bus replay buffer is configurable + bounded.

Audit §4 L5 — the Redis-outage replay buffer was a hardcoded class constant
(_MAX_BUFFER_SIZE=500) with no way to tune it per deployment. It already logs a
high-watermark warning and an eviction warning (overflow IS monitored), so this
pass only makes the bound configurable and verifies the bounded-eviction
behavior under sustained outage.
"""

from __future__ import annotations

from app.communication.message_bus import MessageBus
from app.config import Settings


def test_settings_expose_buffer_size():
    assert Settings().message_bus_buffer_size == 500


def test_buffer_size_resolved_from_settings():
    bus = MessageBus()
    assert bus._max_buffer_size == 500


def test_buffer_evicts_oldest_when_full(monkeypatch):
    import app.config as cfg
    monkeypatch.setenv("MESSAGE_BUS_BUFFER_SIZE", "10")
    cfg.get_settings.cache_clear()
    try:
        bus = MessageBus()
        assert bus._max_buffer_size == 10
        # Simulate a sustained outage: buffer many more than the cap.
        for i in range(25):
            bus._buffer_for_replay(f"chan:{i}", f'{{"seq": {i}}}')
        # Never grows past the configured cap …
        assert len(bus._publish_buffer) <= 10
        # … and it's the OLDEST that get evicted (newest survive).
        seqs = [env for _ch, env in bus._publish_buffer]
        assert '{"seq": 24}' in seqs
        assert '{"seq": 0}' not in seqs
    finally:
        cfg.get_settings.cache_clear()
