"""Tests for aggregated external-channel heartbeat service."""

from unittest.mock import MagicMock

from src.server.services.channels.channel_health_service import ExternalChannelHealthService


def test_get_heartbeat_returns_ready_when_all_channels_ready():
    telegram = MagicMock()
    telegram.get_health_status.return_value = {
        "status": "ready",
        "issues": [],
        "last_checked_at": "2026-03-21T10:00:00+00:00",
    }
    openclaw = MagicMock()
    openclaw.get_health_status.return_value = {
        "status": "ready",
        "issues": [],
        "last_checked_at": "2026-03-21T10:01:00+00:00",
    }

    service = ExternalChannelHealthService(telegram_service=telegram, openclaw_service=openclaw)
    result = service.get_heartbeat()

    assert result["status"] == "ready"
    assert result["ready_channels"] == 2
    assert result["degraded_channels"] == 0
    assert result["ready_channel_keys"] == ["telegram", "openclaw"]
    assert result["degraded_channel_keys"] == []
    assert result["last_checked_at"] == "2026-03-21T10:01:00+00:00"


def test_get_heartbeat_prefixes_channel_issues_when_degraded():
    telegram = MagicMock()
    telegram.get_health_status.return_value = {
        "status": "degraded",
        "issues": ["bot-token-missing"],
        "last_checked_at": "2026-03-21T10:00:00+00:00",
    }
    openclaw = MagicMock()
    openclaw.get_health_status.return_value = {
        "status": "ready",
        "issues": [],
        "last_checked_at": "2026-03-21T10:01:00+00:00",
    }

    service = ExternalChannelHealthService(telegram_service=telegram, openclaw_service=openclaw)
    result = service.get_heartbeat()

    assert result["status"] == "degraded"
    assert result["ready_channels"] == 1
    assert result["degraded_channels"] == 1
    assert result["ready_channel_keys"] == ["openclaw"]
    assert result["degraded_channel_keys"] == ["telegram"]
    assert result["issues"] == ["telegram:bot-token-missing"]
