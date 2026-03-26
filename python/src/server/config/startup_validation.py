"""Service startup validation helpers tied to ``REQUIRED_CONFIG.md``."""

from __future__ import annotations

from collections.abc import Mapping

from .env_aliases import get_agent_service_port, get_control_plane_port
from .required_config import missing_env_message, required_config_reference


def _env_names_text(env_names: tuple[str, ...]) -> str:
    """Format environment variable names for user-facing error messages."""
    if len(env_names) == 1:
        return env_names[0]
    if len(env_names) == 2:
        return f"{env_names[0]} or {env_names[1]}"
    return ", ".join(env_names[:-1]) + f", or {env_names[-1]}"


def _require_port(
    port_value: str | None,
    *,
    service_name: str,
    env_names: tuple[str, ...],
    detail: str,
    anchor: str,
) -> int:
    """Require a valid integer port and return it."""
    if not port_value:
        raise ValueError(missing_env_message(service_name, env_names, detail=detail, anchor=anchor))

    try:
        port = int(port_value)
    except ValueError as exc:
        raise ValueError(
            f"{service_name} requires {_env_names_text(env_names)} to be a valid integer port before startup. "
            f"Got: {port_value!r}. See {required_config_reference(anchor)}."
        ) from exc

    if not 1 <= port <= 65535:
        raise ValueError(
            f"{service_name} requires {_env_names_text(env_names)} to be between 1 and 65535 before startup. "
            f"Got: {port}. See {required_config_reference(anchor)}."
        )

    return port


def validate_backend_entrypoint_port(env: Mapping[str, str] | None = None) -> int:
    """Validate the backend port for the direct ``python -m`` entrypoint."""
    return _require_port(
        get_control_plane_port(env),
        service_name="archon-backend",
        env_names=("LEANKIT_CONTROL_PLANE_PORT", "ARCHON_SERVER_PORT"),
        detail="This is required when starting the backend via python -m src.server.main.",
        anchor="archon-backend",
    )


def validate_agents_control_plane_port(env: Mapping[str, str] | None = None) -> int:
    """Validate the control-plane port required by the agents service at startup."""
    return _require_port(
        get_control_plane_port(env),
        service_name="archon-agents",
        env_names=("LEANKIT_CONTROL_PLANE_PORT", "ARCHON_SERVER_PORT"),
        detail="The agents service fetches credentials from archon-server during startup.",
        anchor="archon-agents",
    )


def validate_agents_entrypoint_port(env: Mapping[str, str] | None = None) -> int:
    """Validate the agents-service port for the direct ``python -m`` entrypoint."""
    return _require_port(
        get_agent_service_port(env),
        service_name="archon-agents",
        env_names=("LEANKIT_AGENT_SERVICE_PORT", "ARCHON_AGENTS_PORT"),
        detail="This is required when starting the agents service via python -m src.agents.server.",
        anchor="archon-agents",
    )
