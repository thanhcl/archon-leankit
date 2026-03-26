"""Configuration Management

Loads configuration from environment variables with sensible defaults.
"""

from collections.abc import Mapping
import os
from pathlib import Path

from src.server.config.config import ConfigurationError
from src.server.config.env_aliases import (
    get_agent_work_orders_port,
    get_control_plane_mcp_url,
    get_control_plane_url,
    get_env_value,
)
from src.server.config.required_config import missing_env_message, required_config_reference


def get_project_root() -> Path:
    """Get the project root directory (one level up from python/)"""
    # This file is in python/src/agent_work_orders/config.py
    # So go up 3 levels to get to project root
    return Path(__file__).parent.parent.parent.parent


class AgentWorkOrdersConfig:
    """Configuration for Agent Work Orders service"""

    # Feature flag - allows disabling agent work orders entirely
    ENABLED: bool = (get_env_value("LEANKIT_ENABLE_AGENT_WORK_ORDERS", "ENABLE_AGENT_WORK_ORDERS", default="false") or "false").lower() == "true"

    CLAUDE_CLI_PATH: str = get_env_value("LEANKIT_RUNNER_CLAUDE_PATH", "CLAUDE_CLI_PATH", default="claude") or "claude"
    EXECUTION_TIMEOUT: int = int(
        get_env_value("LEANKIT_RUNNER_TIMEOUT_SECONDS", "AGENT_WORK_ORDER_TIMEOUT", default="3600") or "3600"
    )

    # Default to python/.claude/commands/agent-work-orders
    _python_root = Path(__file__).parent.parent.parent
    _default_commands_dir = str(_python_root / ".claude" / "commands" / "agent-work-orders")
    COMMANDS_DIRECTORY: str = get_env_value(
        "LEANKIT_AGENT_WORK_ORDER_COMMANDS_DIR", "AGENT_WORK_ORDER_COMMANDS_DIR", default=_default_commands_dir
    ) or _default_commands_dir

    TEMP_DIR_BASE: str = get_env_value(
        "LEANKIT_AGENT_WORK_ORDER_TEMP_DIR", "AGENT_WORK_ORDER_TEMP_DIR", default="/tmp/agent-work-orders"
    ) or "/tmp/agent-work-orders"
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    GH_CLI_PATH: str = os.getenv("GH_CLI_PATH", "gh")

    # Service discovery configuration
    SERVICE_DISCOVERY_MODE: str = get_env_value(
        "LEANKIT_SERVICE_DISCOVERY_MODE", "SERVICE_DISCOVERY_MODE", default="local"
    ) or "local"

    # CORS configuration
    CORS_ORIGINS: str = os.getenv("CORS_ORIGINS", "http://localhost:3737,http://host.docker.internal:3737,*")

    # Claude CLI flags configuration
    # --verbose: Required when using --print with --output-format=stream-json
    CLAUDE_CLI_VERBOSE: bool = (
        get_env_value("LEANKIT_RUNNER_CLAUDE_VERBOSE", "CLAUDE_CLI_VERBOSE", default="true") or "true"
    ).lower() == "true"

    # --max-turns: Optional limit for agent executions. Set to None for unlimited.
    # Default: None (no limit - let agent run until completion)
    _max_turns_env = get_env_value("LEANKIT_RUNNER_CLAUDE_MAX_TURNS", "CLAUDE_CLI_MAX_TURNS")
    CLAUDE_CLI_MAX_TURNS: int | None = int(_max_turns_env) if _max_turns_env else None

    # --model: Claude model to use (sonnet, opus, haiku)
    CLAUDE_CLI_MODEL: str = (
        get_env_value("LEANKIT_RUNNER_CLAUDE_MODEL", "CLAUDE_CLI_MODEL", default="sonnet") or "sonnet"
    )

    # --dangerously-skip-permissions: Required for non-interactive automation
    CLAUDE_CLI_SKIP_PERMISSIONS: bool = (
        get_env_value("LEANKIT_RUNNER_SKIP_PERMISSIONS", "CLAUDE_CLI_SKIP_PERMISSIONS", default="true") or "true"
    ).lower() == "true"

    # Logging configuration
    # Enable saving prompts and outputs for debugging
    ENABLE_PROMPT_LOGGING: bool = os.getenv("ENABLE_PROMPT_LOGGING", "true").lower() == "true"
    ENABLE_OUTPUT_ARTIFACTS: bool = os.getenv("ENABLE_OUTPUT_ARTIFACTS", "true").lower() == "true"

    # Worktree configuration
    WORKTREE_BASE_DIR: str = os.getenv("WORKTREE_BASE_DIR", "trees")

    # Port allocation for parallel execution
    BACKEND_PORT_RANGE_START: int = int(
        get_env_value("LEANKIT_BACKEND_PORT_START", "BACKEND_PORT_START", default="9100") or "9100"
    )
    BACKEND_PORT_RANGE_END: int = int(
        get_env_value("LEANKIT_BACKEND_PORT_END", "BACKEND_PORT_END", default="9114") or "9114"
    )
    FRONTEND_PORT_RANGE_START: int = int(
        get_env_value("LEANKIT_FRONTEND_PORT_START", "FRONTEND_PORT_START", default="9200") or "9200"
    )
    FRONTEND_PORT_RANGE_END: int = int(
        get_env_value("LEANKIT_FRONTEND_PORT_END", "FRONTEND_PORT_END", default="9214") or "9214"
    )

    # State management configuration
    STATE_STORAGE_TYPE: str = os.getenv("STATE_STORAGE_TYPE", "memory")  # "memory" or "file"
    FILE_STATE_DIRECTORY: str = os.getenv("FILE_STATE_DIRECTORY", "agent-work-orders-state")

    # Telemetry persistence configuration
    # SQLite database path for durable event storage beyond the in-memory buffer.
    TELEMETRY_DB_PATH: str = os.getenv(
        "TELEMETRY_DB_PATH",
        "/tmp/agent-work-orders/telemetry.db",
    )

    @classmethod
    def get_service_port(cls) -> str:
        """Get the agent work orders service port from platform or legacy names."""
        return get_agent_work_orders_port() or "8053"

    @classmethod
    def ensure_temp_dir(cls) -> Path:
        """Ensure temp directory exists and return Path"""
        temp_dir = Path(cls.TEMP_DIR_BASE)
        temp_dir.mkdir(parents=True, exist_ok=True)
        return temp_dir

    @classmethod
    def get_archon_server_url(cls) -> str:
        """Get Archon server URL based on service discovery mode"""
        # Allow explicit override
        explicit_url = get_env_value("LEANKIT_CONTROL_PLANE_URL", "ARCHON_SERVER_URL")
        if explicit_url:
            return explicit_url

        # Otherwise use service discovery mode
        if cls.SERVICE_DISCOVERY_MODE == "docker_compose":
            return "http://archon-server:8181"
        return "http://localhost:8181"

    @classmethod
    def get_archon_mcp_url(cls) -> str:
        """Get Archon MCP server URL based on service discovery mode"""
        # Allow explicit override
        explicit_url = get_env_value("LEANKIT_CONTROL_PLANE_MCP_URL", "ARCHON_MCP_URL")
        if explicit_url:
            return explicit_url

        # Otherwise use service discovery mode
        if cls.SERVICE_DISCOVERY_MODE == "docker_compose":
            return "http://archon-mcp:8051"
        return get_control_plane_mcp_url()


