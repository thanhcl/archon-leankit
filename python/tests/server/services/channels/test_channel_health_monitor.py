"""Tests for the background channel health monitor."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.server.services.channels.channel_health_monitor import (
    ChannelHealthMonitor,
    _format_degradation_alert,
    _format_recovery_alert,
)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def test_format_degradation_alert_includes_channel_names():
    text = _format_degradation_alert(["telegram"], ["bot-token-missing"])
    assert "telegram" in text
    assert "bot-token-missing" in text
    assert "🔴" in text


def test_format_degradation_alert_multiple_channels():
    text = _format_degradation_alert(["telegram", "openclaw"], ["issue-a", "issue-b"])
    assert "telegram" in text
    assert "openclaw" in text


def test_format_degradation_alert_empty_issues():
    text = _format_degradation_alert(["telegram"], [])
    assert "telegram" in text
    assert "Issues" not in text


def test_format_recovery_alert_includes_channels():
    text = _format_recovery_alert(["telegram", "openclaw"])
    assert "✅" in text
    assert "telegram" in text
    assert "openclaw" in text


# ---------------------------------------------------------------------------
# _check_once — degradation triggers alert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_once_sends_alert_on_degradation():
    health_service = MagicMock()
    health_service.get_heartbeat.return_value = {
        "status": "degraded",
        "degraded_channel_keys": ["telegram"],
        "ready_channel_keys": [],
        "issues": ["bot-token-missing"],
    }
    telegram = MagicMock()
    telegram.send_text = AsyncMock(return_value=(True, {}))

    monitor = ChannelHealthMonitor(
        health_service=health_service,
        telegram_channel=telegram,
        check_interval_seconds=300,
        alert_dedupe_seconds=1800,
        enabled=True,
    )
    await monitor._check_once()

    telegram.send_text.assert_awaited_once()
    call_text = telegram.send_text.call_args[0][0]
    assert "telegram" in call_text
    assert monitor._last_alert_at is not None
    assert monitor._last_overall_status == "degraded"


@pytest.mark.asyncio
async def test_check_once_no_alert_within_dedupe_window():
    health_service = MagicMock()
    health_service.get_heartbeat.return_value = {
        "status": "degraded",
        "degraded_channel_keys": ["telegram"],
        "ready_channel_keys": [],
        "issues": ["bot-token-missing"],
    }
    telegram = MagicMock()
    telegram.send_text = AsyncMock(return_value=(True, {}))

    monitor = ChannelHealthMonitor(
        health_service=health_service,
        telegram_channel=telegram,
        check_interval_seconds=300,
        alert_dedupe_seconds=1800,
        enabled=True,
    )
    # Pre-set last_alert_at to recent time (within dedupe window)
    monitor._last_alert_at = datetime.now(timezone.utc) - timedelta(seconds=60)

    await monitor._check_once()

    telegram.send_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_once_resends_alert_after_dedupe_window_expires():
    health_service = MagicMock()
    health_service.get_heartbeat.return_value = {
        "status": "degraded",
        "degraded_channel_keys": ["telegram"],
        "ready_channel_keys": [],
        "issues": ["bot-token-missing"],
    }
    telegram = MagicMock()
    telegram.send_text = AsyncMock(return_value=(True, {}))

    monitor = ChannelHealthMonitor(
        health_service=health_service,
        telegram_channel=telegram,
        check_interval_seconds=300,
        alert_dedupe_seconds=30,  # 30s dedupe window
        enabled=True,
    )
    # Pre-set last_alert_at to BEFORE dedupe window
    monitor._last_alert_at = datetime.now(timezone.utc) - timedelta(seconds=60)

    await monitor._check_once()

    telegram.send_text.assert_awaited_once()


# ---------------------------------------------------------------------------
# _check_once — recovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_once_sends_recovery_alert_on_recovery():
    health_service = MagicMock()
    health_service.get_heartbeat.return_value = {
        "status": "ready",
        "degraded_channel_keys": [],
        "ready_channel_keys": ["telegram", "openclaw"],
        "issues": [],
    }
    telegram = MagicMock()
    telegram.send_text = AsyncMock(return_value=(True, {}))

    monitor = ChannelHealthMonitor(
        health_service=health_service,
        telegram_channel=telegram,
        check_interval_seconds=300,
        alert_dedupe_seconds=1800,
        enabled=True,
    )
    # Simulate previous degraded state
    monitor._last_overall_status = "degraded"
    monitor._last_alert_at = datetime.now(timezone.utc) - timedelta(seconds=100)

    await monitor._check_once()

    telegram.send_text.assert_awaited_once()
    call_text = telegram.send_text.call_args[0][0]
    assert "✅" in call_text
    # last_alert_at cleared on recovery
    assert monitor._last_alert_at is None
    assert monitor._last_overall_status == "ready"


@pytest.mark.asyncio
async def test_check_once_no_recovery_alert_if_already_ready():
    health_service = MagicMock()
    health_service.get_heartbeat.return_value = {
        "status": "ready",
        "degraded_channel_keys": [],
        "ready_channel_keys": ["telegram"],
        "issues": [],
    }
    telegram = MagicMock()
    telegram.send_text = AsyncMock(return_value=(True, {}))

    monitor = ChannelHealthMonitor(
        health_service=health_service,
        telegram_channel=telegram,
        check_interval_seconds=300,
        alert_dedupe_seconds=1800,
        enabled=True,
    )
    # Already in ready state — no recovery alert expected
    monitor._last_overall_status = "ready"

    await monitor._check_once()

    telegram.send_text.assert_not_awaited()


# ---------------------------------------------------------------------------
# start / stop lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_creates_background_task_when_enabled():
    health_service = MagicMock()
    health_service.get_heartbeat.return_value = {
        "status": "ready",
        "degraded_channel_keys": [],
        "ready_channel_keys": ["telegram"],
        "issues": [],
    }
    telegram = MagicMock()
    telegram.send_text = AsyncMock(return_value=(True, {}))

    monitor = ChannelHealthMonitor(
        health_service=health_service,
        telegram_channel=telegram,
        check_interval_seconds=9999,
        enabled=True,
    )
    monitor.start()

    assert monitor._task is not None
    assert not monitor._task.done()

    await monitor.stop()
    assert monitor._task.done()


@pytest.mark.asyncio
async def test_start_does_nothing_when_disabled():
    health_service = MagicMock()
    telegram = MagicMock()

    monitor = ChannelHealthMonitor(
        health_service=health_service,
        telegram_channel=telegram,
        enabled=False,
    )
    monitor.start()

    assert monitor._task is None


@pytest.mark.asyncio
async def test_stop_is_idempotent_when_not_started():
    monitor = ChannelHealthMonitor(
        health_service=MagicMock(),
        telegram_channel=MagicMock(),
        enabled=True,
    )
    # Should not raise
    await monitor.stop()


# ---------------------------------------------------------------------------
# env_aliases helpers
# ---------------------------------------------------------------------------


def test_env_alias_channel_health_monitor_enabled_default():
    from src.server.config.env_aliases import get_channel_health_monitor_enabled

    assert get_channel_health_monitor_enabled(env={}) is True


def test_env_alias_channel_health_monitor_disabled_via_env():
    from src.server.config.env_aliases import get_channel_health_monitor_enabled

    assert get_channel_health_monitor_enabled(env={"LEANKIT_CHANNEL_HEALTH_MONITOR_ENABLED": "false"}) is False


def test_env_alias_check_interval_default():
    from src.server.config.env_aliases import get_channel_health_check_interval_seconds

    assert get_channel_health_check_interval_seconds(env={}) == 300


def test_env_alias_check_interval_custom():
    from src.server.config.env_aliases import get_channel_health_check_interval_seconds

    assert get_channel_health_check_interval_seconds(env={"LEANKIT_CHANNEL_HEALTH_CHECK_INTERVAL_SECONDS": "120"}) == 120


def test_env_alias_check_interval_minimum_enforced():
    from src.server.config.env_aliases import get_channel_health_check_interval_seconds

    assert get_channel_health_check_interval_seconds(env={"LEANKIT_CHANNEL_HEALTH_CHECK_INTERVAL_SECONDS": "1"}) == 10


def test_env_alias_alert_dedupe_default():
    from src.server.config.env_aliases import get_channel_health_alert_dedupe_seconds

    assert get_channel_health_alert_dedupe_seconds(env={}) == 1800


def test_env_alias_alert_dedupe_custom():
    from src.server.config.env_aliases import get_channel_health_alert_dedupe_seconds

    assert get_channel_health_alert_dedupe_seconds(env={"LEANKIT_CHANNEL_HEALTH_ALERT_DEDUPE_SECONDS": "600"}) == 600


def test_env_alias_alert_dedupe_minimum_enforced():
    from src.server.config.env_aliases import get_channel_health_alert_dedupe_seconds

    assert get_channel_health_alert_dedupe_seconds(env={"LEANKIT_CHANNEL_HEALTH_ALERT_DEDUPE_SECONDS": "5"}) == 60
