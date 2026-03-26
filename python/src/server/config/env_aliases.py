"""Helpers for platform-aligned environment variable aliases."""

from __future__ import annotations

import os
from collections.abc import Mapping
from urllib.parse import urlparse, urlunparse


def get_env_value(
    *names: str, default: str | None = None, env: Mapping[str, str] | None = None
) -> str | None:
    """Return the first non-empty value from the provided environment variable names."""
    source = env or os.environ
    for name in names:
        value = source.get(name)
        if value not in (None, ""):
            return value
    return default


def get_control_plane_port(env: Mapping[str, str] | None = None) -> str | None:
    """Resolve the Archon control-plane port from platform or legacy names."""
    return get_env_value("LEANKIT_CONTROL_PLANE_PORT", "ARCHON_SERVER_PORT", env=env)


def get_control_plane_mcp_port(env: Mapping[str, str] | None = None) -> str | None:
    """Resolve the Archon MCP port from platform or legacy names."""
    return get_env_value("LEANKIT_CONTROL_PLANE_MCP_PORT", "ARCHON_MCP_PORT", env=env)


def get_agent_service_port(env: Mapping[str, str] | None = None) -> str | None:
    """Resolve the Archon agents-service port from platform or legacy names."""
    return get_env_value("LEANKIT_AGENT_SERVICE_PORT", "ARCHON_AGENTS_PORT", env=env)


def _rewrite_localhost_for_docker_compose(url: str, env: Mapping[str, str] | None = None) -> str:
    """Rewrite localhost-style URLs so Docker containers can reach host services."""
    source = env or os.environ
    if source.get("SERVICE_DISCOVERY_MODE") != "docker_compose":
        return url

    parsed = urlparse(url)
    if parsed.hostname not in {"localhost", "127.0.0.1"}:
        return url

    host = "host.docker.internal"
    netloc = host
    if parsed.port is not None:
        netloc = f"{host}:{parsed.port}"
    return urlunparse(parsed._replace(netloc=netloc))


def get_observability_ingest_url(env: Mapping[str, str] | None = None) -> str:
    """Resolve the observability ingest URL from platform or legacy names."""
    resolved = (
        get_env_value("LEANKIT_OBSERVABILITY_INGEST_URL", "OBSERVABILITY_URL", env=env)
        or "http://localhost:4000/api/events"
    )
    return _rewrite_localhost_for_docker_compose(resolved, env)


def get_observability_replay_url(env: Mapping[str, str] | None = None) -> str:
    """Resolve the observability replay endpoint from platform or legacy names."""
    explicit = get_env_value("LEANKIT_OBSERVABILITY_REPLAY_URL", "OBSERVABILITY_REPLAY_URL", env=env)
    if explicit:
        return _rewrite_localhost_for_docker_compose(explicit, env)

    ingest_url = get_observability_ingest_url(env)
    if ingest_url.endswith("/api/events"):
        return f"{ingest_url[:-len('/api/events')]}/api/unified-events/recent"
    return _rewrite_localhost_for_docker_compose("http://localhost:4000/api/unified-events/recent", env)


def get_telegram_bot_token(env: Mapping[str, str] | None = None) -> str | None:
    """Resolve Telegram bot token from platform or legacy names."""
    return get_env_value("LEANKIT_TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_TOKEN", env=env)


def get_telegram_chat_id(env: Mapping[str, str] | None = None) -> str | None:
    """Resolve Telegram chat id from platform or legacy names."""
    return get_env_value("LEANKIT_TELEGRAM_CHAT_ID", "TELEGRAM_CHAT_ID", env=env)


def get_telegram_webhook_secret(env: Mapping[str, str] | None = None) -> str | None:
    """Resolve Telegram webhook secret token."""
    return get_env_value("LEANKIT_TELEGRAM_WEBHOOK_SECRET", "TELEGRAM_WEBHOOK_SECRET", env=env)


def get_telegram_only_critical(env: Mapping[str, str] | None = None) -> bool:
    """Resolve Telegram critical-only flag from env."""
    value = get_env_value("LEANKIT_TELEGRAM_ONLY_CRITICAL", "TELEGRAM_ONLY_CRITICAL", env=env)
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def get_telegram_notify_events(env: Mapping[str, str] | None = None) -> list[str]:
    """Resolve Telegram notify event allowlist from env."""
    value = get_env_value("LEANKIT_TELEGRAM_NOTIFY_EVENTS", "TELEGRAM_NOTIFY_EVENTS", env=env)
    if value is None:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def get_telegram_digest_events(env: Mapping[str, str] | None = None) -> list[str]:
    """Resolve Telegram digest-event allowlist from env."""
    value = get_env_value("LEANKIT_TELEGRAM_DIGEST_EVENTS", "TELEGRAM_DIGEST_EVENTS", env=env)
    if value is None:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def get_telegram_digest_limit(env: Mapping[str, str] | None = None) -> int:
    """Resolve Telegram digest-event maximum entry count."""
    value = get_env_value("LEANKIT_TELEGRAM_DIGEST_LIMIT", "TELEGRAM_DIGEST_LIMIT", env=env)
    if value is None:
        return 10
    try:
        return max(1, int(value))
    except ValueError:
        return 10