# Global config instance
config = AgentWorkOrdersConfig()


def validate_startup_config(env: Mapping[str, str] | None = None) -> None:
    """Validate startup-blocking agent work orders configuration."""
    source = env or os.environ
    enabled = (get_env_value("LEANKIT_ENABLE_AGENT_WORK_ORDERS", "ENABLE_AGENT_WORK_ORDERS", default="false", env=source) or "false").lower() == "true"
    if not enabled:
        return

    state_storage_type = (source.get("STATE_STORAGE_TYPE") or "memory").strip().lower()
    valid_storage_types = {"memory", "file", "supabase"}
    if state_storage_type not in valid_storage_types:
        expected = ", ".join(sorted(valid_storage_types))
        raise ConfigurationError(
            f"agent-work-orders requires STATE_STORAGE_TYPE to be one of {expected}. "
            f"Got: {state_storage_type!r}. See {required_config_reference('agent-work-orders')}."
        )

    if state_storage_type != "supabase":
        return

    missing: list[str] = []
    if source.get("SUPABASE_URL") in (None, ""):
        missing.append("SUPABASE_URL")
    if source.get("SUPABASE_SERVICE_KEY") in (None, ""):
        missing.append("SUPABASE_SERVICE_KEY")

    if missing:
        raise ConfigurationError(
            missing_env_message(
                "agent-work-orders",
                tuple(missing),
                detail="These variables are required when ENABLE_AGENT_WORK_ORDERS is true and STATE_STORAGE_TYPE=supabase.",
                anchor="agent-work-orders",
            )
        )
