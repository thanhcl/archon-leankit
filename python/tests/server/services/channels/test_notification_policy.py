"""Tests for notification routing policy helpers."""

from types import SimpleNamespace

from src.server.services.channels.notification_policy import NotificationPolicy
from src.server.services.engine.notifier import TaskEvent


def test_policy_classifies_notify_event_as_immediate():
    policy = NotificationPolicy(
        telegram_only_critical=True,
        telegram_notify_events=["task_review_ready"],
        telegram_digest_events=["approval_requested"],
    )

    evt = TaskEvent(event="task_review_ready", task_id="task-1", data={}, is_critical=False)

    assert policy.classify_telegram_delivery(evt) == "immediate"
    assert policy.should_send_telegram(evt) is True


def test_policy_classifies_digest_event_as_digest():
    policy = NotificationPolicy(
        telegram_only_critical=True,
        telegram_notify_events=[],
        telegram_digest_events=["task_review_ready"],
    )

    evt = TaskEvent(event="task_review_ready", task_id="task-1", data={}, is_critical=False)

    assert policy.classify_telegram_delivery(evt) == "digest"
    assert policy.is_telegram_routed(evt) is True
    assert policy.should_send_telegram(evt) is False


def test_policy_skips_noncritical_event_when_only_critical_is_enabled():
    policy = NotificationPolicy(
        telegram_only_critical=True,
        telegram_notify_events=[],
        telegram_digest_events=[],
    )

    evt = TaskEvent(event="task_started", task_id="task-1", data={}, is_critical=False)

    assert policy.classify_telegram_delivery(evt) == "skip"
    assert policy.is_telegram_routed(evt) is False


def test_policy_from_runtime_config_reads_config_like_object():
    config = SimpleNamespace(
        telegram_only_critical=False,
        telegram_notify_events=["task_failed"],
        telegram_digest_events=["task_review_ready"],
    )

    policy = NotificationPolicy.from_runtime_config(config)

    assert policy.classify_telegram_delivery(
        TaskEvent(event="task_failed", task_id="task-1", data={}, is_critical=True)
    ) == "immediate"
    assert policy.classify_telegram_delivery(
        TaskEvent(event="task_review_ready", task_id="task-2", data={}, is_critical=False)
    ) == "digest"
