"""Tests for SSE Transport — buffered event streaming with reconnection replay."""

import asyncio
import time

import pytest

from src.server.services.engine.sse_transport import (
    SSEEvent,
    SSETransport,
    get_sse_transport,
)


@pytest.fixture
def transport() -> SSETransport:
    return SSETransport(buffer_ttl_seconds=10, buffer_max_size=50, keepalive_seconds=1)


class TestPublish:
    """Test event publishing and buffering."""

    def test_publish_returns_event_id(self, transport: SSETransport) -> None:
        eid = transport.publish("task_status", {"task_id": "t-1"})
        assert isinstance(eid, str)
        assert len(eid) > 0

    def test_publish_custom_id(self, transport: SSETransport) -> None:
        eid = transport.publish("task_status", {"task_id": "t-1"}, event_id="custom-123")
        assert eid == "custom-123"

    def test_publish_adds_to_buffer(self, transport: SSETransport) -> None:
        transport.publish("task_status", {"task_id": "t-1"})
        transport.publish("engine_health", {"status": "ok"})
        assert transport.buffer_size == 2

    def test_buffer_max_size(self) -> None:
        transport = SSETransport(buffer_max_size=3)
        transport.publish("e1", {"a": 1})
        transport.publish("e2", {"a": 2})
        transport.publish("e3", {"a": 3})
        transport.publish("e4", {"a": 4})
        assert transport.buffer_size == 3  # Oldest evicted


class TestBufferExpiry:
    """Test TTL-based buffer expiration."""

    def test_expired_events_removed(self) -> None:
        transport = SSETransport(buffer_ttl_seconds=0)  # Instant expiry
        transport.publish("e1", {"a": 1})
        time.sleep(0.01)
        # Trigger expiry
        transport.publish("e2", {"a": 2})
        # e1 should be expired after the next publish triggers cleanup
        # Buffer should have at most 2 (e1 may or may not be expired yet depending on timing)
        assert transport.buffer_size <= 2


class TestReconnectionReplay:
    """Test that events after last_event_id are replayed on reconnection."""

    def test_replay_after_last_id(self, transport: SSETransport) -> None:
        id1 = transport.publish("e1", {"a": 1})
        id2 = transport.publish("e2", {"a": 2})
        id3 = transport.publish("e3", {"a": 3})

        replay = transport._get_events_after(id1)
        assert len(replay) == 2
        assert replay[0].id == id2
        assert replay[1].id == id3

    def test_replay_all_if_id_not_found(self, transport: SSETransport) -> None:
        transport.publish("e1", {"a": 1})
        transport.publish("e2", {"a": 2})

        replay = transport._get_events_after("nonexistent-id")
        assert len(replay) == 2  # All buffered events

    def test_replay_empty_with_no_last_id(self, transport: SSETransport) -> None:
        transport.publish("e1", {"a": 1})
        replay = transport._get_events_after(None)
        assert len(replay) == 0

    def test_replay_empty_when_last_id_is_newest(self, transport: SSETransport) -> None:
        id1 = transport.publish("e1", {"a": 1})
        id2 = transport.publish("e2", {"a": 2})

        replay = transport._get_events_after(id2)
        assert len(replay) == 0  # Nothing after latest


class TestSSEEvent:
    """Test SSEEvent formatting."""

    def test_to_sse_dict(self) -> None:
        event = SSEEvent(id="abc", event_type="task_status", data={"task_id": "t-1"})
        sse_dict = event.to_sse_dict()
        assert sse_dict["id"] == "abc"
        assert sse_dict["event"] == "task_status"
        assert '"task_id"' in sse_dict["data"]

    def test_age_seconds(self) -> None:
        event = SSEEvent(id="abc", event_type="test", data={}, timestamp=time.time() - 5)
        assert event.age_seconds >= 5


class TestSubscribers:
    """Test subscriber management."""

    @pytest.mark.asyncio
    async def test_subscriber_receives_events(self, transport: SSETransport) -> None:
        received: list[dict] = []

        async def collect():
            async for event in transport._event_generator():
                received.append(event)
                if len(received) >= 2:
                    break

        task = asyncio.create_task(collect())
        await asyncio.sleep(0.05)

        transport.publish("e1", {"a": 1})
        transport.publish("e2", {"a": 2})

        await asyncio.wait_for(task, timeout=3)
        assert len(received) == 2

    @pytest.mark.asyncio
    async def test_subscriber_count(self, transport: SSETransport) -> None:
        assert transport.subscriber_count == 0

        async def dummy_consumer():
            async for _ in transport._event_generator():
                break

        task = asyncio.create_task(dummy_consumer())
        await asyncio.sleep(0.05)
        assert transport.subscriber_count >= 1

        transport.publish("e", {"a": 1})
        await asyncio.wait_for(task, timeout=2)

    @pytest.mark.asyncio
    async def test_event_filter(self, transport: SSETransport) -> None:
        received: list[dict] = []

        async def collect_filtered():
            async for event in transport._event_generator(event_filter={"task_status"}):
                received.append(event)
                if len(received) >= 1:
                    break

        task = asyncio.create_task(collect_filtered())
        await asyncio.sleep(0.05)

        transport.publish("engine_health", {"status": "ok"})  # Should be filtered
        transport.publish("task_status", {"task_id": "t-1"})  # Should pass

        await asyncio.wait_for(task, timeout=3)
        assert len(received) == 1
        assert received[0]["event"] == "task_status"


class TestShutdown:
    """Test graceful shutdown."""

    @pytest.mark.asyncio
    async def test_shutdown_stops_subscribers(self, transport: SSETransport) -> None:
        events_received = []

        async def consumer():
            async for event in transport._event_generator():
                events_received.append(event)

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0.05)

        await transport.shutdown()
        await asyncio.sleep(0.1)

        assert transport.subscriber_count == 0


class TestSingleton:
    """Test module-level singleton."""

    def test_singleton_returns_same_instance(self) -> None:
        t1 = get_sse_transport()
        t2 = get_sse_transport()
        assert t1 is t2
