"""Aggregated pilot-oriented service health across platform dependencies."""

from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter
from typing import Any

import httpx

from ...config.env_aliases import (
    get_control_plane_mcp_url,
    get_control_plane_url,
    get_observability_replay_url,
)
from .channel_health_service import ExternalChannelHealthService


class PlatformHealthService:
    """Build one aggregate readiness snapshot for pilot-critical services."""

    def __init__(self, channel_health_service: ExternalChannelHealthService | None = None):
        self.channel_health_service = channel_health_service or ExternalChannelHealthService()

    async def get_service_health(self) -> dict[str, Any]:
        """Return aggregated service readiness for control plane, MCP, replay, and channels."""
        control_plane = self._build_self_health()
        archon_mcp = await self._probe_endpoint(
            key="archon_mcp",
            label="Archon MCP",
            url=f"{get_control_plane_mcp_url().rstrip('/')}/health",
            required_keys=("status",),
        )
        observability_replay = await self._probe_endpoint(
            key="observability_replay",
            label="Observability Replay",
            url=f"{get_observability_replay_url()}?limit=1",
            required_keys=("events",),
        )
        external_channels = self.channel_health_service.get_heartbeat()

        services = {
            "control_plane": control_plane,
            "archon_mcp": archon_mcp,
            "observability_replay": observability_replay,
        }
        ready_service_keys = [key for key, item in services.items() if item.get("status") == "ready"]
        degraded_service_keys = [key for key, item in services.items() if item.get("status") != "ready"]
        issues: list[str] = []
        timestamps: list[datetime] = []
        for key, item in services.items():
            issues.extend(f"{key}:{issue}" for issue in item.get("issues", []))
            parsed = self._parse_timestamp(item.get("last_checked_at"))
            if parsed is not None:
                timestamps.append(parsed)
        issues.extend(f"external_channels:{issue}" for issue in external_channels.get("issues", []))
        parsed_channels = self._parse_timestamp(external_channels.get("last_checked_at"))
        if parsed_channels is not None:
            timestamps.append(parsed_channels)

        if external_channels.get("status") == "ready":
            ready_service_keys.append("external_channels")
        else:
            degraded_service_keys.append("external_channels")

        return {
            "status": "ready" if len(degraded_service_keys) == 0 else "degraded",
            "total_services": 4,
            "ready_services": len(ready_service_keys),
            "degraded_services": len(degraded_service_keys),
            "ready_service_keys": ready_service_keys,
            "degraded_service_keys": degraded_service_keys,
            "issues": issues,
            "last_checked_at": max(timestamps).isoformat() if timestamps else datetime.now(timezone.utc).isoformat(),
            "control_plane": control_plane,
            "archon_mcp": archon_mcp,
            "observability_replay": observability_replay,
            "external_channels": external_channels,
        }

    def _build_self_health(self) -> dict[str, Any]:
        checked_at = datetime.now(timezone.utc).isoformat()
        return {
            "key": "control_plane",
            "label": "Archon Control Plane",
            "status": "ready",
            "configured": True,
            "reachable": True,
            "url": get_control_plane_url(),
            "http_status": 200,
            "latency_ms": 0.0,
            "issues": [],
            "last_checked_at": checked_at,
        }

    async def _probe_endpoint(
        self,
        *,
        key: str,
        label: str,
        url: str,
        required_keys: tuple[str, ...],
    ) -> dict[str, Any]:
        started = perf_counter()
        checked_at = datetime.now(timezone.utc).isoformat()
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                response = await client.get(url)
            latency_ms = round((perf_counter() - started) * 1000, 2)
            payload = response.json() if response.content else {}
            missing_keys = [required for required in required_keys if required not in payload]
            issues = []
            if response.status_code >= 400:
                issues.append(f"http-{response.status_code}")
            if missing_keys:
                issues.append(f"missing-keys:{','.join(missing_keys)}")
            return {
                "key": key,
                "label": label,
                "status": "ready" if not issues else "degraded",
                "configured": True,
                "reachable": response.status_code < 400,
                "url": url,
                "http_status": response.status_code,
                "latency_ms": latency_ms,
                "issues": issues,
                "last_checked_at": checked_at,
            }
        except Exception as exc:
            return {
                "key": key,
                "label": label,
                "status": "degraded",
                "configured": True,
                "reachable": False,
                "url": url,
                "http_status": None,
                "latency_ms": None,
                "issues": [str(exc)],
                "last_checked_at": checked_at,
            }

    @staticmethod
    def _parse_timestamp(value: object) -> datetime | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
