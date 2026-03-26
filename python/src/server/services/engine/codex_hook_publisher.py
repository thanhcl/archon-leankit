"""Best-effort publisher for Codex native hook events."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from ...config.env_aliases import get_observability_ingest_url
from ...config.logfire_config import get_logger

logger = get_logger(__name__)


class CodexHookPublisher:
    """Publishes Codex-native hook events to Observability."""

    def __init__(self, endpoint_url: str | None = None):
        self.endpoint_url = endpoint_url or self._resolve_endpoint()

    @staticmethod
    def _resolve_endpoint() -> str:
        ingest_url = get_observability_ingest_url()
        parsed = urllib.parse.urlparse(ingest_url)
        if parsed.path.endswith("/api/events"):
            new_path = parsed.path[: -len("/api/events")] + "/api/codex/events"
        else:
            new_path = "/api/codex/events"
        return urllib.parse.urlunparse(parsed._replace(path=new_path, query="", fragment=""))

    def publish(
        self,
        *,
        source_app: str,
        run_id: str,
        event: str,
        data: dict[str, Any],
        task_id: str | None = None,
        execution_run_id: str | None = None,
        agent_id: str | None = None,
        model: str | None = None,
    ) -> None:
        """Send a normalized Codex hook event without blocking engine flow."""
        try:
            payload = {
                "sourceApp": source_app,
                "runId": run_id,
                "event": event,
                "taskId": task_id,
                "executionRunId": execution_run_id,
                "agentId": agent_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "model": model,
                "data": data,
            }
            req = urllib.request.Request(
                self.endpoint_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=2)
        except Exception as exc:
            logger.debug(f"Codex hook publish failed (non-fatal): {exc}")
