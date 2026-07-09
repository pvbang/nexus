"""Tests for ChannelManager.connect_all concurrency (audit H3).

Root cause: connect_all connected channels sequentially, so one slow/unreachable
channel delayed every channel after it — and with the 15s outer timeout in
main.py, a single stall could starve the rest of startup. Fixed by connecting
concurrently with a per-channel timeout.

Fake channels (no network) drive the behaviour: parallelism, per-channel timeout
isolation, and per-channel error isolation.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.channels.manager import ChannelManager


class _FakeChannel:
    def __init__(self, channel_type: str, *, delay: float = 0.0, fail: bool = False):
        self.channel_type = channel_type
        self._delay = delay
        self._fail = fail
        self.handler = None
        self.connected = False

    def set_message_handler(self, handler):
        self.handler = handler

    async def connect(self):
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._fail:
            raise RuntimeError(f"{self.channel_type} connect boom")
        self.connected = True

    async def disconnect(self):
        if self._delay:
            await asyncio.sleep(self._delay)
        self.connected = False


@pytest.mark.asyncio
async def test_connects_all_healthy_channels():
    mgr = ChannelManager()
    mgr.register(_FakeChannel("telegram"))
    mgr.register(_FakeChannel("slack"))
    connected = await mgr.connect_all()
    assert set(connected) == {"telegram", "slack"}


@pytest.mark.asyncio
async def test_failed_channel_does_not_block_others():
    mgr = ChannelManager()
    mgr.register(_FakeChannel("telegram", fail=True))
    mgr.register(_FakeChannel("slack"))
    connected = await mgr.connect_all()
    # The failing channel is dropped; the healthy one still connects.
    assert connected == ["slack"]


@pytest.mark.asyncio
async def test_channels_connect_in_parallel_not_sequentially():
    # Two channels each sleeping 0.3s: parallel ≈ 0.3s, sequential ≈ 0.6s.
    mgr = ChannelManager()
    mgr.register(_FakeChannel("a", delay=0.3))
    mgr.register(_FakeChannel("b", delay=0.3))
    start = time.perf_counter()
    connected = await mgr.connect_all()
    elapsed = time.perf_counter() - start
    assert set(connected) == {"a", "b"}
    assert elapsed < 0.55, f"connect_all took {elapsed:.2f}s — looks sequential"


@pytest.mark.asyncio
async def test_slow_channel_times_out_without_blocking(monkeypatch):
    # Shrink the per-channel timeout so the test is fast.
    import app.channels.manager as m
    monkeypatch.setattr(m, "_CHANNEL_CONNECT_TIMEOUT", 0.2)

    mgr = ChannelManager()
    mgr.register(_FakeChannel("hang", delay=5.0))   # would stall far past timeout
    mgr.register(_FakeChannel("fast"))

    start = time.perf_counter()
    connected = await mgr.connect_all()
    elapsed = time.perf_counter() - start

    assert connected == ["fast"]          # slow one timed out, dropped
    assert elapsed < 1.0, f"timeout did not bound the slow channel ({elapsed:.2f}s)"


@pytest.mark.asyncio
async def test_empty_manager_returns_empty():
    assert await ChannelManager().connect_all() == []


@pytest.mark.asyncio
async def test_disconnect_all_hanging_channel_does_not_block_shutdown(monkeypatch):
    import app.channels.manager as m
    monkeypatch.setattr(m, "_CHANNEL_CONNECT_TIMEOUT", 0.2)

    mgr = ChannelManager()
    mgr.register(_FakeChannel("hang", delay=5.0))   # would stall shutdown
    mgr.register(_FakeChannel("fast"))

    start = time.perf_counter()
    await mgr.disconnect_all()   # must return promptly despite the hanging channel
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0, f"disconnect_all blocked on hanging channel ({elapsed:.2f}s)"
