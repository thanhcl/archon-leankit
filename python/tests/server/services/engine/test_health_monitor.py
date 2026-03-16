"""Tests for HealthMonitor — metrics computation, thresholds, alerts."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.server.services.engine.health_monitor import (
    EngineMetrics,
    HealthMonitor,
    HealthThresholds,
)


def _make_task(status="done", retry_count=0, minutes_ago=30):
    now = datetime.now()
    created = (now - timedelta(minutes=minutes_ago)).isoformat()
    updated = now.isoformat()
    return {
        "id": f"task-{status}-{retry_count}",
        "status": status,
        "retry_count": retry_count,
        "created_at": created,
        "updated_at": updated,
        "state_changed_at": updated,
    }


def _mock_task_service(tasks):
    svc = MagicMock()
    svc.list_tasks.return_value = (True, {"tasks": tasks})
    return svc


# -- Metrics computation --


class TestComputeMetrics:
    def test_empty_tasks(self):
        monitor = HealthMonitor(task_service=_mock_task_service([]))
        metrics = monitor.compute_metrics()
        assert metrics.total_completed == 0
        assert metrics.total_processed == 0
        assert metrics.first_pass_rate == 1.0

    def test_all_first_pass(self):
        tasks = [_make_task("done", retry_count=0) for _ in range(5)]
        monitor = HealthMonitor(task_service=_mock_task_service(tasks))
        metrics = monitor.compute_metrics()
        assert metrics.total_completed == 5
        assert metrics.first_pass_rate == 1.0

    def test_mixed_retries(self):
        tasks = [
            _make_task("done", retry_count=0),
            _make_task("done", retry_count=0),
            _make_task("done", retry_count=2),
            _make_task("done", retry_count=1),
        ]
        monitor = HealthMonitor(task_service=_mock_task_service(tasks))
        metrics = monitor.compute_metrics()
        assert metrics.total_completed == 4
        assert metrics.first_pass_rate == 0.5  # 2 out of 4
        assert metrics.avg_retries == 0.75  # (0+0+2+1)/4

    def test_escalated_tasks(self):
        tasks = [
            _make_task("done"), _make_task("done"),
            _make_task("escalated"), _make_task("escalated"),
        ]
        monitor = HealthMonitor(task_service=_mock_task_service(tasks))
        metrics = monitor.compute_metrics()
        assert metrics.total_processed == 4
        assert metrics.escalation_rate == 0.5

    def test_failed_tasks(self):
        tasks = [_make_task("failed") for _ in range(4)]
        monitor = HealthMonitor(task_service=_mock_task_service(tasks))
        metrics = monitor.compute_metrics()
        assert metrics.engine_errors == 4

    def test_delivery_time(self):
        tasks = [_make_task("done", minutes_ago=10)]
        monitor = HealthMonitor(task_service=_mock_task_service(tasks))
        metrics = monitor.compute_metrics()
        # Should be approximately 10 minutes = 600 seconds
        assert 500 < metrics.avg_delivery_seconds < 700

    def test_no_task_service(self):
        monitor = HealthMonitor(task_service=None)
        metrics = monitor.compute_metrics()
        assert metrics.total_processed == 0


# -- Threshold evaluation --


class TestThresholdEvaluation:
    def test_healthy_metrics_no_alerts(self):
        monitor = HealthMonitor(
            task_service=_mock_task_service([]),
            thresholds=HealthThresholds(),
        )
        metrics = EngineMetrics(
            total_completed=10,
            total_processed=10,
            first_pass_rate=0.8,
            escalation_rate=0.05,
            avg_retries=1.0,
            avg_delivery_seconds=300,
            engine_errors=1,
        )
        alerts = monitor._evaluate_thresholds(metrics)
        assert len(alerts) == 0

    def test_low_first_pass_rate(self):
        monitor = HealthMonitor(thresholds=HealthThresholds(first_pass_rate_min=0.6))
        metrics = EngineMetrics(total_processed=10, first_pass_rate=0.4)
        alerts = monitor._evaluate_thresholds(metrics)
        assert any(a.metric == "first_pass_rate" for a in alerts)

    def test_high_escalation_rate(self):
        monitor = HealthMonitor(thresholds=HealthThresholds(escalation_rate_max=0.15))
        metrics = EngineMetrics(total_processed=10, escalation_rate=0.3)
        alerts = monitor._evaluate_thresholds(metrics)
        assert any(a.metric == "escalation_rate" for a in alerts)

    def test_high_avg_retries(self):
        monitor = HealthMonitor(thresholds=HealthThresholds(avg_retries_max=2.0))
        metrics = EngineMetrics(total_processed=10, avg_retries=3.5)
        alerts = monitor._evaluate_thresholds(metrics)
        assert any(a.metric == "avg_retries" for a in alerts)

    def test_slow_delivery(self):
        monitor = HealthMonitor(thresholds=HealthThresholds(avg_delivery_max=900))
        metrics = EngineMetrics(total_processed=10, avg_delivery_seconds=1200)
        alerts = monitor._evaluate_thresholds(metrics)
        assert any(a.metric == "avg_delivery_seconds" for a in alerts)

    def test_many_engine_errors(self):
        monitor = HealthMonitor(thresholds=HealthThresholds(engine_errors_max=3))
        metrics = EngineMetrics(total_processed=10, engine_errors=5)
        alerts = monitor._evaluate_thresholds(metrics)
        assert any(a.metric == "engine_errors" for a in alerts)

    def test_critical_severity(self):
        monitor = HealthMonitor(thresholds=HealthThresholds(first_pass_rate_min=0.6))
        metrics = EngineMetrics(total_processed=10, first_pass_rate=0.2)  # < 0.3 = critical
        alerts = monitor._evaluate_thresholds(metrics)
        fpr_alert = next(a for a in alerts if a.metric == "first_pass_rate")
        assert fpr_alert.severity == "critical"

    def test_no_alerts_when_no_processed(self):
        monitor = HealthMonitor()
        metrics = EngineMetrics(total_processed=0)
        assert monitor._evaluate_thresholds(metrics) == []


# -- Auto-remediation --


class TestAutoRemediation:
    def test_auto_action_on_critical(self):
        monitor = HealthMonitor(
            thresholds=HealthThresholds(first_pass_rate_min=0.6),
            auto_remediation=True,
        )
        metrics = EngineMetrics(total_processed=10, first_pass_rate=0.2)
        alerts = monitor._evaluate_thresholds(metrics)
        fpr_alert = next(a for a in alerts if a.metric == "first_pass_rate")
        assert fpr_alert.auto_action_taken is not None
        assert "api" in fpr_alert.auto_action_taken.lower()

    def test_no_auto_action_when_disabled(self):
        monitor = HealthMonitor(
            thresholds=HealthThresholds(first_pass_rate_min=0.6),
            auto_remediation=False,
        )
        metrics = EngineMetrics(total_processed=10, first_pass_rate=0.2)
        alerts = monitor._evaluate_thresholds(metrics)
        fpr_alert = next(a for a in alerts if a.metric == "first_pass_rate")
        assert fpr_alert.auto_action_taken is None


# -- check_health integration --


class TestCheckHealth:
    @pytest.mark.asyncio
    async def test_check_health_notifies(self):
        tasks = [_make_task("failed") for _ in range(5)]
        notifier = MagicMock()
        notifier.on_health_alert = AsyncMock()

        monitor = HealthMonitor(
            task_service=_mock_task_service(tasks),
            notifier=notifier,
            thresholds=HealthThresholds(engine_errors_max=2),
        )
        alerts = await monitor.check_health()

        assert len(alerts) > 0
        notifier.on_health_alert.assert_called()
        assert monitor.latest_metrics is not None

    @pytest.mark.asyncio
    async def test_alert_history_accumulated(self):
        tasks = [_make_task("failed") for _ in range(5)]
        monitor = HealthMonitor(
            task_service=_mock_task_service(tasks),
            thresholds=HealthThresholds(engine_errors_max=2),
        )
        await monitor.check_health()
        await monitor.check_health()
        assert len(monitor.alert_history) >= 2


# -- Lifecycle --


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_start_stop(self):
        monitor = HealthMonitor(check_interval=1)
        await monitor.start()
        assert monitor._running is True
        await monitor.stop()
        assert monitor._running is False

    @pytest.mark.asyncio
    async def test_metrics_to_dict(self):
        metrics = EngineMetrics(
            total_completed=10, total_processed=12,
            first_pass_rate=0.8, escalation_rate=0.1,
        )
        d = metrics.to_dict()
        assert d["total_completed"] == 10
        assert d["first_pass_rate"] == 0.8
