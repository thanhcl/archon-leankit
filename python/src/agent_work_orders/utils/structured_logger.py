"""Structured Logging Setup

Configures structlog for PRD-compliant event logging with SSE streaming support.
Event naming follows: {module}_{noun}_{verb_past_tense}
"""

from __future__ import annotations

import logging
from collections.abc import MutableMapping
from typing import Any

try:
    import structlog
    from structlog.contextvars import bind_contextvars, clear_contextvars
except ModuleNotFoundError:  # pragma: no cover - exercised in lightweight test envs
    structlog = None

    def bind_contextvars(**_: Any) -> None:
        return None

    def clear_contextvars() -> None:
        return None

from .log_buffer import WorkOrderLogBuffer
from .telemetry_store import TelemetryEventStore


class _StdlibBoundLogger:
    """Small structlog-like adapter for environments without structlog."""

    def __init__(self, logger: logging.Logger, bound: dict[str, Any] | None = None) -> None:
        self._logger = logger
        self._bound = bound or {}

    def bind(self, **kwargs: Any) -> "_StdlibBoundLogger":
        return _StdlibBoundLogger(self._logger, {**self._bound, **kwargs})

    def _log(self, level: str, event: str, *args: Any, **kwargs: Any) -> None:
        extra = kwargs.pop("extra", {})
        merged_extra = {**self._bound, **extra, **kwargs}
        log_method = getattr(self._logger, level)
        log_method(event, *args, extra=merged_extra if merged_extra else None)

    def debug(self, event: str, *args: Any, **kwargs: Any) -> None:
        self._log("debug", event, *args, **kwargs)

    def info(self, event: str, *args: Any, **kwargs: Any) -> None:
        self._log("info", event, *args, **kwargs)

    def warning(self, event: str, *args: Any, **kwargs: Any) -> None:
        self._log("warning", event, *args, **kwargs)

    def error(self, event: str, *args: Any, **kwargs: Any) -> None:
        self._log("error", event, *args, **kwargs)

    def exception(self, event: str, *args: Any, **kwargs: Any) -> None:
        self._log("exception", event, *args, **kwargs)


class BufferProcessor:
    """Custom structlog processor to route logs to WorkOrderLogBuffer and TelemetryEventStore.

    Buffers logs that have 'work_order_id' in their context for SSE streaming.
    Optionally persists all correlated events to a TelemetryEventStore for
    durable historical storage beyond the in-memory buffer retention window.
    """

    def __init__(
        self,
        buffer: WorkOrderLogBuffer,
        telemetry_store: TelemetryEventStore | None = None,
    ) -> None:
        """Initialize processor with a log buffer and optional telemetry store.

        Args:
            buffer: The WorkOrderLogBuffer instance to write logs to.
            telemetry_store: Optional persistent store for historical telemetry.
        """
        self.buffer = buffer
        self.telemetry_store = telemetry_store

    def __call__(
        self, logger: Any, method_name: str, event_dict: MutableMapping[str, Any]
    ) -> MutableMapping[str, Any]:
        """Process log event and route to buffer and telemetry store.

        Adds the event to the in-memory buffer when work_order_id is present.
        Persists the event to the telemetry store when any correlation field
        (work_order_id, task_id, run_id, project_id) is present.

        Args:
            logger: The logger instance
            method_name: The log level method name
            event_dict: Dictionary containing log event data

        Returns:
            Unmodified event_dict (pass-through processor)
        """
        work_order_id = event_dict.get("work_order_id")
        if work_order_id:
            # Extract core fields
            level = event_dict.get("level", method_name)
            event = event_dict.get("event", "")
            timestamp = event_dict.get("timestamp", "")

            # Get all extra fields (everything except core fields)
            extra = {
                k: v
                for k, v in event_dict.items()
                if k not in ("work_order_id", "level", "event", "timestamp")
            }

            # Add to in-memory buffer for SSE streaming
            self.buffer.add_log(
                work_order_id=work_order_id,
                level=level,
                event=event,
                timestamp=timestamp,
                **extra,
            )

        # Persist to durable store when any correlation field is present
        if self.telemetry_store is not None:
            self.telemetry_store.persist_event(dict(event_dict))

        return event_dict


def configure_structured_logging(log_level: str = "INFO") -> None:
    """Configure structlog with console rendering.

    Event naming convention: {module}_{noun}_{verb_past_tense}
    Examples:
        - agent_work_order_created
        - git_branch_created
        - workflow_phase_started
        - sandbox_cleanup_completed

    Args:
        log_level: Minimum log level (DEBUG, INFO, WARNING, ERROR)
    """
    if structlog is None:
        logging.basicConfig(level=getattr(logging, log_level.upper(), logging.INFO))
        return

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def configure_structured_logging_with_buffer(
    log_level: str,
    buffer: WorkOrderLogBuffer,
    telemetry_store: TelemetryEventStore | None = None,
) -> None:
    """Configure structlog with console rendering, log buffering, and telemetry persistence.

    This configuration enables SSE streaming by routing logs to the buffer
    while maintaining console output for local development. When a
    TelemetryEventStore is provided, correlated events are also persisted to
    SQLite for historical querying beyond the in-memory buffer.

    Args:
        log_level: Minimum log level (DEBUG, INFO, WARNING, ERROR)
        buffer: WorkOrderLogBuffer instance to store logs for streaming
        telemetry_store: Optional persistent store for historical telemetry

    Examples:
        buffer = WorkOrderLogBuffer()
        store = TelemetryEventStore("/tmp/telemetry.db")
        configure_structured_logging_with_buffer("INFO", buffer, store)
    """
    if structlog is None:
        logging.basicConfig(level=getattr(logging, log_level.upper(), logging.INFO))
        return

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            BufferProcessor(buffer, telemetry_store),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def bind_work_order_context(work_order_id: str) -> None:
    """Bind work order ID to the current context.

    All logs in this context will include the work_order_id automatically.
    Convenience wrapper around structlog.contextvars.bind_contextvars.

    Args:
        work_order_id: The work order ID to bind to the context

    Examples:
        bind_work_order_context("wo-abc123")
        logger.info("step_started", step="planning")
        # Log will include work_order_id="wo-abc123" automatically
    """
    bind_contextvars(work_order_id=work_order_id)


def clear_work_order_context() -> None:
    """Clear the work order context.

    Should be called when work order execution completes to prevent
    context leakage to other work orders.
    Convenience wrapper around structlog.contextvars.clear_contextvars.

    Examples:
        try:
            bind_work_order_context("wo-abc123")
            # ... execute work order ...
        finally:
            clear_work_order_context()
    """
    clear_contextvars()


def get_logger(name: str | None = None) -> Any:
    """Get a structured logger instance.

    Args:
        name: Optional name for the logger

    Returns:
        Configured structlog logger

    Examples:
        logger = get_logger(__name__)
        logger.info("operation_completed", duration_ms=123)
    """
    if structlog is None:
        return _StdlibBoundLogger(logging.getLogger(name))
    return structlog.get_logger(name)  # type: ignore[no-any-return]
