"""Tests for start_engine runtime configuration resolution."""

from start_engine import resolve_engine_runtime_config


def test_resolve_engine_runtime_config_prefers_platform_aliases():
    """Platform aliases should override legacy engine env vars."""
    config = resolve_engine_runtime_config(
        {
            "LEANKIT_CONTROL_PLANE_URL": "http://platform-server:9191",
            "ARCHON_API_URL": "http://legacy-server:8181",
            "LEANKIT_ENGINE_MAX_PARALLEL": "4",
            "MAX_PARALLEL": "3",
            "LEANKIT_ENGINE_MAX_PARALLEL_GLOBAL": "11",
            "MAX_PARALLEL_GLOBAL": "10",
            "LEANKIT_ENGINE_TASK_TIMEOUT_SECONDS": "1900",
            "TASK_ENGINE_TIMEOUT": "1800",
        }
    )

    assert config["archon_api"] == "http://platform-server:9191"
    assert config["default_max_parallel"] == 4
    assert config["max_parallel_global"] == 11
    assert config["task_timeout"] == 1900


def test_resolve_engine_runtime_config_falls_back_to_legacy_names():
    """Legacy env vars should still work when platform aliases are absent."""
    config = resolve_engine_runtime_config(
        {
            "ARCHON_API_URL": "http://legacy-server:8181",
            "MAX_PARALLEL": "5",
            "MAX_PARALLEL_GLOBAL": "12",
            "TASK_ENGINE_TIMEOUT": "2000",
        }
    )

    assert config["archon_api"] == "http://legacy-server:8181"
    assert config["default_max_parallel"] == 5
    assert config["max_parallel_global"] == 12
    assert config["task_timeout"] == 2000
