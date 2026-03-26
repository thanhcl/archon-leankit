"""Control-plane service for approval workflows."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.server.utils import get_supabase_client

from ...config.logfire_config import get_logger
from ..engine.notifier import Notifier

logger = get_logger(__name__)


class ApprovalRequestService:
    """Stores approval requests and decision audit trail."""

    VALID_STATUSES = {"pending", "approved", "rejected", "cancelled"}

    def __init__(self, supabase_client=None, notifier: Notifier | None = None):
        self.supabase_client = supabase_client or get_supabase_client()
        self.notifier = notifier or Notifier(source_app="archon-leankit")

    async def create_request(
        self,
        *,
        title: str,
        summary: str,
        requested_by: str,
        requested_channel: str,
        project_id: str | None = None,
        task_id: str | None = None,
        execution_run_id: str | None = None,
        bootstrap_plan_id: str | None = None,
        external_request_id: str | None = None,
        actor_id: str | None = None,
        actor_display: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        """Create a pending approval request."""
        try:
            if not title.strip():
                return False, {"error": "Title is required"}
            if not requested_by.strip():
                return False, {"error": "requested_by is required"}
            if not requested_channel.strip():
                return False, {"error": "requested_channel is required"}

            now = datetime.now().isoformat()
            payload: dict[str, Any] = {
                "title": title.strip(),
                "summary": summary.strip(),
                "status": "pending",
                "requested_by": requested_by.strip(),
                "requested_channel": requested_channel.strip(),
                "context": context or {},
                "created_at": now,
                "updated_at": now,
            }
            optional_fields = {
                "project_id": project_id,
                "task_id": task_id,
                "execution_run_id": execution_run_id,
                "bootstrap_plan_id": bootstrap_plan_id,
                "external_request_id": external_request_id,
                "actor_id": actor_id,
                "actor_display": actor_display,
            }
            for key, value in optional_fields.items():
                if value:
                    payload[key] = value

            response = self.supabase_client.table("archon_approval_requests").insert(payload).execute()
            if not response.data:
                return False, {"error": "Failed to create approval request"}

            approval = response.data[0]
            await self.notifier.on_approval_requested(approval)
            return True, {"approval": approval}
        except Exception as exc:
            logger.error(f"Error creating approval request: {exc}", exc_info=True)
            return False, {"error": str(exc)}

    def get_request(self, approval_id: str) -> tuple[bool, dict[str, Any]]:
        """Fetch one approval request."""
        try:
            response = (
                self.supabase_client.table("archon_approval_requests")
                .select("*")
                .eq("id", approval_id)
                .execute()
            )
            if response.data:
                return True, {"approval": response.data[0]}
            return False, {"error": f"Approval request {approval_id} not found"}
        except Exception as exc:
            logger.error(f"Error fetching approval request {approval_id}: {exc}", exc_info=True)
            return False, {"error": str(exc)}

    def list_requests(
        self,
        *,
        project_id: str | None = None,
        task_id: str | None = None,
        status: str | None = None,
        external_request_id: str | None = None,
        limit: int = 50,
    ) -> tuple[bool, dict[str, Any]]:
        """List approval requests with optional filtering."""
        try:
            query = self.supabase_client.table("archon_approval_requests").select("*")
            filters_applied: list[str] = []

            if project_id:
                query = query.eq("project_id", project_id)
                filters_applied.append(f"project_id={project_id}")
            if task_id:
                query = query.eq("task_id", task_id)
                filters_applied.append(f"task_id={task_id}")
            if status:
                normalized_status = status.strip().lower()
                if normalized_status not in self.VALID_STATUSES:
                    return False, {"error": f"Invalid approval status '{status}'"}
                query = query.eq("status", normalized_status)
                filters_applied.append(f"status={normalized_status}")
            if external_request_id:
                query = query.eq("external_request_id", external_request_id)
                filters_applied.append(f"external_request_id={external_request_id}")

            response = query.order("created_at", desc=True).limit(limit).execute()
            approvals = response.data or []
            return True, {
                "approvals": approvals,
                "total_count": len(approvals),
                "filters_applied": ", ".join(filters_applied) if filters_applied else "none",
            }
        except Exception as exc:
            logger.error(f"Error listing approval requests: {exc}", exc_info=True)
            return False, {"error": str(exc)}

    async def decide_request(
        self,
        approval_id: str,
        *,
        decision: str,
        decided_by: str,
        decision_comment: str | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        """Record an approval decision."""
        try:
            ok, result = self.get_request(approval_id)
            if not ok:
                return False, result

            current = result["approval"]
            if current.get("status") != "pending":
                return False, {"error": f"Approval request {approval_id} is already {current.get('status')}"}

            normalized = decision.strip().lower()
            if normalized not in {"approve", "reject"}:
                return False, {"error": f"Invalid decision '{decision}'"}

            final_status = "approved" if normalized == "approve" else "rejected"
            payload = {
                "status": final_status,
                "decided_by": decided_by,
                "decision_comment": decision_comment,
                "decided_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
            }
            response = (
                self.supabase_client.table("archon_approval_requests")
                .update(payload)
                .eq("id", approval_id)
                .execute()
            )
            if not response.data:
                return False, {"error": f"Failed to update approval request {approval_id}"}

            approval = response.data[0]
            await self.notifier.on_approval_decided(approval, normalized)
            return True, {"approval": approval}
        except Exception as exc:
            logger.error(f"Error deciding approval request {approval_id}: {exc}", exc_info=True)
            return False, {"error": str(exc)}

    async def decide_requests(
        self,
        approval_ids: list[str],
        *,
        decision: str,
        decided_by: str,
        decision_comment: str | None = None,
        bundle_label: str | None = None,
        minimum_required: int | None = None,
        stop_on_error: bool = False,
    ) -> tuple[bool, dict[str, Any]]:
        """Apply one approval decision across multiple pending approval requests."""
        approvals: list[dict[str, Any]] = []
        failed_ids: list[str] = []
        seen: set[str] = set()
        selected_ids: list[str] = []

        for approval_id in approval_ids:
            normalized_id = approval_id.strip()
            if not normalized_id or normalized_id in seen:
                continue
            seen.add(normalized_id)
            selected_ids.append(normalized_id)

        if minimum_required is not None and len(selected_ids) < minimum_required:
            return False, {
                "error": f"Selected approvals ({len(selected_ids)}) do not meet the minimum_required threshold ({minimum_required})",
                "approvals": approvals,
                "failed_ids": failed_ids,
                "processed_count": 0,
                "failed_count": 0,
                "bundle_label": bundle_label,
                "minimum_required": minimum_required,
                "threshold_met": False,
            }

        for normalized_id in selected_ids:
            ok, result = await self.decide_request(
                normalized_id,
                decision=decision,
                decided_by=decided_by,
                decision_comment=decision_comment,
            )
            if ok:
                approvals.append(result["approval"])
                continue

            failed_ids.append(normalized_id)
            if stop_on_error:
                return False, {
                    "error": result.get("error", "Batch decision failed"),
                    "approvals": approvals,
                    "failed_ids": failed_ids,
                    "processed_count": len(approvals),
                    "failed_count": len(failed_ids),
                    "bundle_label": bundle_label,
                    "minimum_required": minimum_required,
                    "threshold_met": minimum_required is None or len(approvals) >= minimum_required,
                }

        threshold_met = minimum_required is None or len(approvals) >= minimum_required
        if not threshold_met:
            return False, {
                "error": f"Processed approvals ({len(approvals)}) did not meet the minimum_required threshold ({minimum_required})",
                "approvals": approvals,
                "failed_ids": failed_ids,
                "processed_count": len(approvals),
                "failed_count": len(failed_ids),
                "bundle_label": bundle_label,
                "minimum_required": minimum_required,
                "threshold_met": False,
            }

        return True, {
            "approvals": approvals,
            "failed_ids": failed_ids,
            "processed_count": len(approvals),
            "failed_count": len(failed_ids),
            "bundle_label": bundle_label,
            "minimum_required": minimum_required,
            "threshold_met": threshold_met,
        }
