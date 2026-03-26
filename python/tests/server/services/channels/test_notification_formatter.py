"""Tests for NotificationFormatter.format_telegram."""

import pytest

from src.server.services.channels.notification_formatter import NotificationFormatter
from src.server.services.engine.notifier import TaskEvent


def _evt(event: str, task_id: str = "t-1", **data) -> TaskEvent:
    return TaskEvent(event=event, task_id=task_id, data=data)


class TestFormatTelegramTaskStarted:
    def test_shows_title_not_uuid(self):
        msg = NotificationFormatter.format_telegram(
            _evt("task_started", title="Auth Feature", priority="high", assignee="Agent")
        )
        assert "Auth Feature" in msg
        assert "t-1" not in msg

    def test_shows_priority_with_emoji(self):
        msg = NotificationFormatter.format_telegram(
            _evt("task_started", title="Auth", priority="high")
        )
        assert "🔺" in msg
        assert "high" in msg

    def test_shows_assignee(self):
        msg = NotificationFormatter.format_telegram(
            _evt("task_started", title="Auth", assignee="Claude")
        )
        assert "Claude" in msg

    def test_shows_project_id(self):
        msg = NotificationFormatter.format_telegram(
            _evt("task_started", title="Auth", project_id="proj-123")
        )
        assert "proj-123" in msg

    def test_falls_back_to_task_id_when_no_title(self):
        msg = NotificationFormatter.format_telegram(
            _evt("task_started", task_id="t-99")
        )
        assert "t-99" in msg

    def test_medium_priority_emoji(self):
        msg = NotificationFormatter.format_telegram(
            _evt("task_started", title="Work", priority="medium")
        )
        assert "🔸" in msg

    def test_low_priority_emoji(self):
        msg = NotificationFormatter.format_telegram(
            _evt("task_started", title="Work", priority="low")
        )
        assert "🔹" in msg

    def test_no_raw_internal_fields(self):
        msg = NotificationFormatter.format_telegram(
            _evt("task_started", title="Auth", tags=["tag1"], source_app="leankit")
        )
        assert "source_app" not in msg
        assert "tags" not in msg


class TestFormatTelegramTaskCompleted:
    def test_shows_title(self):
        msg = NotificationFormatter.format_telegram(
            _evt("task_completed", title="Auth Feature", result="SUCCESS", files_changed=3)
        )
        assert "Auth Feature" in msg
        assert "t-1" not in msg

    def test_shows_result(self):
        msg = NotificationFormatter.format_telegram(
            _evt("task_completed", title="Auth", result="SUCCESS")
        )
        assert "SUCCESS" in msg

    def test_shows_files_changed(self):
        msg = NotificationFormatter.format_telegram(
            _evt("task_completed", title="Auth", files_changed=5)
        )
        assert "5" in msg
        assert "Files changed" in msg

    def test_shows_duration_formatted(self):
        msg = NotificationFormatter.format_telegram(
            _evt("task_completed", title="Auth", duration_seconds=125)
        )
        assert "2m 5s" in msg

    def test_shows_duration_seconds_only(self):
        msg = NotificationFormatter.format_telegram(
            _evt("task_completed", title="Auth", duration_seconds=45)
        )
        assert "45s" in msg

    def test_shows_cost(self):
        msg = NotificationFormatter.format_telegram(
            _evt("task_completed", title="Auth", cost=0.0123)
        )
        assert "0.0123" in msg

    def test_falls_back_to_task_id_when_no_title(self):
        msg = NotificationFormatter.format_telegram(
            _evt("task_completed", task_id="t-42", result="SUCCESS")
        )
        assert "t-42" in msg

    def test_omits_none_fields(self):
        msg = NotificationFormatter.format_telegram(
            _evt("task_completed", title="Auth", duration_seconds=None, files_changed=None)
        )
        assert "Duration" not in msg
        assert "Files changed" not in msg


class TestFormatTelegramTaskDone:
    def test_shows_title(self):
        msg = NotificationFormatter.format_telegram(
            _evt("task_done", title="Auth Feature")
        )
        assert "Auth Feature" in msg
        assert "🏁" in msg

    def test_falls_back_to_task_id(self):
        msg = NotificationFormatter.format_telegram(_evt("task_done", task_id="t-7"))
        assert "t-7" in msg


