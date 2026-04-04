"""Tests for start_engine runtime and review configuration resolution."""

import json
from unittest.mock import MagicMock, patch

from start_engine import (
    ReviewConfig,
    _coerce_review_config_payload,
    fetch_review_config,
    resolve_engine_runtime_config,
)


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


def test_coerce_review_config_payload_uses_api_values():
    config = _coerce_review_config_payload(
        {
            "review_mode": "api",
            "provider": "anthropic",
            "model": "claude-opus-4-6",
            "timeout": 90,
        }
    )

    assert isinstance(config, ReviewConfig)
    assert config.review_mode == "api"
    assert config.provider == "anthropic"
    assert config.model == "claude-opus-4-6"
    assert config.timeout == 90


def test_fetch_review_config_returns_defaults_on_error():
    with patch("start_engine.urlopen", side_effect=RuntimeError("boom")):
        config = fetch_review_config("http://archon")

    assert config == ReviewConfig()


def test_fetch_review_config_reads_control_plane_payload():
    response = MagicMock()
    response.read.return_value = json.dumps(
        {
            "review_mode": "api",
            "provider": "anthropic",
            "model": "claude-opus-4-6",
        }
    ).encode()

    with patch("start_engine.urlopen", return_value=response):
        config = fetch_review_config("http://archon")

    assert config.review_mode == "api"
    assert config.provider == "anthropic"
    assert config.model == "claude-opus-4-6"
