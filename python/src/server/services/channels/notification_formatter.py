"""Notification formatting helpers shared by transport adapters."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..engine.notifier import TaskEvent


_EXTERNAL_REQUEST_EVENTS = {"external_request_created", "external_request_materialized"}


class NotificationFormatter:
    """Shared formatting helpers for notification channels."""

    @staticmethod
    def format_telegram(evt: TaskEvent) -> str:
        icon = "🔴" if evt.is_critical else "🔵"
        title = evt.data.get("title")
        summary = evt.data.get("summary")
        reason = evt.data.get("reason") or evt.data.get("error")
        lines = [f"{icon} *{evt.event}*"]

        if evt.event == "task_started":
            task_label = title or evt.task_id
            priority = evt.data.get("priority")
            assignee = evt.data.get("assignee")
            project_id = evt.data.get("project_id")
            priority_emoji = {"high": "🔺", "medium": "🔸", "low": "🔹"}.get(str(priority).lower(), "▪️") if priority else "▪️"
            lines.append(f"🚀 Task started: *{task_label}*")
            if priority:
                lines.append(f"{priority_emoji} Priority: {priority}")
            if assignee:
                lines.append(f"👤 Assignee: {assignee}")
            if project_id:
                lines.append(f"📁 Project: `{project_id}`")
            return "\n".join(lines)

        if evt.event == "task_completed":
            task_label = title or evt.task_id
            result = evt.data.get("result")
            files_changed = evt.data.get("files_changed")
            duration_seconds = evt.data.get("duration_seconds")
            cost = evt.data.get("cost")
            lines.append(f"✅ Task completed: *{task_label}*")
            if result:
                lines.append(f"Result: {result}")
            if files_changed is not None:
                lines.append(f"📝 Files changed: {files_changed}")
            if duration_seconds is not None:
                minutes, seconds = divmod(int(duration_seconds), 60)
                duration_str = f"{minutes}m {seconds}s" if minutes else f"{seconds}s"
                lines.append(f"⏱ Duration: {duration_str}")
            if cost is not None:
                lines.append(f"💰 Cost: ${cost:.4f}")
            return "\n".join(lines)

        if evt.event == "task_done":
            task_label = title or evt.task_id
            lines.append(f"🏁 Task done: *{task_label}*")
            return "\n".join(lines)

        if evt.event == "task_review_ready":
            task_label = title or evt.task_id
            lines.append(f"Task ready for review: *{task_label}*")
            if summary:
                lines.append(f"Summary: {summary}")
            verdict = evt.data.get("verdict")
            if verdict:
                lines.append(f"Verdict: {verdict}")
            confidence = evt.data.get("confidence")
            if confidence is not None:
                lines.append(f"Confidence: {confidence}")
            return "\n".join(lines)

        if evt.event == "task_failed":
            task_label = title or evt.task_id
            lines.append(f"Task failed: *{task_label}*")
            if reason:
                lines.append(f"Reason: {reason}")
            return "\n".join(lines)

        if evt.event == "task_escalated":
            task_label = title or evt.task_id
            lines.append(f"Task escalated: *{task_label}*")
            if reason:
                lines.append(f"Reason: {reason}")
            return "\n".join(lines)

        if evt.event == "approval_requested":
            lines.append(f"Approval request: `{evt.data.get('approval_request_id', 'unknown')}`")
            if title:
                lines.append(f"Title: {title}")
            if summary:
                lines.append(f"Summary: {summary}")
            return "\n".join(lines)

        if evt.event == "external_request_created":
            lines.append(f"External request: `{evt.data.get('external_request_id', 'unknown')}`")
            if title:
                lines.append(f"Title: {title}")
            source_channel = evt.data.get("source_channel")
            if source_channel:
                lines.append(f"Channel: {source_channel}")
            return "\n".join(lines)

        # Default fallback: skip internal/noisy fields
        _INTERNAL_FIELDS = {"source_app", "project_id", "tags"}
        if evt.task_id:
            lines.append(f"Task: `{evt.task_id}`")
        for key, value in evt.data.items():
            if key in _INTERNAL_FIELDS or value is None:
                continue
            lines.append(f"  {key}: {value}")
        return "\n".join(lines)

    @staticmethod
    def build_telegram_daily_digest(
        events: list[TaskEvent],
        *,
        digest_limit: int,
        delivery_classifier: Any,
    ) -> str:
        """Build a compact Telegram-friendly daily digest from recent events."""
        digest_candidates = [
            evt for evt in events
            if delivery_classifier(evt) in {"immediate", "digest"}
        ]
        digest_candidates = digest_candidates[-digest_limit:]

        if not digest_candidates:
            return "🟦 *LeanKit Daily Digest*\nNo routed events in the current digest window."

        counts: dict[str, int] = {}
        critical_count = 0
        for evt in digest_candidates:
            counts[evt.event] = counts.get(evt.event, 0) + 1
            if evt.is_critical:
                critical_count += 1

        lines = [
            "🟦 *LeanKit Daily Digest*",
            f"Events: {len(digest_candidates)}",
            f"Critical: {critical_count}",
            "Breakdown:",
        ]
        for event_name, count in sorted(counts.items()):
            lines.append(f"- {event_name}: {count}")

        ext_events = [evt for evt in digest_candidates if evt.event in _EXTERNAL_REQUEST_EVENTS]
        if ext_events:
            status_counts: dict[str, int] = {}
            for evt in ext_events:
                if evt.event == "external_request_created":
                    status = "received"
                else:
                    target = evt.data.get("materialized_target") or "materialized"
                    status = f"materialized ({target})"
                status_counts[status] = status_counts.get(status, 0) + 1

            source_counts: dict[str, int] = {}
            for evt in ext_events:
                channel = evt.data.get("source_channel")
                if channel:
                    source_counts[channel] = source_counts.get(channel, 0) + 1

            lines.append(f"External Requests: {len(ext_events)}")
            for status, count in sorted(status_counts.items()):
                lines.append(f"  {status}: {count}")
            if source_counts:
                channels_str = ", ".join(f"{ch}({n})" for ch, n in sorted(source_counts.items()))
                lines.append(f"  sources: {channels_str}")

        lines.append("Highlights:")
        for evt in digest_candidates[-3:]:
            subject = (
                evt.task_id
                or evt.data.get("approval_request_id")
                or evt.data.get("external_request_id")
                or "n/a"
            )
            lines.append(f"- {evt.event} [{subject}]")

        return "\n".join(lines)

    @staticmethod
    def format_discord(evt: TaskEvent) -> str:
        """Format one event for Discord webhook delivery."""
        icon = "🔴" if evt.is_critical else "🔵"
        lines = [f"{icon} **{evt.event}**"]
        if evt.task_id:
            lines.append(f"Task: `{evt.task_id}`")
        for key, value in evt.data.items():
            lines.append(f"  {key}: {value}")
        return "\n".join(lines)
