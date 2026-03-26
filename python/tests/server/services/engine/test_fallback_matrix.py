"""Tests for the multi-level fallback matrix (runner, model, channel)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.server.services.engine.cc_spawner import (
    CCExecutionResult,
    MODEL_HAIKU,
    MODEL_OPUS,
    MODEL_SONNET,
    is_runner_level_failure,
)
from src.server.services.engine.runner_adapter import DEFAULT_RUNNER_KEY
from src.server.services.engine.runner_routing import (
    CODEX_RUNNER_KEY,
    get_fallback_runner_chain,
)


# ---------------------------------------------------------------------------
# is_runner_level_failure
# ---------------------------------------------------------------------------


def _make_result(**kwargs) -> CCExecutionResult:
    defaults = dict(success=False, stdout="", stderr="", exit_code=-1, duration_seconds=0.1)
    defaults.update(kwargs)
    return CCExecutionResult(**defaults)  # type: ignore[arg-type]


class TestIsRunnerLevelFailure:
    def test_success_is_not_runner_failure(self):
        result = _make_result(success=True, exit_code=0)
        assert not is_runner_level_failure(result)

    def test_nonzero_exit_is_task_failure(self):
        result = _make_result(exit_code=1, stderr="RESULT: FAILURE")
        assert not is_runner_level_failure(result)

    def test_binary_not_found_is_runner_failure(self):
        result = _make_result(stderr="codex binary not found: codex")
        assert is_runner_level_failure(result)

    def test_command_not_found_is_runner_failure(self):
        result = _make_result(stderr="claude: command not found")
        assert is_runner_level_failure(result)

    def test_no_such_file_is_runner_failure(self):
        result = _make_result(stderr="no such file or directory")
        assert is_runner_level_failure(result)

    def test_exec_format_error_is_runner_failure(self):
        result = _make_result(stderr="exec format error")
        assert is_runner_level_failure(result)

    def test_long_duration_excludes_runner_failure(self):
        # If duration >= 5s, the runner did start — classify as task failure
        result = _make_result(stderr="command not found", duration_seconds=10.0)
        assert not is_runner_level_failure(result)

    def test_task_failure_summary_not_runner_failure(self):
        result = _make_result(exit_code=-1, stderr="task timed out after 600s", duration_seconds=601.0)
        assert not is_runner_level_failure(result)

    def test_generic_failure_not_runner_failure(self):
        result = _make_result(stderr="something went wrong")
        assert not is_runner_level_failure(result)


# ---------------------------------------------------------------------------
# get_fallback_runner_chain
# ---------------------------------------------------------------------------


class TestGetFallbackRunnerChain:
    def test_returns_codex_when_primary_is_claude(self):
        chain = get_fallback_runner_chain(
            DEFAULT_RUNNER_KEY,
            available_runner_keys={DEFAULT_RUNNER_KEY, CODEX_RUNNER_KEY},
        )
        assert chain == [CODEX_RUNNER_KEY]

    def test_returns_claude_when_primary_is_codex(self):
        chain = get_fallback_runner_chain(
            CODEX_RUNNER_KEY,
            available_runner_keys={DEFAULT_RUNNER_KEY, CODEX_RUNNER_KEY},
        )
        assert chain == [DEFAULT_RUNNER_KEY]

    def test_excludes_unavailable_runners(self):
        chain = get_fallback_runner_chain(
            DEFAULT_RUNNER_KEY,
            available_runner_keys={DEFAULT_RUNNER_KEY},  # codex not registered
        )
        assert chain == []

    def test_policy_configured_chain_overrides_default(self):
        model_routing = {
            "fallback_policy": {
                "runner_fallback_chain": [CODEX_RUNNER_KEY, DEFAULT_RUNNER_KEY],
            }
        }
        chain = get_fallback_runner_chain(
            DEFAULT_RUNNER_KEY,
            available_runner_keys={DEFAULT_RUNNER_KEY, CODEX_RUNNER_KEY},
            model_routing=model_routing,
        )
        assert chain == [CODEX_RUNNER_KEY]  # primary excluded, codex is first

    def test_policy_runner_fallback_disabled(self):
        model_routing = {"fallback_policy": {"runner_fallback_enabled": False}}
        chain = get_fallback_runner_chain(
            DEFAULT_RUNNER_KEY,
            available_runner_keys={DEFAULT_RUNNER_KEY, CODEX_RUNNER_KEY},
            model_routing=model_routing,
        )
        assert chain == []

    def test_no_model_routing_uses_defaults(self):
        chain = get_fallback_runner_chain(
            DEFAULT_RUNNER_KEY,
            available_runner_keys={DEFAULT_RUNNER_KEY, CODEX_RUNNER_KEY},
            model_routing=None,
        )
        assert CODEX_RUNNER_KEY in chain
        assert DEFAULT_RUNNER_KEY not in chain

    def test_policy_chain_filters_unavailable_runners(self):
        model_routing = {
            "fallback_policy": {
                "runner_fallback_chain": [CODEX_RUNNER_KEY, DEFAULT_RUNNER_KEY],
            }
        }
        # codex not in available_runner_keys
        chain = get_fallback_runner_chain(
            DEFAULT_RUNNER_KEY,
            available_runner_keys={DEFAULT_RUNNER_KEY},
            model_routing=model_routing,
        )
        assert chain == []


# ---------------------------------------------------------------------------
# CCSpawner model fallback chain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestCCSpawnerModelFallback:
    async def test_explicit_chain_tried_in_order(self):
        """When model_fallback_chain is provided, each model is tried in order."""
        from src.server.services.engine.cc_spawner import CCSpawner, ProjectConfig

        spawner = CCSpawner()
        project_config = ProjectConfig(project_path="/tmp")

        call_log: list[str] = []

        async def fake_spawn(task_id, prompt, config, model, timeout=None, **kwargs):
            call_log.append(model)
            # First two fail, third succeeds
            return CCExecutionResult(
                success=(len(call_log) >= 3),
                stdout="",
                stderr="failed" if len(call_log) < 3 else "",
                exit_code=-1 if len(call_log) < 3 else 0,
                duration_seconds=0.1,
            )

        with patch.object(spawner, "_spawn_with_model", side_effect=fake_spawn):
            with patch.object(spawner, "select_model", return_value=MODEL_OPUS):
                result = await spawner.spawn(
                    task_id="t-1",
                    prompt="test",
                    config=project_config,
                    model_fallback_chain=[MODEL_SONNET, MODEL_HAIKU],
                )

        assert call_log == [MODEL_OPUS, MODEL_SONNET, MODEL_HAIKU]
        assert result.success
        assert result.parsed.get("fallback_from") == MODEL_OPUS
        assert result.parsed.get("fallback_to") == MODEL_HAIKU

    async def test_empty_chain_disables_fallback(self):
        """An empty model_fallback_chain disables all model fallback."""
        from src.server.services.engine.cc_spawner import CCSpawner, ProjectConfig

        spawner = CCSpawner()
        project_config = ProjectConfig(project_path="/tmp")
        call_log: list[str] = []

        async def fake_spawn(task_id, prompt, config, model, **kwargs):
            call_log.append(model)
            return CCExecutionResult(success=False, stdout="", stderr="fail", exit_code=-1, duration_seconds=0.1)

        with patch.object(spawner, "_spawn_with_model", side_effect=fake_spawn):
            with patch.object(spawner, "select_model", return_value=MODEL_SONNET):
                result = await spawner.spawn(
                    task_id="t-1",
                    prompt="test",
                    config=project_config,
                    model_fallback_chain=[],
                )

        assert call_log == [MODEL_SONNET]  # no fallback attempted
        assert not result.success

    async def test_legacy_default_upgrades_to_opus(self):
        """Without explicit chain, non-Opus failures still retry with Opus."""
        from src.server.services.engine.cc_spawner import CCSpawner, ProjectConfig

        spawner = CCSpawner()
        project_config = ProjectConfig(project_path="/tmp")
        call_log: list[str] = []

        async def fake_spawn(task_id, prompt, config, model, **kwargs):
            call_log.append(model)
            return CCExecutionResult(
                success=(model == MODEL_OPUS),
                stdout="",
                stderr="",
                exit_code=0 if model == MODEL_OPUS else -1,
                duration_seconds=0.1,
            )

        with patch.object(spawner, "_spawn_with_model", side_effect=fake_spawn):
            with patch.object(spawner, "select_model", return_value=MODEL_SONNET):
                result = await spawner.spawn(
                    task_id="t-1",
                    prompt="test",
                    config=project_config,
                    model_fallback_chain=None,  # legacy mode
                )

        assert call_log == [MODEL_SONNET, MODEL_OPUS]
        assert result.success
        assert result.parsed.get("fallback_from") == MODEL_SONNET
        assert result.parsed.get("fallback_to") == MODEL_OPUS


# ---------------------------------------------------------------------------
# Engine policy fallback_policy validation
# ---------------------------------------------------------------------------


class TestFallbackPolicyValidation:
    def test_valid_fallback_policy_passes(self):
        from src.server.api_routes.engine_policies_api import _validate_model_routing

        _validate_model_routing(
            {
                "fallback_policy": {
                    "runner_fallback_enabled": True,
                    "runner_fallback_chain": ["claude-code-cli", "codex-cli"],
                    "model_fallback_chain": ["claude-opus-4-6", "claude-sonnet-4-6"],
                }
            }
        )  # should not raise

    def test_invalid_runner_in_chain_raises(self):
        from fastapi import HTTPException

        from src.server.api_routes.engine_policies_api import _validate_model_routing

        with pytest.raises(HTTPException) as exc_info:
            _validate_model_routing(
                {
                    "fallback_policy": {
                        "runner_fallback_chain": ["not-a-real-runner"],
                    }
                }
            )
        assert exc_info.value.status_code == 400

    def test_invalid_type_raises(self):
        from fastapi import HTTPException

        from src.server.api_routes.engine_policies_api import _validate_model_routing

        with pytest.raises(HTTPException) as exc_info:
            _validate_model_routing({"fallback_policy": "not-a-dict"})
        assert exc_info.value.status_code == 400

    def test_runner_fallback_enabled_must_be_bool(self):
        from fastapi import HTTPException

        from src.server.api_routes.engine_policies_api import _validate_model_routing

        with pytest.raises(HTTPException) as exc_info:
            _validate_model_routing(
                {"fallback_policy": {"runner_fallback_enabled": "yes"}}
            )
        assert exc_info.value.status_code == 400

    def test_model_fallback_chain_must_be_list(self):
        from fastapi import HTTPException

        from src.server.api_routes.engine_policies_api import _validate_model_routing

        with pytest.raises(HTTPException) as exc_info:
            _validate_model_routing(
                {"fallback_policy": {"model_fallback_chain": "claude-opus-4-6"}}
            )
        assert exc_info.value.status_code == 400
