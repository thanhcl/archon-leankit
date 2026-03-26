"""Unit tests for TelemetryEventStore

Tests SQLite persistence, querying with filters, pagination, and thread safety.
"""

import os
import tempfile
import threading
from datetime import UTC, datetime

import pytest

from src.agent_work_orders.utils.telemetry_store import TelemetryEventStore


@pytest.fixture
def tmp_store(tmp_path: "os.PathLike[str]") -> TelemetryEventStore:
    """TelemetryEventStore backed by a temporary SQLite file."""
    db_path = str(tmp_path / "test_telemetry.db")
    return TelemetryEventStore(db_path)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_persist_and_query_single_event(tmp_store: TelemetryEventStore) -> None:
    """Persisted event is returned by query_events."""
    tmp_store.persist_event({
        "work_order_id": "wo-001",
        "level": "info",
        "event": "step_started",
        "timestamp": "2025-01-01T10:00:00+00:00",
        "step": "planning",
    })

    events, total = tmp_store.query_events(work_order_id="wo-001")
    assert total == 1
    assert len(events) == 1
    assert events[0]["event"] == "step_started"
    assert events[0]["step"] == "planning"
    assert events[0]["work_order_id"] == "wo-001"


@pytest.mark.unit
def test_event_without_correlation_fields_not_persisted(tmp_store: TelemetryEventStore) -> None:
    """Events with no correlation fields are silently dropped."""
    tmp_store.persist_event({
        "level": "info",
        "event": "unrelated_system_event",
        "timestamp": "2025-01-01T10:00:00+00:00",
    })

    events, total = tmp_store.query_events()
    assert total == 0
    assert events == []


@pytest.mark.unit
def test_persist_event_with_task_id(tmp_store: TelemetryEventStore) -> None:
    """Events correlated via task_id are persisted and queryable."""
    tmp_store.persist_event({
        "task_id": "task-99",
        "level": "info",
        "event": "task_completed",
        "timestamp": "2025-01-01T10:00:00+00:00",
    })

    events, total = tmp_store.query_events(task_id="task-99")
    assert total == 1
    assert events[0]["task_id"] == "task-99"


@pytest.mark.unit
def test_persist_event_with_run_and_project_ids(tmp_store: TelemetryEventStore) -> None:
    """Events with run_id and project_id are queryable by those fields."""
    tmp_store.persist_event({
        "run_id": "run-abc",
        "project_id": "proj-xyz",
        "level": "warning",
        "event": "budget_threshold_reached",
        "timestamp": "2025-01-01T11:00:00+00:00",
    })

    by_run, _ = tmp_store.query_events(run_id="run-abc")
    assert len(by_run) == 1

    by_project, _ = tmp_store.query_events(project_id="proj-xyz")
    assert len(by_project) == 1


@pytest.mark.unit
def test_extra_fields_round_tripped(tmp_store: TelemetryEventStore) -> None:
    """Arbitrary extra fields survive persist/query round-trip via extra_data."""
    tmp_store.persist_event({
        "work_order_id": "wo-002",
        "level": "info",
        "event": "agent_response_received",
        "timestamp": "2025-01-01T12:00:00+00:00",
        "tokens": 256,
        "model": "claude-sonnet",
        "nested": {"key": "value"},
    })

    events, _ = tmp_store.query_events(work_order_id="wo-002")
    assert events[0]["tokens"] == 256
    assert events[0]["model"] == "claude-sonnet"
    assert events[0]["nested"] == {"key": "value"}


@pytest.mark.unit
def test_timestamp_auto_generated_when_missing(tmp_store: TelemetryEventStore) -> None:
    """A timestamp is generated automatically when the event dict omits it."""
    tmp_store.persist_event({
        "work_order_id": "wo-ts",
        "level": "info",
        "event": "no_timestamp",
    })

    events, _ = tmp_store.query_events(work_order_id="wo-ts")
    assert events[0]["timestamp"] != ""
    # Verify it is a parseable ISO timestamp
    datetime.fromisoformat(events[0]["timestamp"])


# ---------------------------------------------------------------------------
# Time-range filtering
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_filter_by_from_time(tmp_store: TelemetryEventStore) -> None:
    """from_time filter excludes events before the lower bound."""
    for ts in ["2025-01-01T08:00:00+00:00", "2025-01-01T10:00:00+00:00", "2025-01-01T12:00:00+00:00"]:
        tmp_store.persist_event({"work_order_id": "wo-tr", "level": "info", "event": "ping", "timestamp": ts})

    events, total = tmp_store.query_events(from_time="2025-01-01T10:00:00+00:00")
    assert total == 2
    assert all(e["timestamp"] >= "2025-01-01T10:00:00+00:00" for e in events)


@pytest.mark.unit
def test_filter_by_to_time(tmp_store: TelemetryEventStore) -> None:
    """to_time filter excludes events after the upper bound."""
    for ts in ["2025-01-01T08:00:00+00:00", "2025-01-01T10:00:00+00:00", "2025-01-01T12:00:00+00:00"]:
        tmp_store.persist_event({"work_order_id": "wo-tr", "level": "info", "event": "ping", "timestamp": ts})

    events, total = tmp_store.query_events(to_time="2025-01-01T10:00:00+00:00")
    assert total == 2
    assert all(e["timestamp"] <= "2025-01-01T10:00:00+00:00" for e in events)