def get_telegram_digest_hour(env: Mapping[str, str] | None = None) -> int:
    """Resolve Telegram digest hour in 24h format."""
    value = get_env_value("LEANKIT_TELEGRAM_DIGEST_HOUR", "TELEGRAM_DIGEST_HOUR", env=env)
    if value is None:
        return 9
    try:
        parsed = int(value)
    except ValueError:
        return 9
    return min(23, max(0, parsed))


def get_telegram_digest_minute(env: Mapping[str, str] | None = None) -> int:
    """Resolve Telegram digest minute."""
    value = get_env_value("LEANKIT_TELEGRAM_DIGEST_MINUTE", "TELEGRAM_DIGEST_MINUTE", env=env)
    if value is None:
        return 0
    try:
        parsed = int(value)
    except ValueError:
        return 0
    return min(59, max(0, parsed))


def get_telegram_digest_timezone(env: Mapping[str, str] | None = None) -> str:
    """Resolve Telegram digest timezone identifier."""
    return get_env_value("LEANKIT_TELEGRAM_DIGEST_TIMEZONE", "TELEGRAM_DIGEST_TIMEZONE", env=env) or "UTC"


def get_telegram_digest_lookback_minutes(env: Mapping[str, str] | None = None) -> int:
    """Resolve the replay lookback window used when building scheduled digests."""
    value = get_env_value(
        "LEANKIT_TELEGRAM_DIGEST_LOOKBACK_MINUTES",
        "TELEGRAM_DIGEST_LOOKBACK_MINUTES",
        env=env,
    )
    if value is None:
        return 1440
    try:
        parsed = int(value)
    except ValueError:
        return 1440
    return max(5, parsed)


