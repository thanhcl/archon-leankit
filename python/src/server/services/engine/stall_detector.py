"""
Stall Detector for LeanKit V3 Task Engine.

Detects infinite retry loops by comparing consecutive execution run
outcome fingerprints. When N identical consecutive outcomes are found,
recommends escalation or pause.

Adopted from GSD's stall detection pattern:
- Detects identical planner/executor outputs across iterations
- Escalates strategy before hitting retry cap
- Hard-stop gates force mandatory human notification

Usage:
    detector = StallDetector(supabase_client=client)
    result = await detector.check(task_id="task-123", threshold=3)
    if result.stalled:
        # Handle stall: escalate model or pause task
"""

import hashlib
from dataclasses import dataclass
from typing import Any

from ...config.logfire_config import get_logger
from ...utils import get_supabase_client

logger = get_logger(__name__)

DEFAULT_STALL_THRESHOLD = 3
EXECUTION_RUNS_TABLE = "archon_execution_runs"


@dataclass
class StallResult:
    """Result of a stall detection check."""

    stalled: bool
    consecutive_count: int
    outcome_hash: str | None
    recommendation: str  # "none" | "escalate_model" | "pause_and_notify" | "force_hold"
    models_tried: list[str]
    last_error_preview: str | None = None

    @property
    def should_escalate(self) -> bool:
        return self.recommendation == "escalate_model"

    @property
    def should_pause(self) -> bool:
        return self.recommendation in ("pause_and_notify", "force_hold")


def compute_outcome_hash(
    error_summary: str | None,
    result_summary: str | None,
    stage: str | None,
) -> str:
    """Compute a fingerprint for an execution run outcome.

    Uses error_summary + result_summary + stage to detect identical outcomes.
    This catches repeated failures with the same error message.
    """
    parts = [
        (error_summary or "").strip()[:500],
        (result_summary or "").strip()[:500],
        (stage or "").strip(),
    ]
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class StallDetector:
    """Detects stalled tasks by comparing consecutive execution run outcomes."""

    def __init__(self, supabase_client=None):
        self._client = supabase_client or get_supabase_client()

    async def check(
        self,
        task_id: str,
        threshold: int = DEFAULT_STALL_THRESHOLD,
    ) -> StallResult:
        """Check if a task is stalled by comparing recent execution run outcomes.

        Args:
            task_id: The task to check.
            threshold: Number of consecutive identical outcomes before stall
                is declared. Default: 3.

        Returns:
            StallResult with stall status and recommendation.
        """
        try:
            # Fetch recent execution runs for this task, ordered by most recent first
            resp = (
                self._client.table(EXECUTION_RUNS_TABLE)
                .select("id, status, stage, model, result_summary, error_summary, metadata, started_at")
                .eq("task_id", task_id)
                .in_("status", ["completed", "failed"])
                .order("started_at", desc=True)
                .limit(threshold + 2)  # Fetch extra for context
                .execute()
            )
            runs = resp.data or []

            if len(runs) < threshold:
                return StallResult(
                    stalled=False,
                    consecutive_count=len(runs),
                    outcome_hash=None,
                    recommendation="none",
                    models_tried=[r.get("model", "unknown") for r in runs],
                )

            # Compute outcome hashes for each run
            hashes = []
            for run in runs:
                h = compute_outcome_hash(
                    error_summary=run.get("error_summary"),
                    result_summary=run.get("result_summary"),
                    stage=run.get("stage"),
                )
                hashes.append(h)

            # Count consecutive identical hashes from the most recent
            first_hash = hashes[0]
            consecutive = 1
            for h in hashes[1:]:
                if h == first_hash:
                    consecutive += 1
                else:
                    break

            models_tried = [r.get("model", "unknown") for r in runs[:consecutive]]
            last_error = runs[0].get("error_summary") if runs else None

            if consecutive < threshold:
                return StallResult(
                    stalled=False,
                    consecutive_count=consecutive,
                    outcome_hash=first_hash,
                    recommendation="none",
                    models_tried=models_tried,
                    last_error_preview=last_error[:200] if last_error else None,
                )

            # Stall detected -- determine recommendation based on severity
            unique_models = set(models_tried)
            all_models_tried = len(unique_models) >= 3  # haiku, sonnet, opus

            if consecutive >= threshold + 2 or all_models_tried:
                recommendation = "force_hold"
            elif consecutive >= threshold + 1:
                recommendation = "pause_and_notify"
            else:
                recommendation = "escalate_model"

            logger.warning(
                f"Stall detected | task_id={task_id} | "
                f"consecutive={consecutive} | hash={first_hash} | "
                f"models={unique_models} | recommendation={recommendation}"
            )

            return StallResult(
                stalled=True,
                consecutive_count=consecutive,
                outcome_hash=first_hash,
                recommendation=recommendation,
                models_tried=models_tried,
                last_error_preview=last_error[:200] if last_error else None,
            )

        except Exception as e:
            logger.error(f"Stall detection failed (non-fatal): {e}", exc_info=True)
            # On error, assume not stalled to avoid blocking execution
            return StallResult(
                stalled=False,
                consecutive_count=0,
                outcome_hash=None,
                recommendation="none",
                models_tried=[],
            )
