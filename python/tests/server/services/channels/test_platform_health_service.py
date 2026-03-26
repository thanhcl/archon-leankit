"""Tests for aggregated pilot-oriented service health."""

from unittest.mock import AsyncMock, MagicMock, patch

from src.server.services.channels.platform_health_service import PlatformHealthService


def test_get_service_health_reports_ready_when_dependencies_are_reachable():
    channels = MagicMock()
    channels.get_heartbeat.return_value = {
        "status": "ready",
        "issues": [],
        "last_checked_at": "2026-03-21T10:02:00+00:00",
        "telegram": {"status": "ready"},
        "openclaw": {"status": "ready"},
    }
    service = PlatformHealthService(channel_health_service=channels)

    async def _run():
        with patch.object(service, "_probe_endpoint", new=AsyncMock()) as probe:
            probe.side_effect = [
                {
                    "key": "archon_mcp",
                    "label": "Archon MCP",
                    "status": "ready",
                    "configured": True,
                    "reachable": True,
                    "url": "http://localhost:8051/health",
                    "http_status": 200,
                    "latency_ms": 12.5,
                    "issues": [],
                    "last_checked_at": "2026-03-21T10:00:00+00:00",
                },
                {
                    "key": "observability_replay",
                    "label": "Observability Replay",
                    "status": "ready",
                    "configured": True,
                    "reachable": True,
                    "url": "http://localhost:4000/api/unified-events/recent?limit=1",
                    "http_status": 200,
                    "latency_ms": 8.3,
                    "issues": [],
                    "last_checked_at": "2026-03-21T10:01:00+00:00",
                },
            ]
            return await service.get_service_health()

    result = __import__("asyncio").run(_run())

    assert result["status"] == "ready"
    assert result["ready_services"] == 4
    assert result["degraded_services"] == 0
    assert result["ready_service_keys"] == [
        "control_plane",
        "archon_mcp",
        "observability_replay",
        "external_channels",
    ]


def test_get_service_health_prefixes_nested_issues_when_degraded():
    channels = MagicMock()
    channels.get_heartbeat.return_value = {
        "status": "degraded",
        "issues": ["telegram:webhook-secret-missing"],
        "last_checked_at": "2026-03-21T10:02:00+00:00",
        "telegram": {"status": "degraded"},
        "openclaw": {"status": "ready"},
    }
    service = PlatformHealthService(channel_health_service=channels)

    async def _run():
        with patch.object(service, "_probe_endpoint", new=AsyncMock()) as probe:
            probe.side_effect = [
                {
                    "key": "archon_mcp",
                    "label": "Archon MCP",
                    "status": "degraded",
                    "configured": True,
                    "reachable": False,
                    "url": "http://localhost:8051/health",
                    "http_status": None,
                    "latency_ms": None,
                    "issues": ["connection-refused"],
                    "last_checked_at": "2026-03-21T10:00:00+00:00",
                },
                {
                    "key": "observability_replay",
                    "label": "Observability Replay",
                    "status": "ready",
                    "configured": True,
                    "reachable": True,
                    "url": "http://localhost:4000/api/unified-events/recent?limit=1",
                    "http_status": 200,
                    "latency_ms": 8.3,
                    "issues": [],
                    "last_checked_at": "2026-03-21T10:01:00+00:00",
                },
            ]
            return await service.get_service_health()

    result = __import__("asyncio").run(_run())

    assert result["status"] == "degraded"
    assert "archon_mcp:connection-refused" in result["issues"]
    assert "external_channels:telegram:webhook-secret-missing" in result["issues"]
    assert "archon_mcp" in result["degraded_service_keys"]
    assert "external_channels" in result["degraded_service_keys"]
