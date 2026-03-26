"""OpenClaw channel adapter for inbound command ingestion."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any

from ...config.env_aliases import get_openclaw_ingest_secret, get_openclaw_replay_window_minutes
from .architect_request_service import ArchitectRequestService
from ..projects.approval_request_service import ApprovalRequestService
from ..projects.external_request_service import ExternalRequestService


class OpenClawChannelService:
    """Handle OpenClaw ingress by converting requests into external-request records."""

    def __init__(
        self,
        external_request_service: ExternalRequestService | None = None,
        approval_request_service: ApprovalRequestService | None = None,
        architect_request_service: ArchitectRequestService | None = None,
    ):
        self.external_request_service = external_request_service or ExternalRequestService()
        self.approval_request_service = approval_request_service or ApprovalRequestService(
            supabase_client=self.external_request_service.supabase_client,
            notifier=self.external_request_service.notifier,
        )
        self.architect_request_service = architect_request_service or ArchitectRequestService()
        self.ingest_secret = get_openclaw_ingest_secret()
        self.replay_window_minutes = get_openclaw_replay_window_minutes()

    async def handle_ingest(
        self,
        body: dict[str, Any],
        *,
        secret_token: str | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        """Process one OpenClaw ingress request."""
        if self.ingest_secret and secret_token != self.ingest_secret:
            return False, {"error": "Invalid OpenClaw ingest secret"}

        title = str(body.get("title") or "").strip()
        summary = str(body.get("summary") or "").strip()
        if not title:
            return False, {"error": "title is required"}
        if not summary:
            return False, {"error": "summary is required"}

        request_type = str(body.get("request_type") or "message")
        input_metadata = self._resolve_input_metadata(body)
        correlation_id = body.get("correlation_id")
        dedupe_strategy = "explicit-correlation-id"
        if not correlation_id:
            command_sequence_id = body.get("command_sequence_id")
            step_key = body.get("step_key")
            request_id = body.get("request_id") or body.get("message_id") or title[:24]
            if body.get("request_id") or body.get("message_id"):
                correlation_id = f"openclaw:{request_id}"
                dedupe_strategy = "request-id"
            elif isinstance(command_sequence_id, str) and command_sequence_id.strip() and isinstance(step_key, str) and step_key.strip():
                correlation_id = self._sequence_correlation_id(
                    command_sequence_id.strip(),
                    step_key.strip(),
                    request_type=request_type,
                    replay_window_minutes=self.replay_window_minutes,
                )
                dedupe_strategy = "sequence-step-window"
            else:
                correlation_id = self._semantic_correlation_id(
                    body,
                    replay_window_minutes=self.replay_window_minutes,
                )
                dedupe_strategy = "semantic-window-v3"

        payload = dict(body.get("payload") or {})
        sequence_policy, policy_error = self._resolve_sequence_policy(body)
        if policy_error:
            return False, {"error": policy_error}
        payload.update({
            "openclaw_request_type": request_type,
            "openclaw_session_id": body.get("session_id"),
            "openclaw_model": body.get("model"),
            "openclaw_dedupe_strategy": dedupe_strategy,
            "openclaw_replay_window_minutes": self.replay_window_minutes,
            "openclaw_command_sequence_id": body.get("command_sequence_id"),
            "openclaw_step_key": body.get("step_key"),
            "openclaw_sequence_snapshot": sequence_policy,
            **input_metadata,
        })

        ok, result = await self.external_request_service.create_request(
            source_channel="openclaw",
            request_type=request_type,
            title=title,
            summary=summary,
            materialize_as=body.get("materialize_as") or "none",
            correlation_id=correlation_id,
            project_id=body.get("project_id"),
            task_id=body.get("task_id"),
            execution_run_id=body.get("execution_run_id"),
            bootstrap_plan_id=body.get("bootstrap_plan_id"),
            source_app="openclaw",
            actor_id=body.get("actor_id"),
            actor_display=body.get("actor_display") or "openclaw",
            input_modality=input_metadata["input_modality"],
            input_text=input_metadata["input_text"],
            transcript_confidence=input_metadata["transcript_confidence"],
            audio_reference=input_metadata["audio_reference"],
            payload=payload,
            task_template=body.get("task_template"),
            approval_template=body.get("approval_template"),
        )
        if not ok:
            return False, result

        external_request = result["request"]
        if result.get("deduplicated"):
            return True, {
                "status": "deduplicated",
                "request": {
                    **external_request,
                    "deduplicated": True,
                    "dedupe_strategy": dedupe_strategy,
                },
            }
        if request_type == "approval-action":
            return await self._handle_approval_action(body, external_request)
        if request_type == "status-query":
            return self._handle_status_query(body, external_request)
        return True, {"status": "recorded", "request": external_request}

    def get_health_status(self) -> dict[str, Any]:
        """Return readiness snapshot for the OpenClaw ingress surface."""
        issues: list[str] = []
        if not self.ingest_secret:
            issues.append("ingest-secret-missing")
        status = "ready" if not issues else "degraded"
        return {
            "ingest_secret_configured": bool(self.ingest_secret),
            "ingest_ready": bool(self.ingest_secret),
            "status": status,
            "issues": issues,
            "replay_guard_strategy": "semantic-window-v3",
            "replay_window_minutes": self.replay_window_minutes,
            "sequence_guard_enabled": True,
            "conversational_policy_enabled": True,
            "last_checked_at": datetime.now(timezone.utc).isoformat(),
            "architect_route_enabled": True,
            "semantic_dedupe_enabled": True,
        }

    async def _handle_approval_action(
        self,
        body: dict[str, Any],
        external_request: dict[str, Any],
    ) -> tuple[bool, dict[str, Any]]:
        """Apply an approval decision requested through OpenClaw."""
        decision = str(body.get("decision") or "").strip().lower()
        approval_ids = body.get("approval_ids")
        actor_display = str(body.get("actor_display") or "openclaw")
        comment = f"OpenClaw approval action by {actor_display}"

        if isinstance(approval_ids, list) and approval_ids:
            ok, result = await self.approval_request_service.decide_requests(
                [str(item) for item in approval_ids],
                decision=decision,
                decided_by=actor_display,
                decision_comment=comment,
            )
            if not ok:
                return False, result
            return True, {"status": "processed", "request": external_request, "batch": result}

        approval_id = body.get("approval_id")
        if isinstance(approval_id, str) and approval_id.strip():
            ok, result = await self.approval_request_service.decide_request(
                approval_id.strip(),
                decision=decision,
                decided_by=actor_display,
                decision_comment=comment,
            )
            if not ok:
                return False, result
            return True, {"status": "processed", "request": external_request, "approval": result["approval"]}

        return False, {"error": "approval_id or approval_ids is required for approval-action"}

    async def handle_architect_request(
        self,
        body: dict[str, Any],
        *,
        secret_token: str | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        """Create an external architect request and resolve it through the architect-provider boundary."""
        ok, result = await self.handle_ingest(
            {
                **body,
                "request_type": "architect-request",
                "materialize_as": "none",
            },
            secret_token=secret_token,
        )
        if not ok:
            return False, result

        external_request = result["request"]
        merged_payload = dict(external_request.get("payload") or {})
        sequence_context = self._build_sequence_context(body)
        architect_plan = await self.architect_request_service.create_plan(
            title=str(body.get("title") or ""),
            summary=str(body.get("summary") or ""),
            project_id=body.get("project_id"),
            input_modality=external_request.get("input_modality") or merged_payload.get("input_modality"),
            input_text=external_request.get("input_text") or merged_payload.get("input_text"),
            clarification_answers=[str(item) for item in (body.get("clarification_answers") or []) if str(item).strip()],
            sequence_context=sequence_context,
            payload=dict(body.get("payload") or {}),
            requested_provider=body.get("architect_provider"),
            model=body.get("architect_model"),
        )

        merged_payload["openclaw_sequence_context"] = sequence_context
        if body.get("clarification_answers"):
            merged_payload["clarification_answers"] = [str(item) for item in (body.get("clarification_answers") or []) if str(item).strip()]
        if body.get("clarification_response_to_request_id"):
            merged_payload["clarification_response_to_request_id"] = body.get("clarification_response_to_request_id")
        merged_payload["architect_plan"] = architect_plan
        update_fields: dict[str, Any] = {
            "status": "materialized",
            "payload": merged_payload,
        }
        materialization_kind = "architect-plan"

        if body.get("project_id") and architect_plan.get("recommended_materialization") == "task":
            ok, materialized = self.external_request_service.materialize_architect_plan(
                external_request={
                    **external_request,
                    "payload": merged_payload,
                },
                architect_plan=architect_plan,
                source_app="openclaw",
                actor_display=body.get("actor_display") or "openclaw",
            )
            if not ok:
                return False, materialized
            created_tasks = materialized["tasks"]
            merged_payload["architect_materialization"] = {
                "created_task_ids": [task["id"] for task in created_tasks],
                "created_task_count": len(created_tasks),
            }
            update_fields["payload"] = merged_payload
            if created_tasks:
                update_fields["linked_task_id"] = created_tasks[0]["id"]
            materialization_kind = "architect-plan-task"

        updated_request = self.external_request_service._update_request(external_request["id"], update_fields) or external_request
        if body.get("clarification_response_to_request_id"):
            self._mark_clarification_resolved(
                responded_request_id=str(body.get("clarification_response_to_request_id")),
                resolution_request=updated_request,
                clarification_answers=[
                    str(item) for item in (body.get("clarification_answers") or []) if str(item).strip()
                ],
                step_key=str(body.get("step_key") or "clarification-answer"),
            )
        await self.external_request_service.notifier.on_external_request_materialized(updated_request, materialization_kind)

        return True, {
            "status": "planned",
            "request": updated_request,
            "architect_plan": architect_plan,
        }

    def _mark_clarification_resolved(
        self,
        *,
        responded_request_id: str,
        resolution_request: dict[str, Any],
        clarification_answers: list[str],
        step_key: str,
    ) -> None:
        """Record that a prior architect request was answered by a follow-up turn."""
        if not responded_request_id.strip():
            return
        self.external_request_service.merge_payload_fields(
            responded_request_id.strip(),
            {
                "clarification_status": "resolved",
                "clarification_resolved_by_request_id": resolution_request.get("id"),
                "clarification_resolved_at": datetime.now(timezone.utc).isoformat(),
                "clarification_resolution_step_key": step_key,
                "clarification_resolution_input_modality": resolution_request.get("input_modality"),
                "clarification_resolution_input_text": resolution_request.get("input_text"),
                "clarification_answers": clarification_answers,
            },
        )

    @staticmethod
    def _resolve_input_metadata(body: dict[str, Any]) -> dict[str, Any]:
        """Resolve normalized voice/text ingress metadata for OpenClaw requests."""
        payload = body.get("payload") if isinstance(body.get("payload"), dict) else {}
        input_modality = body.get("input_modality") if isinstance(body.get("input_modality"), str) else None
        if not input_modality:
            if payload.get("voice_transcript") or body.get("audio_reference") or body.get("transcript_confidence") is not None:
                input_modality = "voice"
            else:
                input_modality = "text"

        input_text = body.get("input_text") if isinstance(body.get("input_text"), str) else None
        if not input_text and isinstance(payload.get("voice_transcript"), str):
            input_text = payload.get("voice_transcript")
        if not input_text:
            input_text = str(body.get("summary") or "").strip() or None

        transcript_confidence = body.get("transcript_confidence")
        if not isinstance(transcript_confidence, (float, int)):
            transcript_confidence = payload.get("transcript_confidence")
        if not isinstance(transcript_confidence, (float, int)):
            transcript_confidence = None

        audio_reference = body.get("audio_reference") if isinstance(body.get("audio_reference"), str) else None
        if not audio_reference and isinstance(payload.get("audio_reference"), str):
            audio_reference = payload.get("audio_reference")

        return {
            "input_modality": input_modality,
            "input_text": input_text,
            "transcript_confidence": float(transcript_confidence) if transcript_confidence is not None else None,
            "audio_reference": audio_reference,
        }

    @staticmethod
    def _semantic_correlation_id(
        body: dict[str, Any],
        *,
        replay_window_minutes: int,
        now: datetime | None = None,
    ) -> str:
        """Create a stable correlation id within one replay window for semantic retries."""
        canonical = {
            "request_type": body.get("request_type") or "message",
            "title": OpenClawChannelService._normalize_dedupe_value(body.get("title")),
            "summary": OpenClawChannelService._normalize_dedupe_value(body.get("summary")),
            "input_modality": OpenClawChannelService._normalize_dedupe_value(body.get("input_modality")),
            "input_text": OpenClawChannelService._normalize_dedupe_value(body.get("input_text")),
            "project_id": OpenClawChannelService._normalize_dedupe_value(body.get("project_id")),
            "task_id": OpenClawChannelService._normalize_dedupe_value(body.get("task_id")),
            "execution_run_id": OpenClawChannelService._normalize_dedupe_value(body.get("execution_run_id")),
            "bootstrap_plan_id": OpenClawChannelService._normalize_dedupe_value(body.get("bootstrap_plan_id")),
            "approval_id": OpenClawChannelService._normalize_dedupe_value(body.get("approval_id")),
            "approval_ids": OpenClawChannelService._normalize_dedupe_value(body.get("approval_ids")),
            "decision": OpenClawChannelService._normalize_dedupe_value(body.get("decision")),
            "architect_provider": OpenClawChannelService._normalize_dedupe_value(body.get("architect_provider")),
            "payload": OpenClawChannelService._normalize_dedupe_value(body.get("payload") or {}),
        }
        fingerprint = hashlib.sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=True).encode("utf-8")).hexdigest()[:16]
        bucket = OpenClawChannelService._replay_window_bucket(replay_window_minutes, now=now)
        return f"openclaw-sem:{fingerprint}:{bucket}"

    @staticmethod
    def _sequence_correlation_id(
        command_sequence_id: str,
        step_key: str,
        *,
        request_type: str,
        replay_window_minutes: int,
        now: datetime | None = None,
    ) -> str:
        """Create a replay-window-scoped correlation id for one step in a multi-step flow."""
        canonical = {
            "request_type": request_type,
            "command_sequence_id": OpenClawChannelService._normalize_dedupe_value(command_sequence_id),
            "step_key": OpenClawChannelService._normalize_dedupe_value(step_key),
        }
        fingerprint = hashlib.sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=True).encode("utf-8")).hexdigest()[:16]
        bucket = OpenClawChannelService._replay_window_bucket(replay_window_minutes, now=now)
        return f"openclaw-seq:{fingerprint}:{bucket}"

    @staticmethod
    def _replay_window_bucket(replay_window_minutes: int, *, now: datetime | None = None) -> int:
        """Return the integer replay bucket for the provided time window."""
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        seconds = replay_window_minutes * 60
        return math.floor(current.timestamp() / seconds)

    @staticmethod
    def _normalize_dedupe_value(value: Any) -> Any:
        """Normalize values used for semantic replay protection."""
        volatile_keys = {
            "attempt",
            "audio_chunk_id",
            "client_timestamp",
            "correlation_id",
            "created_at",
            "message_id",
            "nonce",
            "received_at",
            "request_id",
            "retry_count",
            "timestamp",
            "transcript_id",
            "updated_at",
        }
        if isinstance(value, str):
            return " ".join(value.strip().lower().split())
        if isinstance(value, dict):
            normalized: dict[str, Any] = {}
            for key, item in sorted(value.items()):
                if key in volatile_keys:
                    continue
                normalized[key] = OpenClawChannelService._normalize_dedupe_value(item)
            return normalized
        if isinstance(value, list):
            return [OpenClawChannelService._normalize_dedupe_value(item) for item in value]
        return value

    def _resolve_sequence_policy(self, body: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
        """Resolve conversational policy metadata for one OpenClaw command sequence."""
        command_sequence_id = body.get("command_sequence_id")
        step_key = body.get("step_key")
        request_type = str(body.get("request_type") or "message")

        if not isinstance(command_sequence_id, str) or not command_sequence_id.strip():
            return {
                "sequence_id": None,
                "step_key": step_key if isinstance(step_key, str) else None,
                "history_count": 0,
                "sequence_closed": False,
                "next_recommended_request_types": self._recommended_next_request_types(request_type),
            }, None

        history = self._load_sequence_history(command_sequence_id.strip())
        previous_step_keys = [
            str(item.get("step_key"))
            for item in history
            if isinstance(item.get("step_key"), str) and item.get("step_key")
        ]
        previous_request_types = [
            str(item.get("request_type"))
            for item in history
            if isinstance(item.get("request_type"), str) and item.get("request_type")
        ]
        sequence_closed = any(item.get("request_type") == "approval-action" for item in history)

        if isinstance(step_key, str) and step_key.strip():
            conflicting = next(
                (
                    item for item in history
                    if item.get("step_key") == step_key.strip()
                    and item.get("request_type") != request_type
                ),
                None,
            )
            if conflicting is not None:
                return {}, (
                    f"OpenClaw step '{step_key.strip()}' was already used for "
                    f"request_type '{conflicting.get('request_type')}'"
                )

        if sequence_closed and request_type not in {"status-query"}:
            return {}, "OpenClaw command sequence is already closed by a prior approval-action"

        snapshot = {
            "sequence_id": command_sequence_id.strip(),
            "step_key": step_key.strip() if isinstance(step_key, str) and step_key.strip() else None,
            "history_count": len(history),
            "previous_request_types": previous_request_types,
            "previous_step_keys": previous_step_keys,
            "sequence_closed": sequence_closed,
            "next_recommended_request_types": self._recommended_next_request_types(request_type),
        }
        return snapshot, None

    def _load_sequence_history(self, command_sequence_id: str) -> list[dict[str, Any]]:
        """Load recent external requests for one OpenClaw command sequence."""
        try:
            response = (
                self.external_request_service.supabase_client.table("archon_external_requests")
                .select("*")
                .eq("source_channel", "openclaw")
                .order("created_at", desc=False)
                .limit(100)
                .execute()
            )
        except Exception:
            return []

        history: list[dict[str, Any]] = []
        for item in response.data or []:
            if not isinstance(item, dict):
                continue
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            if payload.get("openclaw_command_sequence_id") == command_sequence_id:
                history.append({
                    "id": item.get("id"),
                    "request_type": item.get("request_type"),
                    "title": item.get("title"),
                    "summary": item.get("summary"),
                    "step_key": payload.get("openclaw_step_key"),
                    "input_modality": payload.get("input_modality"),
                    "architect_plan": payload.get("architect_plan") if isinstance(payload.get("architect_plan"), dict) else None,
                    "clarification_answers": payload.get("clarification_answers") if isinstance(payload.get("clarification_answers"), list) else [],
                    "status": item.get("status"),
                    "created_at": item.get("created_at"),
                })
        return history

    def _build_sequence_context(self, body: dict[str, Any]) -> dict[str, Any]:
        """Build a compact multi-turn context snapshot for architect planning."""
        command_sequence_id = body.get("command_sequence_id")
        if not isinstance(command_sequence_id, str) or not command_sequence_id.strip():
            return {
                "sequence_id": None,
                "history_count": 0,
                "recent_requests": [],
            }

        history = self._load_sequence_history(command_sequence_id.strip())
        recent_requests = history[-5:]
        pending_request = next(
            (
                item for item in reversed(history)
                if isinstance(item.get("architect_plan"), dict)
                and isinstance(item["architect_plan"].get("clarifying_questions"), list)
                and item["architect_plan"].get("clarifying_questions")
            ),
            None,
        )
        pending_questions = (
            [str(item) for item in pending_request["architect_plan"].get("clarifying_questions") if str(item).strip()]
            if isinstance(pending_request, dict)
            else []
        )
        return {
            "sequence_id": command_sequence_id.strip(),
            "history_count": len(history),
            "recent_requests": recent_requests,
            "pending_clarifying_questions": pending_questions,
            "pending_clarifying_request_id": pending_request.get("id") if isinstance(pending_request, dict) else None,
        }

    @staticmethod
    def _recommended_next_request_types(request_type: str) -> list[str]:
        """Return simple next-step hints for conversational OpenClaw flows."""
        transitions = {
            "message": ["status-query", "architect-request", "task-request"],
            "status-query": ["architect-request", "approval-action"],
            "architect-request": ["task-request", "approval-action", "status-query"],
            "task-request": ["status-query", "approval-action"],
            "approval-action": ["status-query"],
        }
        return transitions.get(request_type, ["status-query"])

    def _handle_status_query(
        self,
        body: dict[str, Any],
        external_request: dict[str, Any],
    ) -> tuple[bool, dict[str, Any]]:
        """Return lightweight status snapshots for operator-facing OpenClaw queries."""
        snapshots: dict[str, Any] = {}
        task_id = body.get("task_id")
        if isinstance(task_id, str) and task_id.strip():
            task_response = (
                self.external_request_service.supabase_client.table("archon_tasks")
                .select("id,title,status,project_id,assignee,updated_at")
                .eq("id", task_id.strip())
                .limit(1)
                .execute()
            )
            if task_response.data:
                snapshots["task"] = task_response.data[0]

        execution_run_id = body.get("execution_run_id")
        if isinstance(execution_run_id, str) and execution_run_id.strip():
            run_response = (
                self.external_request_service.supabase_client.table("archon_execution_runs")
                .select("id,task_id,status,stage,project_id,session_id,updated_at")
                .eq("id", execution_run_id.strip())
                .limit(1)
                .execute()
            )
            if run_response.data:
                snapshots["execution_run"] = run_response.data[0]

        approval_id = body.get("approval_id")
        if isinstance(approval_id, str) and approval_id.strip():
            ok, result = self.approval_request_service.get_request(approval_id.strip())
            if ok:
                snapshots["approval"] = result["approval"]

        project_id = body.get("project_id")
        if isinstance(project_id, str) and project_id.strip():
            pending_approvals = self.approval_request_service.list_requests(
                project_id=project_id.strip(),
                status="pending",
                limit=5,
            )
            if pending_approvals[0]:
                snapshots["pending_approvals"] = pending_approvals[1]["approvals"]

        return True, {"status": "recorded", "request": external_request, "snapshots": snapshots}
