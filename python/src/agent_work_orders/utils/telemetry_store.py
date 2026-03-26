"""Persistent Telemetry Event Store

SQLite-backed store for work order telemetry events. Provides durable
persistence beyond the in-memory replay buffer, with querying by time
range and correlation IDs (work_order_id, task_id, run_id, project_id).
"""

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class TelemetryEventStore:
    """SQLite-backed persistent store for structured telemetry events.

    Persists log events with correlation IDs to disk, enabling historical
    debugging beyond the in-memory buffer's retention window.

    Supports filtering by time range and any combination of correlation
    fields. All query results are paginated.

    Thread-safe via a per-instance lock around all database operations.
    """

    # Fields extracted as first-class columns for indexed querying
    CORRELATION_FIELDS: frozenset[str] = frozenset({"work_order_id", "task_id", "run_id", "project_id"})

    def __init__(self, db_path: str) -> None:
        """Initialize the store and create the database schema if needed.

        Args:
            db_path: Absolute path to the SQLite database file. Parent
                     directories are created automatically.
        """
        self._db_path = db_path
        self._lock = threading.Lock()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS telemetry_events (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        work_order_id TEXT,
                        task_id       TEXT,
                        run_id        TEXT,
                        project_id    TEXT,
                        level         TEXT NOT NULL,
                        event         TEXT NOT NULL,
                        timestamp     TEXT NOT NULL,
                        extra_data    TEXT
                    );
                    CREATE INDEX IF NOT EXISTS idx_te_timestamp
                        ON telemetry_events(timestamp);
                    CREATE INDEX IF NOT EXISTS idx_te_work_order
                        ON telemetry_events(work_order_id);
                    CREATE INDEX IF NOT EXISTS idx_te_task_id
                        ON telemetry_events(task_id);
                    CREATE INDEX IF NOT EXISTS idx_te_run_id
                        ON telemetry_events(run_id);
                    CREATE INDEX IF NOT EXISTS idx_te_project_id
                        ON telemetry_events(project_id);
                """)
                conn.commit()
            finally:
                conn.close()

    def persist_event(self, event_dict: dict[str, Any]) -> None:
        """Persist a structured telemetry event to the store.

        Only events that carry at least one correlation field
        (work_order_id, task_id, run_id, or project_id) are stored.
        All remaining fields are serialised into extra_data as JSON.

        Args:
            event_dict: Structured log event dictionary produced by structlog.

        Examples:
            store.persist_event({
                "work_order_id": "wo-123",
                "level": "info",
                "event": "step_completed",
                "timestamp": "2025-01-01T12:00:00+00:00",
                "step": "planning",
            })
        """
        work_order_id: str | None = event_dict.get("work_order_id")
        task_id: str | None = event_dict.get("task_id")
        run_id: str | None = event_dict.get("run_id")
        project_id: str | None = event_dict.get("project_id")

        if not any([work_order_id, task_id, run_id, project_id]):
            return

        level = str(event_dict.get("level", "info"))
        event = str(event_dict.get("event", ""))
        timestamp = str(event_dict.get("timestamp") or datetime.now(UTC).isoformat())

        core_fields = {"work_order_id", "task_id", "run_id", "project_id", "level", "event", "timestamp"}
        extra = {k: v for k, v in event_dict.items() if k not in core_fields}
        extra_data: str | None = json.dumps(extra, default=str) if extra else None

        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    """
                    INSERT INTO telemetry_events
                        (work_order_id, task_id, run_id, project_id,
                         level, event, timestamp, extra_data)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (work_order_id, task_id, run_id, project_id,
                     level, event, timestamp, extra_data),
                )
                conn.commit()
            finally:
                conn.close()

    def query_events(
        self,
        work_order_id: str | None = None,
        task_id: str | None = None,
        run_id: str | None = None,
        project_id: str | None = None,
        from_time: str | None = None,
        to_time: str | None = None,
        level: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """Query persisted telemetry events with optional filters.

        All filters are combined with AND. Results are ordered by
        timestamp ascending (oldest first), with id as a tiebreaker.

        Args:
            work_order_id: Exact match on work_order_id column.
            task_id: Exact match on task_id column.
            run_id: Exact match on run_id column.
            project_id: Exact match on project_id column.
            from_time: ISO timestamp lower bound (inclusive).
            to_time: ISO timestamp upper bound (inclusive).
            level: Case-insensitive log level filter.
            limit: Maximum events to return (caller should enforce 1–1000).
            offset: Number of matching events to skip.

        Returns:
            Tuple of (list of event dicts, total count matching filters).
            Extra fields stored in extra_data are merged into each event dict.

        Examples:
            events, total = store.query_events(
                work_order_id="wo-123",
                from_time="2025-01-01T00:00:00Z",
                limit=50,
            )
        """
        conditions: list[str] = []
        params: list[Any] = []

        if work_order_id:
            conditions.append("work_order_id = ?")
            params.append(work_order_id)
        if task_id:
            conditions.append("task_id = ?")
            params.append(task_id)
        if run_id:
            conditions.append("run_id = ?")
            params.append(run_id)
        if project_id:
            conditions.append("project_id = ?")
            params.append(project_id)
        if from_time:
            conditions.append("timestamp >= ?")
            params.append(from_time)
        if to_time:
            conditions.append("timestamp <= ?")
            params.append(to_time)
        if level:
            conditions.append("LOWER(level) = LOWER(?)")
            params.append(level)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with self._lock:
            conn = self._get_connection()
            try:
                count_row = conn.execute(
                    f"SELECT COUNT(*) FROM telemetry_events {where_clause}",
                    params,
                ).fetchone()
                total: int = int(count_row[0]) if count_row else 0

                rows = conn.execute(
                    f"""
                    SELECT id, work_order_id, task_id, run_id, project_id,
                           level, event, timestamp, extra_data
                    FROM telemetry_events
                    {where_clause}
                    ORDER BY timestamp ASC, id ASC
                    LIMIT ? OFFSET ?
                    """,
                    params + [limit, offset],
                ).fetchall()
            finally:
                conn.close()

        events: list[dict[str, Any]] = []
        for row in rows:
            entry: dict[str, Any] = {
                "id": row["id"],
                "work_order_id": row["work_order_id"],
                "task_id": row["task_id"],
                "run_id": row["run_id"],
                "project_id": row["project_id"],
                "level": row["level"],
                "event": row["event"],
                "timestamp": row["timestamp"],
            }
            if row["extra_data"]:
                try:
                    extra = json.loads(row["extra_data"])
                    entry.update(extra)
                except json.JSONDecodeError:
                    pass
            events.append(entry)

        return events, total

    def get_event_count(self) -> int:
        """Return the total number of persisted events.

        Returns:
            Total row count in telemetry_events table.
        """
        with self._lock:
            conn = self._get_connection()
            try:
                row = conn.execute("SELECT COUNT(*) FROM telemetry_events").fetchone()
                return int(row[0]) if row else 0
            finally:
                conn.close()