def get_telegram_digest_scheduler_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Resolve whether the due-digest scheduler path is enabled."""
    value = get_env_value(
        "LEANKIT_TELEGRAM_DIGEST_SCHEDULER_ENABLED",
        "TELEGRAM_DIGEST_SCHEDULER_ENABLED",
        env=env,
    )
    if value is None:
        return True
    return value.strip().lower() in {"1", "true", "yes", "on"}


def get_openclaw_ingest_secret(env: Mapping[str, str] | None = None) -> str | None:
    """Resolve OpenClaw ingress secret."""
    return get_env_value("LEANKIT_OPENCLAW_INGEST_SECRET", "OPENCLAW_INGEST_SECRET", env=env)


def get_openclaw_replay_window_minutes(env: Mapping[str, str] | None = None) -> int:
    """Resolve replay window used by OpenClaw semantic/sequence dedupe."""
    value = get_env_value("LEANKIT_OPENCLAW_REPLAY_WINDOW_MINUTES", "OPENCLAW_REPLAY_WINDOW_MINUTES", env=env)
    if value is None:
        return 30
    try:
        parsed = int(value)
    except ValueError:
        return 30
    return max(1, parsed)


def get_control_plane_url(env: Mapping[str, str] | None = None) -> str:
    """Resolve the control-plane base URL from platform or legacy names."""
    explicit = get_env_value("LEANKIT_CONTROL_PLANE_URL", "ARCHON_SERVER_URL", "ARCHON_API_URL", env=env)
    if explicit:
        return _rewrite_localhost_for_docker_compose(explicit, env)
    port = get_control_plane_port(env) or "8181"
    return _rewrite_localhost_for_docker_compose(f"http://localhost:{port}", env)


def get_control_plane_mcp_url(env: Mapping[str, str] | None = None) -> str:
    """Resolve the control-plane MCP URL from platform or legacy names."""
    explicit = get_env_value("LEANKIT_CONTROL_PLANE_MCP_URL", "ARCHON_MCP_URL", env=env)
    if explicit:
        return _rewrite_localhost_for_docker_compose(explicit, env)
    port = get_control_plane_mcp_port(env) or "8051"
    return _rewrite_localhost_for_docker_compose(f"http://localhost:{port}", env)


def get_agent_work_orders_port(env: Mapping[str, str] | None = None) -> str | None:
    """Resolve the agent-work-orders port from platform or legacy names."""
    return get_env_value("LEANKIT_AGENT_WORK_ORDERS_PORT", "AGENT_WORK_ORDERS_PORT", env=env)


def get_runner_token_profiles_path(env: Mapping[str, str] | None = None) -> str | None:
    """Resolve the runner token profiles config path."""
    return get_env_value("LEANKIT_RUNNER_TOKEN_PROFILES_PATH", env=env)


_DEFAULT_RUNNER_ENV_ALLOWLIST = "MAX_THINKING_TOKENS,CLAUDE_AUTOCOMPACT_PCT_OVERRIDE,CLAUDE_CODE_SUBAGENT_MODEL"


def get_runner_env_allowlist(env: Mapping[str, str] | None = None) -> list[str]:
    """Resolve the allowlist of env var names that may be injected into runner subprocesses.

    Returns a list of uppercase env var names. Defaults to a safe set of Claude Code
    tuning variables if not overridden via LEANKIT_RUNNER_ENV_ALLOWLIST.
    """
    value = get_env_value("LEANKIT_RUNNER_ENV_ALLOWLIST", env=env)
    if value is None:
        value = _DEFAULT_RUNNER_ENV_ALLOWLIST
    return [item.strip().upper() for item in value.split(",") if item.strip()]


def get_engine_default_daily_budget(env: Mapping[str, str] | None = None) -> float:
    """Resolve the default per-project daily budget limit in USD."""
    value = get_env_value("LEANKIT_ENGINE_DEFAULT_DAILY_BUDGET", env=env)
    if value is None:
        return 50.0
    try:
        return max(0.0, float(value))
    except ValueError:
        return 50.0


def get_engine_default_weekly_budget(env: Mapping[str, str] | None = None) -> float:
    """Resolve the default per-project weekly budget limit in USD."""
    value = get_env_value("LEANKIT_ENGINE_DEFAULT_WEEKLY_BUDGET", env=env)
    if value is None:
        return 200.0
    try:
        return max(0.0, float(value))
    except ValueError:
        return 200.0


def get_engine_retry_delay_seconds(env: Mapping[str, str] | None = None) -> int:
    """Resolve the base retry delay in seconds before re-queuing a failed task."""
    value = get_env_value("LEANKIT_ENGINE_RETRY_DELAY_SECONDS", env=env)
    if value is None:
        return 60
    try:
        return max(0, int(value))
    except ValueError:
        return 60


def get_engine_retry_max_delay_seconds(env: Mapping[str, str] | None = None) -> int:
    """Resolve the maximum retry delay cap in seconds for exponential backoff."""
    value = get_env_value("LEANKIT_ENGINE_RETRY_MAX_DELAY_SECONDS", env=env)
    if value is None:
        return 3600
    try:
        return max(1, int(value))
    except ValueError:
        return 3600


def get_engine_escalation_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Resolve whether exhausted retries escalate the task rather than leaving it failed."""
    value = get_env_value("LEANKIT_ENGINE_ESCALATION_ENABLED", env=env)
    if value is None:
        return True
    return value.strip().lower() in {"1", "true", "yes", "on"}


def get_engine_pid_check_interval(env: Mapping[str, str] | None = None) -> int:
    """Resolve the CC process liveness check interval in seconds for the engine watchdog."""
    value = get_env_value("LEANKIT_ENGINE_PID_CHECK_INTERVAL", env=env)
    if value is None:
        return 30
    try:
        return max(5, int(value))
    except ValueError:
        return 30


def get_channel_health_monitor_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Resolve whether the background channel health monitor is enabled."""
    value = get_env_value("LEANKIT_CHANNEL_HEALTH_MONITOR_ENABLED", env=env)
    if value is None:
        return True
    return value.strip().lower() in {"1", "true", "yes", "on"}


def get_channel_health_check_interval_seconds(env: Mapping[str, str] | None = None) -> int:
    """Resolve the channel health poll interval in seconds (default: 300)."""
    value = get_env_value("LEANKIT_CHANNEL_HEALTH_CHECK_INTERVAL_SECONDS", env=env)
    if value is None:
        return 300
    try:
        return max(10, int(value))
    except ValueError:
        return 300


def get_channel_health_alert_dedupe_seconds(env: Mapping[str, str] | None = None) -> int:
    """Resolve the alert dedupe window in seconds (default: 1800 / 30 min)."""
    value = get_env_value("LEANKIT_CHANNEL_HEALTH_ALERT_DEDUPE_SECONDS", env=env)
    if value is None:
        return 1800
    try:
        return max(60, int(value))
    except ValueError:
        return 1800
