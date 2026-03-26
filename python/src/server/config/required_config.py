"""Helpers for startup validation messages tied to ``REQUIRED_CONFIG.md``."""

from __future__ import annotations


REQUIRED_CONFIG_PATH = "REQUIRED_CONFIG.md"


def required_config_reference(anchor: str | None = None) -> str:
    """Return a short reference to the repository startup checklist."""
    if not anchor:
        return REQUIRED_CONFIG_PATH
    return f"{REQUIRED_CONFIG_PATH}#{anchor}"


def missing_env_message(
    service_name: str,
    env_names: tuple[str, ...],
    *,
    detail: str | None = None,
    anchor: str | None = None,
) -> str:
    """Build a clear fail-fast message for missing startup configuration."""
    if len(env_names) == 1:
        names_text = env_names[0]
    elif len(env_names) == 2:
        names_text = f"{env_names[0]} or {env_names[1]}"
    else:
        names_text = ", ".join(env_names[:-1]) + f", or {env_names[-1]}"

    message = f"{service_name} requires {names_text} to be set before startup."
    if detail:
        message = f"{message} {detail}"
    return f"{message} See {required_config_reference(anchor)}."