@pytest.mark.unit
def test_filter_by_time_range(tmp_store: TelemetryEventStore) -> None:
    """Combining from_time and to_time returns only the events within the range."""
    for ts in ["2025-01-01T08:00:00+00:00", "2025-01-01T10:00:00+00:00", "2025-01-01T12:00:00+00:00"]:
        tmp_store.persist_event({"work_order_id": "wo-tr", "level": "info", "event": "ping", "timestamp": ts})

    events, total = tmp_store.query_events(
        from_time="2025-01-01T09:00:00+00:00",
        to_time="2025-01-01T11:00:00+00:00",
    )
    assert total == 1
    assert events[0]["timestamp"] == "2025-01-01T10:00:00+00:00"


# ---------------------------------------------------------------------------
# Correlation filtering
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_multiple_work_orders_isolated(tmp_store: TelemetryEventStore) -> None:
    """Events from different work orders are independently queryable."""
    tmp_store.persist_event({"work_order_id": "wo-A", "level": "info", "event": "e1", "timestamp": "2025-01-01T10:00:00+00:00"})
    tmp_store.persist_event({"work_order_id": "wo-B", "level": "info", "event": "e2", "timestamp": "2025-01-01T10:00:00+00:00"})
    tmp_store.persist_event({"work_order_id": "wo-A", "level": "info", "event": "e3", "timestamp": "2025-01-01T11:00:00+00:00"})

    events_a, total_a = tmp_store.query_events(work_order_id="wo-A")
    assert total_a == 2
    assert all(e["work_order_id"] == "wo-A" for e in events_a)

    events_b, total_b = tmp_store.query_events(work_order_id="wo-B")
    assert total_b == 1


@pytest.mark.unit
def test_filter_by_level(tmp_store: TelemetryEventStore) -> None:
    """level filter returns only events at that level (case-insensitive)."""
    tmp_store.persist_event({"work_order_id": "wo-lvl", "level": "info", "event": "ok", "timestamp": "2025-01-01T10:00:00+00:00"})
    tmp_store.persist_event({"work_order_id": "wo-lvl", "level": "error", "event": "fail", "timestamp": "2025-01-01T11:00:00+00:00"})

    error_events, total = tmp_store.query_events(level="ERROR")
    assert total == 1
    assert error_events[0]["event"] == "fail"


@pytest.mark.unit
def test_combined_filters(tmp_store: TelemetryEventStore) -> None:
    """All active filters are combined with AND."""
    tmp_store.persist_event({"work_order_id": "wo-c", "level": "info", "event": "e1", "timestamp": "2025-01-01T09:00:00+00:00"})
    tmp_store.persist_event({"work_order_id": "wo-c", "level": "error", "event": "e2", "timestamp": "2025-01-01T11:00:00+00:00"})
    tmp_store.persist_event({"work_order_id": "wo-other", "level": "error", "event": "e3", "timestamp": "2025-01-01T11:00:00+00:00"})

    events, total = tmp_store.query_events(
        work_order_id="wo-c",
        level="error",
        from_time="2025-01-01T10:00:00+00:00",
    )
    assert total == 1
    assert events[0]["event"] == "e2"


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_pagination_limit(tmp_store: TelemetryEventStore) -> None:
    """limit controls the number of events returned."""
    for i in range(20):
        tmp_store.persist_event({
            "work_order_id": "wo-pg",
            "level": "info",
            "event": f"event_{i:02d}",
            "timestamp": f"2025-01-01T{10 + i // 60:02d}:{i % 60:02d}:00+00:00",
        })

    events, total = tmp_store.query_events(work_order_id="wo-pg", limit=5)
    assert total == 20
    assert len(events) == 5


@pytest.mark.unit
def test_pagination_offset(tmp_store: TelemetryEventStore) -> None:
    """offset skips the expected number of events."""
    timestamps = [
        f"2025-01-01T{10 + i:02d}:00:00+00:00" for i in range(10)
    ]
    for i, ts in enumerate(timestamps):
        tmp_store.persist_event({
            "work_order_id": "wo-pg2",
            "level": "info",
            "event": f"event_{i}",
            "timestamp": ts,
        })

    page1, _ = tmp_store.query_events(work_order_id="wo-pg2", limit=3, offset=0)
    page2, _ = tmp_store.query_events(work_order_id="wo-pg2", limit=3, offset=3)
    assert len(page1) == 3
    assert len(page2) == 3
    # No overlap
    page1_events = {e["event"] for e in page1}
    page2_events = {e["event"] for e in page2}
    assert page1_events.isdisjoint(page2_events)