class TestFormatTelegramExistingEvents:
    def test_task_review_ready_uses_title(self):
        msg = NotificationFormatter.format_telegram(
            _evt("task_review_ready", title="Fix Auth", verdict="PASS", confidence=0.9)
        )
        assert "Fix Auth" in msg
        assert "PASS" in msg

    def test_task_failed_uses_title(self):
        msg = NotificationFormatter.format_telegram(
            _evt("task_failed", title="Fix Auth", error="Timeout")
        )
        assert "Fix Auth" in msg
        assert "Timeout" in msg

    def test_task_escalated_uses_title(self):
        msg = NotificationFormatter.format_telegram(
            _evt("task_escalated", title="Deploy", reason="Manual review needed")
        )
        assert "Deploy" in msg
        assert "Manual review needed" in msg


class TestFormatTelegramDefaultFallback:
    def test_skips_internal_fields(self):
        msg = NotificationFormatter.format_telegram(
            _evt("unknown_event", task_id="t-1", source_app="leankit", project_id="p-1", tags=None)
        )
        assert "source_app" not in msg
        assert "project_id" not in msg
        assert "tags" not in msg

    def test_skips_none_values(self):
        msg = NotificationFormatter.format_telegram(
            _evt("unknown_event", task_id="t-1", some_field=None)
        )
        assert "some_field" not in msg

    def test_shows_non_internal_fields(self):
        msg = NotificationFormatter.format_telegram(
            _evt("unknown_event", task_id="t-1", status="active")
        )
        assert "status" in msg
        assert "active" in msg


def _digest(events, limit=20):
    """Helper to build a digest with all events classified as 'digest'."""
    return NotificationFormatter.build_telegram_daily_digest(
        events,
        digest_limit=limit,
        delivery_classifier=lambda evt: "digest",
    )


class TestDigestExternalRequestSummary:
    def test_shows_external_requests_section_when_present(self):
        events = [
            _evt("external_request_created", task_id="", external_request_id="r-1", source_channel="openclaw"),
        ]
        digest = _digest(events)
        assert "External Requests: 1" in digest

    def test_shows_received_status_for_created_events(self):
        events = [
            _evt("external_request_created", task_id="", external_request_id="r-1", source_channel="openclaw"),
            _evt("external_request_created", task_id="", external_request_id="r-2", source_channel="openclaw"),
        ]
        digest = _digest(events)
        assert "External Requests: 2" in digest
        assert "received: 2" in digest

    def test_shows_materialized_status_with_target(self):
        events = [
            _evt("external_request_materialized", task_id="t-1", external_request_id="r-1", materialized_target="task"),
        ]
        digest = _digest(events)
        assert "External Requests: 1" in digest
        assert "materialized (task): 1" in digest

    def test_shows_mixed_status_breakdown(self):
        events = [
            _evt("external_request_created", task_id="", external_request_id="r-1", source_channel="openclaw"),
            _evt("external_request_materialized", task_id="t-1", external_request_id="r-2", materialized_target="approval"),
        ]
        digest = _digest(events)
        assert "External Requests: 2" in digest
        assert "received: 1" in digest
        assert "materialized (approval): 1" in digest

    def test_shows_source_channel_breakdown(self):
        events = [
            _evt("external_request_created", task_id="", external_request_id="r-1", source_channel="openclaw"),
            _evt("external_request_created", task_id="", external_request_id="r-2", source_channel="openclaw"),
            _evt("external_request_created", task_id="", external_request_id="r-3", source_channel="telegram"),
        ]
        digest = _digest(events)
        assert "openclaw(2)" in digest
        assert "telegram(1)" in digest

    def test_no_external_requests_section_when_none(self):
        events = [
            _evt("task_started", title="Auth"),
            _evt("task_completed", title="Auth"),
        ]
        digest = _digest(events)
        assert "External Requests" not in digest

    def test_external_requests_coexist_with_task_events(self):
        events = [
            _evt("task_started", title="Auth"),
            _evt("external_request_created", task_id="", external_request_id="r-1", source_channel="openclaw"),
        ]
        digest = _digest(events)
        assert "External Requests: 1" in digest
        assert "Events: 2" in digest

    def test_no_source_line_when_channel_absent(self):
        events = [
            _evt("external_request_created", task_id="", external_request_id="r-1"),
        ]
        digest = _digest(events)
        assert "External Requests: 1" in digest
        assert "sources:" not in digest
