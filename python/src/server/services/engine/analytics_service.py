"""
Engine Analytics Service for LeanKit V3 (Batch 3: C-P6-03, C-P6-04, D-P2-03).

Provides aggregated analytics across execution runs:
- Per-profile success metrics and anomaly detection (C-P6-03)
- Per-model cost breakdown with savings recommendations (C-P6-04)
- Cost attribution by model/profile/task_type/stage with time series (D-P2-03)
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from ...config.logfire_config import get_logger
from ...utils import get_supabase_client

logger = get_logger(__name__)


class EngineAnalyticsService:
    """Aggregated analytics across execution runs."""

    def __init__(self, supabase_client=None):
        self.supabase_client = supabase_client or get_supabase_client()

    # ------------------------------------------------------------------
    # C-P6-03: Token Profile Feedback Loop
    # ------------------------------------------------------------------

    def get_profile_metrics(
        self,
        project_id: str | None = None,
        days: int = 30,
    ) -> dict[str, Any]:
        """Return per-profile success metrics.

        Aggregates: success_rate, avg_cost, avg_retries, avg_duration, run_count
        per token profile. Includes anomaly flags for consistently failing or
        over-provisioned profiles.
        """
        runs = self._fetch_runs(project_id=project_id, days=days)

        profiles: dict[str, dict[str, Any]] = defaultdict(lambda: {
            "total": 0, "success": 0, "failed": 0,
            "total_cost": 0.0, "total_duration": 0.0,
            "total_retries": 0, "task_types": defaultdict(int),
        })

        for run in runs:
            metadata = run.get("metadata") or {}
            profile = metadata.get("profile_used") or "unknown"
            status = run.get("status", "")

            bucket = profiles[profile]
            bucket["total"] += 1
            if status == "completed":
                bucket["success"] += 1
            elif status == "failed":
                bucket["failed"] += 1

            cost = run.get("cost_usd") or 0.0
            bucket["total_cost"] += float(cost)

            duration = run.get("duration_seconds") or 0.0
            bucket["total_duration"] += float(duration)

            retry_idx = run.get("retry_index") or 0
            bucket["total_retries"] += int(retry_idx)

        result: dict[str, Any] = {}
        anomalies: list[dict[str, str]] = []

        for profile_name, bucket in profiles.items():
            total = bucket["total"]
            if total == 0:
                continue

            success_rate = round(bucket["success"] / total, 3)
            avg_cost = round(bucket["total_cost"] / total, 4)
            avg_duration = round(bucket["total_duration"] / total, 1)
            avg_retries = round(bucket["total_retries"] / total, 2)

            result[profile_name] = {
                "run_count": total,
                "success_count": bucket["success"],
                "failed_count": bucket["failed"],
                "success_rate": success_rate,
                "avg_cost_usd": avg_cost,
                "avg_duration_seconds": avg_duration,
                "avg_retries": avg_retries,
                "total_cost_usd": round(bucket["total_cost"], 4),
            }

            # Anomaly detection
            if total >= 5 and success_rate < 0.5:
                anomalies.append({
                    "profile": profile_name,
                    "type": "low_success_rate",
                    "message": f"Profile '{profile_name}' has {success_rate:.0%} success rate "
                               f"over {total} runs — consider upgrading",
                })

            if total >= 5 and success_rate > 0.95 and avg_cost > 0 and avg_retries < 0.1:
                anomalies.append({
                    "profile": profile_name,
                    "type": "over_provisioned",
                    "message": f"Profile '{profile_name}' has {success_rate:.0%} success rate "
                               f"with avg cost ${avg_cost:.4f} and near-zero retries — "
                               f"consider downgrading to save cost",
                })

        return {"profiles": result, "anomalies": anomalies, "period_days": days}

    # ------------------------------------------------------------------
    # C-P6-04: Model Routing Cost Feedback
    # ------------------------------------------------------------------

    def get_model_cost_breakdown(
        self,
        project_id: str | None = None,
        days: int = 30,
    ) -> dict[str, Any]:
        """Return cost breakdown by model × task_type × stage.

        Includes savings recommendations where cheaper models could suffice.
        """
        runs = self._fetch_runs(project_id=project_id, days=days)

        # Aggregate by model × stage
        model_stage: dict[str, dict[str, Any]] = defaultdict(lambda: {
            "total": 0, "success": 0, "total_cost": 0.0, "task_types": defaultdict(int),
        })

        for run in runs:
            model = run.get("model") or "unknown"
            stage = run.get("stage") or "execute"
            status = run.get("status", "")
            cost = float(run.get("cost_usd") or 0.0)

            metadata = run.get("metadata") or {}
            task_type = metadata.get("task_type") or "unknown"

            key = f"{model}:{stage}"
            bucket = model_stage[key]
            bucket["total"] += 1
            bucket["total_cost"] += cost
            if status == "completed":
                bucket["success"] += 1
            bucket["task_types"][task_type] += 1

        breakdown: list[dict[str, Any]] = []
        for key, bucket in model_stage.items():
            model, stage = key.split(":", 1)
            total = bucket["total"]
            if total == 0:
                continue
            breakdown.append({
                "model": model,
                "stage": stage,
                "run_count": total,
                "success_count": bucket["success"],
                "success_rate": round(bucket["success"] / total, 3),
                "total_cost_usd": round(bucket["total_cost"], 4),
                "avg_cost_usd": round(bucket["total_cost"] / total, 4),
                "task_types": dict(bucket["task_types"]),
            })

        # Sort by total cost descending
        breakdown.sort(key=lambda x: x["total_cost_usd"], reverse=True)

        # Generate savings recommendations
        recommendations = self._generate_cost_recommendations(breakdown)

        return {
            "breakdown": breakdown,
            "recommendations": recommendations,
            "period_days": days,
        }

    # ------------------------------------------------------------------
    # D-P2-03: Cost Attribution & Trending
    # ------------------------------------------------------------------

    def get_cost_trending(
        self,
        project_id: str | None = None,
        days: int = 30,
    ) -> dict[str, Any]:
        """Return cost time series and anomaly detection.

        Time series: daily cost aggregated by model and profile.
        Anomaly: runs with cost > 3x average for their profile.
        """
        runs = self._fetch_runs(project_id=project_id, days=days)

        # Daily time series
        daily: dict[str, float] = defaultdict(float)
        profile_costs: dict[str, list[float]] = defaultdict(list)
        anomalous_runs: list[dict[str, Any]] = []

        for run in runs:
            cost = float(run.get("cost_usd") or 0.0)
            if cost <= 0:
                continue

            started_at = run.get("started_at") or ""
            day = started_at[:10] if len(started_at) >= 10 else "unknown"
            daily[day] += cost

            metadata = run.get("metadata") or {}
            profile = metadata.get("profile_used") or "unknown"
            profile_costs[profile].append(cost)

        # Compute profile averages for anomaly detection
        profile_avgs: dict[str, float] = {}
        for profile, costs in profile_costs.items():
            if costs:
                profile_avgs[profile] = sum(costs) / len(costs)

        # Detect anomalous runs (> 3x average for profile)
        for run in runs:
            cost = float(run.get("cost_usd") or 0.0)
            if cost <= 0:
                continue
            metadata = run.get("metadata") or {}
            profile = metadata.get("profile_used") or "unknown"
            avg = profile_avgs.get(profile, 0)
            if avg > 0 and cost > avg * 3:
                anomalous_runs.append({
                    "run_id": run.get("id"),
                    "task_id": run.get("task_id"),
                    "profile": profile,
                    "cost_usd": round(cost, 4),
                    "profile_avg_usd": round(avg, 4),
                    "ratio": round(cost / avg, 1),
                })

        # Sort daily by date
        time_series = [
            {"date": date, "cost_usd": round(cost, 4)}
            for date, cost in sorted(daily.items())
        ]

        total_cost = sum(daily.values())

        return {
            "time_series": time_series,
            "total_cost_usd": round(total_cost, 4),
            "daily_avg_usd": round(total_cost / max(len(daily), 1), 4),
            "anomalous_runs": anomalous_runs[:20],
            "period_days": days,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_runs(
        self,
        project_id: str | None = None,
        days: int = 30,
    ) -> list[dict[str, Any]]:
        """Fetch execution runs for the given period."""
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            query = (
                self.supabase_client.table("archon_execution_runs")
                .select("id, task_id, project_id, status, stage, model, "
                        "retry_index, cost_usd, duration_seconds, "
                        "started_at, finished_at, metadata")
                .gte("started_at", cutoff)
                .order("started_at", desc=True)
                .limit(1000)
            )
            if project_id:
                query = query.eq("project_id", project_id)

            response = query.execute()
            return response.data or []
        except Exception as e:
            logger.error(f"Failed to fetch runs for analytics: {e}", exc_info=True)
            return []

    @staticmethod
    def _generate_cost_recommendations(
        breakdown: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        """Generate savings recommendations from cost breakdown."""
        recommendations: list[dict[str, str]] = []

        # Model cost tiers (approximate)
        model_tiers = {
            "opus": 3,
            "sonnet": 2,
            "haiku": 1,
        }

        for entry in breakdown:
            model = entry["model"].lower()
            stage = entry["stage"]
            success_rate = entry["success_rate"]
            avg_cost = entry["avg_cost_usd"]
            run_count = entry["run_count"]

            if run_count < 5:
                continue

            # Find model tier
            tier = 0
            for tier_name, tier_level in model_tiers.items():
                if tier_name in model:
                    tier = tier_level
                    break

            # Recommend downgrade if high success rate with expensive model
            if tier >= 2 and success_rate >= 0.9 and stage == "execute":
                task_types = entry.get("task_types", {})
                simple_types = {"docs", "learning", "documentation", "refactor"}
                simple_count = sum(task_types.get(t, 0) for t in simple_types)
                if simple_count > run_count * 0.5:
                    recommendations.append({
                        "model": entry["model"],
                        "stage": stage,
                        "type": "downgrade",
                        "message": f"Model '{entry['model']}' in {stage} stage has "
                                   f"{success_rate:.0%} success rate for mostly simple tasks. "
                                   f"Consider using haiku to save ~${avg_cost * 0.6:.4f}/run.",
                    })

        return recommendations
