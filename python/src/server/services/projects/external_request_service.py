"""Control-plane service for external ingress requests."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from src.server.utils import get_supabase_client

from ...config.logfire_config import get_logger
from ..engine.notifier import Notifier
from .approval_request_service import ApprovalRequestService

logger = get_logger(__name__)


class ExternalRequestService:
    """Stores and materializes external ingress requests."""

    VALID_STATUSES = {"received", "materialized", "failed", "cancelled"}
    VALID_MATERIALIZATIONS = {"none", "task", "approval"}

    def __init__(self, supabase_client=None, notifier: Notifier | None = None):
        self.supabase_client = supabase_client or get_supabase_client()
        self.notifier = notifier or Notifier(source_app="archon-leankit")

    async def create_request(
        self,
        *,
        source_channel: str,
        request_type: str,
        title: str,
        summary: str,
        materialize_as: str = "none",
        correlation_id: str | None = None,
        project_id: str | None = None,
        task_id: str | None = None,
        execution_run_id: str | None = None,
        bootstrap_plan_id: str | None = None,
        source_app: str | None = None,
        actor_id: str | None = None,
        actor_display: str | None = None,
        input_modality: str | None = None,
        input_text: str | None = None,
        transcript_confidence: float | None = None,
        audio_reference: str | None = None,
        payload: dict[str, Any] | None = None,
        task_template: dict[str, Any] | None = None,
        approval_template: dict[str, Any] | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        """Create an external request and optionally materialize it."""
        try:
            if not source_channel.strip():
                return False, {"error": "source_channel is required"}
            if not title.strip():
                return False, {"error": "title is required"}
            if materialize_as not in self.VALID_MATERIALIZATIONS:
                return False, {"error": f"Invalid materialize_as '{materialize_as}'"}

            if materialize_as == "task" and not project_id:
                return False, {"error": "project_id is required when materialize_as=task"}

            effective_correlation_id = correlation_id or str(uuid4())
            existing = self._find_by_correlation_id(effective_correlation_id)
            if existing is not None:
                return True, {
                    "request": {
                        **self._hydrate_request(existing),
                        "deduplicated": True,
                        "dedupe_strategy": "correlation-id",
                    },
                    "deduplicated": True,
                }

            now = datetime.now().isoformat()
            effective_payload = self._build_payload(
                payload=payload,
                input_modality=input_modality,
                input_text=input_text,
                transcript_confidence=transcript_confidence,
                audio_reference=audio_reference,
            )
            record: dict[str, Any] = {
                "source_channel": source_channel.strip(),
                "request_type": request_type,
                "status": "received",
                "materialize_as": materialize_as,
                "title": title.strip(),
                "summary": summary.strip(),
                "correlation_id": effective_correlation_id,
                "payload": effective_payload,
                "created_at": now,
                "updated_at": now,
            }
            optional_fields = {
                "project_id": project_id,
                "task_id": task_id,
                "execution_run_id": execution_run_id,
                "bootstrap_plan_id": bootstrap_plan_id,
                "source_app": source_app,
                "actor_id": actor_id,
                "actor_display": actor_display,
            }
            for key, value in optional_fields.items():
                if value:
                    record[key] = value

            response = self.supabase_client.table("archon_external_requests").insert(record).execute()
            if not response.data:
                return False, {"error": "Failed to create external request"}

            external_request = self._hydrate_request({
                **record,
                **response.data[0],
            })
            await self.notifier.on_external_request_created(external_request)

            update_payload: dict[str, Any] = {}
            if materialize_as == "task":
                ok, result = self._materialize_task(
                    external_request=external_request,
                    project_id=project_id,
                    source_app=source_app,
                    actor_display=actor_display,
                    task_template=task_template or {},
                )
                if not ok:
                    self._mark_failed(external_request["id"], result["error"])
                    return False, result
                external_request["linked_task_id"] = result["task"]["id"]
                update_payload.update({
                    "linked_task_id": result["task"]["id"],
                    "status": "materialized",
                })
                await self.notifier.on_external_request_materialized(external_request, "task")
            elif materialize_as == "approval":
                approval_service = ApprovalRequestService(self.supabase_client, notifier=self.notifier)
                ok, result = await approval_service.create_request(
                    title=(approval_template or {}).get("title") or title.strip(),
                    summary=(approval_template or {}).get("summary") or summary.strip(),
                    requested_by=actor_display or actor_id or "external-request",
                    requested_channel=source_channel.strip(),
                    project_id=project_id,
                    task_id=task_id,
                    execution_run_id=execution_run_id,
                    bootstrap_plan_id=bootstrap_plan_id,
                    external_request_id=external_request["id"],
                    actor_id=actor_id,
                    actor_display=actor_display,
                    context={
                        "correlation_id": external_request["correlation_id"],
                        "request_type": request_type,
                        "payload": effective_payload,
                        **((approval_template or {}).get("context") or {}),
                    },
                )
                if not ok:
                    self._mark_failed(external_request["id"], result["error"])
                    return False, result
                external_request["linked_approval_request_id"] = result["approval"]["id"]
                update_payload.update({
                    "linked_approval_request_id": result["approval"]["id"],
                    "status": "materialized",
                })
                await self.notifier.on_external_request_materialized(external_request, "approval")

            if update_payload:
                external_request = self._update_request(external_request["id"], update_payload) or external_request

            return True, {"request": external_request}
        except Exception as exc:
            logger.error(f"Error creating external request: {exc}", exc_info=True)
            return False, {"error": str(exc)}

    def get_request(self, request_id: str) -> tuple[bool, dict[str, Any]]:
        """Fetch one external request."""
        try:
            response = (
                self.supabase_client.table("archon_external_requests")
                .select("*")
                .eq("id", request_id)
                .execute()
            )
            if response.data:
                return True, {"request": self._hydrate_request(response.data[0])}
            return False, {"error": f"External request {request_id} not found"}
        except Exception as exc:
            logger.error(f"Error fetching external request {request_id}: {exc}", exc_info=True)
            return False, {"error": str(exc)}

    def list_requests(
        self,
        *,
        project_id: str | None = None,
        source_channel: str | None = None,
        request_type: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> tuple[bool, dict[str, Any]]:
        """List external requests with optional filtering."""
        try:
            query = self.supabase_client.table("archon_external_requests").select("*")
            filters_applied: list[str] = []

            if project_id:
                query = query.eq("project_id", project_id)
                filters_applied.append(f"project_id={project_id}")
            if source_channel:
                query = query.eq("source_channel", source_channel)
                filters_applied.append(f"source_channel={source_channel}")
            if request_type:
                normalized_request_type = request_type.strip().lower()
                query = query.eq("request_type", normalized_request_type)
                filters_applied.append(f"request_type={normalized_request_type}")
            if status:
                normalized_status = status.strip().lower()
                if normalized_status not in self.VALID_STATUSES:
                    return False, {"error": f"Invalid external request status '{status}'"}
                query = query.eq("status", normalized_status)
                filters_applied.append(f"status={normalized_status}")

            response = query.order("created_at", desc=True).limit(limit).execute()
            requests = [self._hydrate_request(item) for item in (response.data or []) if isinstance(item, dict)]
            return True, {
                "requests": requests,
                "total_count": len(requests),
                "filters_applied": ", ".join(filters_applied) if filters_applied else "none",
            }
        except Exception as exc:
            logger.error(f"Error listing external requests: {exc}", exc_info=True)
            return False, {"error": str(exc)}

    def _materialize_task(
        self,
        *,
        external_request: dict[str, Any],
        project_id: str,
        source_app: str | None,
        actor_display: str | None,
        task_template: dict[str, Any],
    ) -> tuple[bool, dict[str, Any]]:
        """Materialize an external request into a real task."""
        now = datetime.now().isoformat()
        tags = [
            "external-request",
            f"external-channel:{external_request['source_channel']}",
            f"external-request:{external_request['id']}",
        ]
        input_modality = self._extract_input_metadata(external_request).get("input_modality")
        if input_modality:
            tags.extend([
                f"input-modality:{input_modality}",
                f"{input_modality}-to-task",
            ])
        extra_tags = task_template.get("tags") or []
        tags = list(dict.fromkeys([*tags, *[str(tag) for tag in extra_tags]]))

        task_payload = {
            "project_id": project_id,
            "title": task_template.get("title") or external_request["title"],
            "description": task_template.get("description") or external_request["summary"],
            "status": task_template.get("status") or "approved",
            "assignee": task_template.get("assignee") or "Platform",
            "task_order": task_template.get("task_order") or 0,
            "priority": task_template.get("priority") or "medium",
            "task_type": task_template.get("task_type") or "feature",
            "complexity": task_template.get("complexity") or "simple",
            "max_retries": task_template.get("max_retries") or 1,
            "source_app": source_app or external_request.get("source_app"),
            "created_by": "external-channel",
            "created_from": external_request["source_channel"],
            "execution_prompt": task_template.get("execution_prompt"),
            "acceptance_criteria": task_template.get("acceptance_criteria") or [],
            "blocked_by": task_template.get("blocked_by") or [],
            "state_history": [],
            "state_changed_at": now,
            "created_at": now,
            "updated_at": now,
            "retry_count": 0,
            "tags": tags,
            "executed_by": {
                "source": "external-request",
                "external_request_id": external_request["id"],
                "actor_display": actor_display,
                "input_modality": input_modality,
            },
            "reviewed_by": [],
        }
        response = self.supabase_client.table("archon_tasks").insert(task_payload).execute()
        if not response.data:
            return False, {"error": f"Failed to materialize task for external request {external_request['id']}"}
        return True, {"task": response.data[0]}

    def materialize_architect_plan(
        self,
        *,
        external_request: dict[str, Any],
        architect_plan: dict[str, Any],
        source_app: str | None,
        actor_display: str | None,
    ) -> tuple[bool, dict[str, Any]]:
        """Materialize architect-generated task suggestions into real tasks."""
        project_id = external_request.get("project_id")
        if not isinstance(project_id, str) or not project_id.strip():
            return False, {"error": "project_id is required to materialize architect tasks"}

        suggestions = architect_plan.get("suggested_tasks") or []
        if not isinstance(suggestions, list):
            suggestions = []

        provider = (
            architect_plan.get("resolved_provider")
            or architect_plan.get("requested_provider")
            or "unknown"
        )
        created_tasks: list[dict[str, Any]] = []
        for index, suggestion in enumerate(suggestions):
            if not isinstance(suggestion, dict):
                continue
            ok, result = self._materialize_task(
                external_request=external_request,
                project_id=project_id.strip(),
                source_app=source_app,
                actor_display=actor_display,
                task_template={
                    "title": suggestion.get("title") or f"{external_request['title']} #{index + 1}",
                    "description": suggestion.get("summary") or external_request["summary"],
                    "task_type": suggestion.get("task_type") or "feature",
                    "priority": suggestion.get("priority") or "medium",
                    "complexity": suggestion.get("complexity") or "simple",
                    "tags": [
                        *(suggestion.get("tags") or []),
                        "architect-request",
                        f"architect-provider:{provider}",
                    ],
                },
            )
            if not ok:
                return False, result
            created_tasks.append(result["task"])

        return True, {"tasks": created_tasks}

    def _mark_failed(self, request_id: str, error: str) -> None:
        """Mark an external request as failed after materialization error."""
        self._update_request(request_id, {"status": "failed", "payload": {"materialization_error": error}})

    def merge_payload_fields(self, request_id: str, payload_updates: dict[str, Any]) -> dict[str, Any] | None:
        """Merge additional payload fields into an existing external request."""
        ok, result = self.get_request(request_id)
        if not ok:
            return None
        existing_request = result["request"]
        merged_payload = {
            **(existing_request.get("payload") or {}),
            **payload_updates,
        }
        return self._update_request(request_id, {"payload": merged_payload})

    def _find_by_correlation_id(self, correlation_id: str) -> dict[str, Any] | None:
        """Return an existing request when the same correlation id was already ingested."""
        response = (
            self.supabase_client.table("archon_external_requests")
            .select("*")
            .eq("correlation_id", correlation_id)
            .limit(1)
            .execute()
        )
        if response.data:
            candidate = response.data[0]
            if isinstance(candidate, dict) and candidate.get("id"):
                return self._hydrate_request(candidate)
        return None

    def _update_request(self, request_id: str, update_fields: dict[str, Any]) -> dict[str, Any] | None:
        """Persist external request updates."""
        payload = dict(update_fields)
        payload["updated_at"] = datetime.now().isoformat()
        response = (
            self.supabase_client.table("archon_external_requests")
            .update(payload)
            .eq("id", request_id)
            .execute()
        )
        if response.data:
            return self._hydrate_request(response.data[0])
        return None

    @staticmethod
    def _build_payload(
        *,
        payload: dict[str, Any] | None,
        input_modality: str | None,
        input_text: str | None,
        transcript_confidence: float | None,
        audio_reference: str | None,
    ) -> dict[str, Any]:
        """Merge normalized ingress metadata into the stored payload envelope."""
        merged = dict(payload or {})
        if input_modality:
            merged["input_modality"] = input_modality
        if input_text:
            merged["input_text"] = input_text
        if transcript_confidence is not None:
            merged["transcript_confidence"] = transcript_confidence
        if audio_reference:
            merged["audio_reference"] = audio_reference
        return merged

    @staticmethod
    def _extract_input_metadata(request: dict[str, Any]) -> dict[str, Any]:
        """Extract normalized input metadata from an external request payload."""
        payload = request.get("payload") if isinstance(request.get("payload"), dict) else {}
        return {
            "input_modality": payload.get("input_modality") if isinstance(payload.get("input_modality"), str) else None,
            "input_text": payload.get("input_text") if isinstance(payload.get("input_text"), str) else None,
            "transcript_confidence": payload.get("transcript_confidence")
            if isinstance(payload.get("transcript_confidence"), (float, int))
            else None,
            "audio_reference": payload.get("audio_reference") if isinstance(payload.get("audio_reference"), str) else None,
        }

    def _hydrate_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """Hydrate one external request with normalized ingress metadata."""
        metadata = self._extract_input_metadata(request)
        return {
            **request,
            **metadata,
        }
