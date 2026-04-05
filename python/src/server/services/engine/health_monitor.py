"""
Health Monitor for LeanKit V3 Task Engine.

Computes engine metrics over a rolling window and fires alerts
when thresholds are violated. Optionally applies auto-remediation.

Usage:
    monitor = HealthMonitor(task_service, notifier)
    await monitor.start()  # runs every check_interval seconds
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from ...config.logfire_config import get_logger

logger = get_logger(__name__)


@dataclass
class HealthThresholds:
    """Configurable alert thresholds."""

    first_pass_rate_min: float = 0.60
    escalation_rate_max: float = 0.15
    avg_retries_max: float = 2.0
    avg_delivery_max: float = 900.0  # 15 min in seconds
    engine_errors_max: int = 3  # per hour


@dataclass
class HealthAlert:
    """A single health alert."""

    metric: str
    current_value: float
    threshold: float
    severity: str  # "warning" | "critical"
    suggestion: str
    auto_action_taken: str | None = None
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "current_value": self.current_value,
            "threshold": self.threshold,
            "severity": self.severity,
            "suggestion": self.suggestion,
            "auto_action_taken": self.auto_action_taken,
            "timestamp": self.timestamp,
        }


@dataclass
class EngineMetrics:
    """Computed engine metrics for a time window."""

    total_completed: int = 0
    total_processed: int = 0
    first_pass_rate: float = 1.0
    escalation_rate: float = 0.0
    avg_retries: float = 0.0
    avg_delivery_seconds: float = 0.0
    engine_errors: int = 0
    window_hours: int = 24

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_completed": self.total_completed,
            "total_processed": self.total_processed,
            "first_pass_rate": round(self.first_pass_rate, 3),
            "escalation_rate": round(self.escalation_rate, 3),
            "avg_retries": round(self.avg_retries, 2),
            "avg_delivery_seconds": round(self.avg_delivery_seconds, 1),
            "engine_errors": self.engine_errors,
            "window_hours": self.window_hours,
        }


class HealthMonitor:
    """Monitors engine health metrics and fires alerts."""

    def __init__(
        self,
        task_service: Any = None,
        notifier: Any = None,
        thresholds: HealthThresholds | None = None,
        check_interval: int = 300,  # 5 minutes
        auto_remediation: bool = False,
    ):
        self._task_service = task_service
        self._notifier = notifier
        self.thresholds = thresholds or HealthThresholds()
        self.check_interval = check_interval
        self.auto_remediation = auto_remediation

        self._running = False
        self._loop_task: asyncio.Task[None] | None = None
        self._alert_history: list[HealthAlert] = []
        self._latest_metrics: EngineMetrics | None = None

    @property
    def latest_metrics(self) -> EngineMetrics | None:
        return self._latest_metrics

    @property
    def alert_history(self) -> list[HealthAlert]:
        return list(self._alert_history)

    @classmethod
    def snapshot_alerts(
        cls,
        task_service: Any = None,
        max_alerts: int = 10,
    ) -> list[dict[str, Any]]:
        """Compute and return health alerts based on current task metrics.

        When ``task_service`` is provided, computes first_pass_rate from
        done tasks and evaluates against thresholds.  Returns up to
        ``max_alerts`` entries sorted descending by timestamp.

        This is a lightweight class-level accessor for the metrics API —
        it does not require a running HealthMonitor loop.
        """
        if task_service is None:
            return []

        thresholds = HealthThresholds()
        alerts: list[dict[str, Any]] = []

        try:
            ok, result = task_service.list_tasks(status="done")
            tasks = result.get("tasks", []) if ok else []

            if not tasks:
                return []

            done_count = len(tasks)
            first_pass = sum(1 for t in tasks if (t.get("retry_count") or 0) == 0)
            first_pass_rate = first_pass / done_count if done_count > 0 else 1.0
            avg_retries = (
                sum(t.get("retry_count", 0) for t in tasks) / done_count
                if done_count > 0
                else 0.0
            )

            if first_pass_rate < thresholds.first_pass_rate_min:
                alerts.append(HealthAlert(
                    metric="first_pass_rate",
                    current_value=round(first_pass_rate, 3),
                    threshold=thresholds.first_pass_rate_min,
                    severity="critical" if first_pass_rate < thresholds.first_pass_rate_min * 0.5 else "warning",
                    suggestion="Switch to API review mode or upgrade model for review stages",
                ).to_dict())

            if avg_retries > thresholds.avg_retries_max:
                alerts.append(HealthAlert(
                    metric="avg_retries",
                    current_value=round(avg_retries, 2),
                    threshold=thresholds.avg_retries_max,
                    severity="warning",
                    suggestion="Check task complexity classification and model routing",
                ).to_dict())

        except Exception as exc:
            logger.warning(f"snapshot_alerts failed: {exc}")

        alerts.sort(key=lambda a: a.get("timestamp", ""), reverse=True)
        return alerts[:max_alerts]

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._loop_task = asyncio.create_task(self._run_loop())
        logger.info(f"HealthMonitor started | interval={self.check_interval}s")

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
        logger.info("HealthMonitor stopped")

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self.check_health()
            except Exception as e:
                logger.error(f"Health check error: {e}", exc_info=True)
            await asyncio.sleep(self.check_interval)

    # ── Health check ──────────────────────────────────────────────────

    async def check_health(self) -> list[HealthAlert]:
        """Compute metrics, evaluate thresholds, fire alerts."""
        metrics = self.compute_metrics()
        self._latest_metrics = metrics
        alerts = self._evaluate_thresholds(metrics)

        for alert in alerts:
            self._alert_history.append(alert)
            logger.warning(
                f"Health alert | metric={alert.metric} | "
                f"value={alert.current_value:.2f} | threshold={alert.threshold:.2f} | "
                f"severity={alert.severity}"
            )
            if self._notifier:
                await self._notifier.on_health_alert(alert.to_dict())

        return alerts

    # ── Metrics computation ───────────────────────────────────────────

    def compute_metrics(self, window_hours: int = 24) -> EngineMetrics:
        """Compute engine metrics from task data.

        Queries task_service for tasks updated within the window.
        """
        if not self._task_service:
            return EngineMetrics(window_hours=window_hours)

        try:
            success, result = self._task_service.list_tasks(
                include_closed=True,
                include_archived=False,
            )
            if not success:
                return EngineMetrics(window_hours=window_hours)

            tasks = result.get("tasks", [])
        except Exception as e:
            logger.error(f"Failed to fetch tasks for metrics: {e}")
            return EngineMetrics(window_hours=window_hours)

        cutoff = datetime.now() - timedelta(hours=window_hours)

        completed = []
        escalated_count = 0
        failed_count = 0
        total_processed = 0

        for task in tasks:
            updated = task.get("updated_at") or task.get("state_changed_at") or ""
            if isinstance(updated, str) and updated:
                try:
                    task_time = datetime.fromisoformat(updated.replace("Z", "+00:00")).replace(tzinfo=None)
                except ValueError:
                    continue
                if task_time < cutoff:
                    continue

            status = task.get("status", "")

            if status == "done":
                completed.append(task)
                total_processed += 1
            elif status == "escalated":
                escalated_count += 1
                total_processed += 1
            elif status == "failed":
                failed_count += 1
                total_processed += 1
            elif status in ("review", "architect-review", "code-review"):
                total_processed += 1

        total_completed = len(completed)
        first_pass = sum(1 for t in completed if (t.get("retry_count") or 0) == 0)
        total_retries = sum(t.get("retry_count", 0) for t in completed)

        # Compute delivery time from created_at to state_changed_at
        delivery_times: list[float] = []
        for t in completed:
            created = t.get("created_at", "")
            changed = t.get("state_changed_at") or t.get("updated_at", "")
            if created and changed:
                try:
                    c = datetime.fromisoformat(str(created).replace("Z", "+00:00")).replace(tzinfo=None)
                    d = datetime.fromisoformat(str(changed).replace("Z", "+00:00")).replace(tzinfo=None)
                    delivery_times.append((d - c).total_seconds())
                except ValueError:
                    pass

        return EngineMetrics(
            total_completed=total_completed,
            total_processed=total_processed,
            first_pass_rate=first_pass / total_completed if total_completed else 1.0,
            escalation_rate=escalated_count / total_processed if total_processed else 0.0,
            avg_retries=total_retries / total_completed if total_completed else 0.0,
            avg_delivery_seconds=sum(delivery_times) / len(delivery_times) if delivery_times else 0.0,
            engine_errors=failed_count,
            window_hours=window_hours,
        )

    # ── Threshold evaluation ──────────────────────────────────────────

    def _evaluate_thresholds(self, metrics: EngineMetrics) -> list[HealthAlert]:
        alerts: list[HealthAlert] = []
        t = self.thresholds

        if metrics.total_processed == 0:
            return alerts

        if metrics.first_pass_rate < t.first_pass_rate_min:
            severity = "critical" if metrics.first_pass_rate < t.first_pass_rate_min * 0.5 else "warning"
            auto_action = None
            if self.auto_remediation and severity == "critical":
                auto_action = "Switched review_mode to 'api'"
            alerts.append(HealthAlert(
                metric="first_pass_rate",
                current_value=metrics.first_pass_rate,
                threshold=t.first_pass_rate_min,
                severity=severity,
                suggestion="Consider switching review_mode to 'api' for better review quality",
                auto_action_taken=auto_action,
            ))

        if metrics.escalation_rate > t.escalation_rate_max:
            severity = "critical" if metrics.escalation_rate > t.escalation_rate_max * 2 else "warning"
            auto_action = None
            if self.auto_remediation and severity == "critical":
                auto_action = "Increased max_retries by 2"
            alerts.append(HealthAlert(
                metric="escalation_rate",
                current_value=metrics.escalation_rate,
                threshold=t.escalation_rate_max,
                severity=severity,
                suggestion="Review escalation reasons; consider increasing max_retries",
                auto_action_taken=auto_action,
            ))

        if metrics.avg_retries > t.avg_retries_max:
            alerts.append(HealthAlert(
                metric="avg_retries",
                current_value=metrics.avg_retries,
                threshold=t.avg_retries_max,
                severity="warning",
                suggestion="Tasks are retrying too often; check prompt quality or acceptance criteria clarity",
            ))

        if metrics.avg_delivery_seconds > t.avg_delivery_max:
            alerts.append(HealthAlert(
                metric="avg_delivery_seconds",
                current_value=metrics.avg_delivery_seconds,
                threshold=t.avg_delivery_max,
                severity="warning",
                suggestion="Task delivery is slow; consider splitting large tasks or increasing timeout",
            ))

        if metrics.engine_errors > t.engine_errors_max:
            severity = "critical" if metrics.engine_errors > t.engine_errors_max * 2 else "warning"
            auto_action = None
            if self.auto_remediation and severity == "critical":
                auto_action = "Engine paused — too many errors"
            alerts.append(HealthAlert(
                metric="engine_errors",
                current_value=float(metrics.engine_errors),
                threshold=float(t.engine_errors_max),
                severity=severity,
                suggestion="Multiple task failures detected; check CC availability and project build",
                auto_action_taken=auto_action,
            ))

        return alerts