@pytest.mark.unit
def test_pagination_last_page(tmp_store: TelemetryEventStore) -> None:
    """Last page returns only the remaining events."""
    for i in range(7):
        tmp_store.persist_event({
            "work_order_id": "wo-pg3",
            "level": "info",
            "event": f"ev_{i}",
            "timestamp": f"2025-01-01T{10 + i:02d}:00:00+00:00",
        })

    events, total = tmp_store.query_events(work_order_id="wo-pg3", limit=5, offset=5)
    assert total == 7
    assert len(events) == 2


@pytest.mark.unit
def test_results_ordered_by_timestamp_ascending(tmp_store: TelemetryEventStore) -> None:
    """Events are returned in ascending timestamp order."""
    for ts in ["2025-01-01T12:00:00+00:00", "2025-01-01T08:00:00+00:00", "2025-01-01T10:00:00+00:00"]:
        tmp_store.persist_event({"work_order_id": "wo-ord", "level": "info", "event": "x", "timestamp": ts})

    events, _ = tmp_store.query_events(work_order_id="wo-ord")
    timestamps = [e["timestamp"] for e in events]
    assert timestamps == sorted(timestamps)


# ---------------------------------------------------------------------------
# get_event_count
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_get_event_count(tmp_store: TelemetryEventStore) -> None:
    """get_event_count returns the total number of persisted events."""
    assert tmp_store.get_event_count() == 0

    tmp_store.persist_event({"work_order_id": "wo-cnt", "level": "info", "event": "a", "timestamp": "2025-01-01T10:00:00+00:00"})
    tmp_store.persist_event({"work_order_id": "wo-cnt", "level": "info", "event": "b", "timestamp": "2025-01-01T11:00:00+00:00"})

    assert tmp_store.get_event_count() == 2


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_thread_safe_concurrent_writes(tmp_store: TelemetryEventStore) -> None:
    """Concurrent persist_event calls from multiple threads do not corrupt the store."""
    num_threads = 8
    events_per_thread = 50
    errors: list[Exception] = []

    def write_events(thread_id: int) -> None:
        try:
            for i in range(events_per_thread):
                tmp_store.persist_event({
                    "work_order_id": f"wo-thread-{thread_id}",
                    "level": "info",
                    "event": f"event_{i}",
                    "timestamp": datetime.now(UTC).isoformat(),
                })
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=write_events, args=(t,)) for t in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Thread errors: {errors}"
    assert tmp_store.get_event_count() == num_threads * events_per_thread


# ---------------------------------------------------------------------------
# Database persistence across instances
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_data_persists_across_store_instances(tmp_path: "os.PathLike[str]") -> None:
    """Closing and reopening the store returns previously persisted events."""
    db_path = str(tmp_path / "persist_test.db")

    store_a = TelemetryEventStore(db_path)
    store_a.persist_event({
        "work_order_id": "wo-persist",
        "level": "info",
        "event": "saved",
        "timestamp": "2025-06-01T09:00:00+00:00",
    })

    # A fresh instance pointing at the same file should see the data
    store_b = TelemetryEventStore(db_path)
    events, total = store_b.query_events(work_order_id="wo-persist")
    assert total == 1
    assert events[0]["event"] == "saved"


# ---------------------------------------------------------------------------
# Integration with BufferProcessor
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_buffer_processor_routes_to_telemetry_store(tmp_store: TelemetryEventStore) -> None:
    """BufferProcessor persists events with correlation fields to the telemetry store."""
    from collections import deque
    from unittest.mock import MagicMock

    from src.agent_work_orders.utils.log_buffer import WorkOrderLogBuffer
    from src.agent_work_orders.utils.structured_logger import BufferProcessor

    buffer = WorkOrderLogBuffer()
    processor = BufferProcessor(buffer, tmp_store)

    event_dict: dict = {
        "work_order_id": "wo-bp",
        "level": "info",
        "event": "processor_test",
        "timestamp": "2025-01-01T10:00:00+00:00",
    }
    processor(MagicMock(), "info", event_dict)

    # Buffer should have the log
    assert buffer.get_log_count("wo-bp") == 1

    # Telemetry store should have the event
    events, total = tmp_store.query_events(work_order_id="wo-bp")
    assert total == 1
    assert events[0]["event"] == "processor_test"


@pytest.mark.unit
def test_buffer_processor_without_telemetry_store(tmp_path: "os.PathLike[str]") -> None:
    """BufferProcessor works when telemetry_store is None (backward compatible)."""
    from unittest.mock import MagicMock

    from src.agent_work_orders.utils.log_buffer import WorkOrderLogBuffer
    from src.agent_work_orders.utils.structured_logger import BufferProcessor

    buffer = WorkOrderLogBuffer()
    processor = BufferProcessor(buffer)  # No telemetry_store

    event_dict: dict = {
        "work_order_id": "wo-no-store",
        "level": "info",
        "event": "no_store_event",
        "timestamp": "2025-01-01T10:00:00+00:00",
    }
    result = processor(MagicMock(), "info", event_dict)

    # Buffer should have the log, no crash
    assert buffer.get_log_count("wo-no-store") == 1
    assert result is event_dict  # pass-through
