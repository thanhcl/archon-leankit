"""Tests for the TypeScript type generation script."""

from __future__ import annotations

import enum
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

# Repo root is 3 levels up from tests/scripts/test_*.py
# python/tests/scripts/test_*.py → parent×3 = python/, parent×4 would overshoot
# .parent = scripts/, .parent.parent = tests/, .parent.parent.parent = python/
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "python"))

# Import the script module (hyphenated filename requires importlib)
import importlib.util

_spec = importlib.util.spec_from_file_location(
    "generate_ts_types",
    str(REPO_ROOT / "scripts" / "generate-ts-types.py"),
)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

ENUMS = _mod.ENUMS
MODELS = _mod.MODELS
generate_all = _mod.generate_all
generate_enum = _mod.generate_enum
generate_interface = _mod.generate_interface
_python_type_to_ts = _mod._python_type_to_ts
_is_optional = _mod._is_optional
_find_diverged_type_names = _mod._find_diverged_type_names
from src.server.models.api_contracts import (
    CurrentVersionResponse,
    CreateExecutionRunRequest,
    MigrationHistoryResponse,
    MigrationStatusResponse,
    PendingMigration,
    ReleaseAsset,
    TaskStatus,
    TaskPriority,
    TaskComplexity,
    TaskType,
    ExecutionRunStatus,
    ExecutionRunStage,
    RuleSource,
    ExecutionRunResponse,
    TaskResponse,
    CreateTaskRequest,
    UpdateTaskRequest,
    ProjectResponse,
    SprintStatsResponse,
    VersionCacheClearResponse,
    VersionCheckResponse,
)


# ── Type mapping tests ────────────────────────────────────────────────


class TestPythonTypeToTs:
    def test_str(self) -> None:
        assert _python_type_to_ts(str) == "string"

    def test_int(self) -> None:
        assert _python_type_to_ts(int) == "number"

    def test_float(self) -> None:
        assert _python_type_to_ts(float) == "number"

    def test_bool(self) -> None:
        assert _python_type_to_ts(bool) == "boolean"

    def test_datetime(self) -> None:
        assert _python_type_to_ts(datetime) == "string"

    def test_any(self) -> None:
        assert _python_type_to_ts(Any) == "unknown"

    def test_none_type(self) -> None:
        assert _python_type_to_ts(type(None)) == "null"

    def test_list_str(self) -> None:
        assert _python_type_to_ts(list[str]) == "string[]"

    def test_list_any(self) -> None:
        assert _python_type_to_ts(list[Any]) == "unknown[]"

    def test_dict_str_any(self) -> None:
        assert _python_type_to_ts(dict[str, Any]) == "Record<string, unknown>"

    def test_dict_str_int(self) -> None:
        assert _python_type_to_ts(dict[str, int]) == "Record<string, number>"

    def test_optional_str(self) -> None:
        result = _python_type_to_ts(str | None)
        assert "string" in result
        assert "null" in result

    def test_enum_type(self) -> None:
        assert _python_type_to_ts(TaskStatus) == "TaskStatus"
        assert _python_type_to_ts(TaskPriority) == "TaskPriority"
        assert _python_type_to_ts(ExecutionRunStatus) == "ExecutionRunStatus"

    def test_list_dict(self) -> None:
        result = _python_type_to_ts(list[dict[str, Any]])
        assert result == "Record<string, unknown>[]"

    def test_pydantic_model_reference(self) -> None:
        assert _python_type_to_ts(TaskResponse) == "TaskResponse"


class TestIsOptional:
    def test_optional_str(self) -> None:
        assert _is_optional(str | None) is True

    def test_plain_str(self) -> None:
        assert _is_optional(str) is False

    def test_optional_int(self) -> None:
        assert _is_optional(int | None) is True


# ── Enum generation tests ────────────────────────────────────────────


class TestGenerateEnum:
    def test_task_status_has_15_values(self) -> None:
        result = generate_enum(TaskStatus)
        assert result.startswith("export type TaskStatus =")
        # Count the union members
        assert result.count('| "') == 15

    def test_task_status_values(self) -> None:
        result = generate_enum(TaskStatus)
        for member in TaskStatus:
            assert f'"{member.value}"' in result

    def test_task_priority_values(self) -> None:
        result = generate_enum(TaskPriority)
        assert '"low"' in result
        assert '"medium"' in result
        assert '"high"' in result
        assert '"critical"' in result

    def test_task_complexity_values(self) -> None:
        result = generate_enum(TaskComplexity)
        assert '"simple"' in result
        assert '"complex"' in result

    def test_execution_run_status_values(self) -> None:
        result = generate_enum(ExecutionRunStatus)
        assert '"queued"' in result
        assert '"completed"' in result

    def test_execution_run_stage_values(self) -> None:
        result = generate_enum(ExecutionRunStage)
        assert '"execute"' in result
        assert '"code-review"' in result

    def test_rule_source_values(self) -> None:
        result = generate_enum(RuleSource)
        assert '"manual"' in result
        assert '"auto"' in result
        assert '"system"' in result

    def test_all_enums_registered(self) -> None:
        assert len(ENUMS) == 13
        assert TaskStatus in ENUMS
        assert TaskPriority in ENUMS
        assert TaskComplexity in ENUMS
        assert TaskType in ENUMS
        assert ExecutionRunStatus in ENUMS
        assert ExecutionRunStage in ENUMS
        assert RuleSource in ENUMS


# ── Interface generation tests ────────────────────────────────────────


class TestGenerateInterface:
    def test_task_response_has_all_fields(self) -> None:
        result = generate_interface(TaskResponse)
        assert "export interface TaskResponse {" in result
        assert "id: string;" in result
        assert "status: TaskStatus;" in result
        assert "priority: TaskPriority;" in result
        assert "complexity: TaskComplexity;" in result
        assert "retry_count: number;" in result
        assert "archived: boolean;" in result

    def test_execution_run_response_has_fields(self) -> None:
        result = generate_interface(ExecutionRunResponse)
        assert "export interface ExecutionRunResponse {" in result
        assert "status: ExecutionRunStatus;" in result
        assert "stage: ExecutionRunStage;" in result
        assert "task_id: string;" in result

    def test_optional_fields_use_question_mark(self) -> None:
        result = generate_interface(TaskResponse)
        assert "feature?: string | null;" in result
        assert "owner?: string | null;" in result
        assert "execution_prompt?: string | null;" in result

    def test_update_request_all_optional(self) -> None:
        result = generate_interface(UpdateTaskRequest)
        lines = [l.strip() for l in result.split("\n") if l.strip() and not l.strip().startswith(("export", "}"))]
        for line in lines:
            assert "?:" in line, f"Expected optional field: {line}"

    def test_list_types(self) -> None:
        result = generate_interface(TaskResponse)
        assert "sources?: Record<string, unknown>[] | null;" in result

    def test_nested_model_reference(self) -> None:
        result = generate_interface(SprintStatsResponse)
        assert "summary: SprintSummary;" in result
        assert "sprints: SprintDay[];" in result
        assert "trends: SprintTrends;" in result

    def test_all_models_registered(self) -> None:
        assert len(MODELS) == 53
        assert MODELS[0].__name__ == "TaskRepoGuidancePack"
        assert ReleaseAsset in MODELS
        assert VersionCheckResponse in MODELS
        assert CurrentVersionResponse in MODELS
        assert VersionCacheClearResponse in MODELS
        assert PendingMigration in MODELS
        assert MigrationStatusResponse in MODELS
        assert MigrationHistoryResponse in MODELS


# ── Full output tests ─────────────────────────────────────────────────


class TestGenerateAll:
    def test_header_present(self) -> None:
        result = generate_all()
        assert "AUTO-GENERATED" in result
        assert "DO NOT EDIT MANUALLY" in result
        assert "pnpm generate:types" in result

    def test_all_enums_present(self) -> None:
        result = generate_all()
        assert "export type TaskStatus =" in result
        assert "export type TaskPriority =" in result
        assert "export type TaskComplexity =" in result
        assert "export type ExecutionRunStatus =" in result
        assert "export type ExecutionRunStage =" in result
        assert "export type RuleSource =" in result

    def test_all_interfaces_present(self) -> None:
        result = generate_all()
        for model in MODELS:
            assert f"export interface {model.__name__}" in result

    def test_enums_before_interfaces(self) -> None:
        result = generate_all()
        enum_pos = result.index("// ── Enums")
        iface_pos = result.index("// ── Interfaces")
        assert enum_pos < iface_pos

    def test_idempotent(self) -> None:
        """Running generate_all twice produces identical output."""
        assert generate_all() == generate_all()


# ── CI check mode test ────────────────────────────────────────────────


class TestCheckMode:
    def test_check_passes_when_up_to_date(self, tmp_path: Path) -> None:
        output = tmp_path / "types.ts"
        output.write_text(generate_all())

        main = _mod.main

        sys.argv = ["generate-ts-types.py", "--check", "--output", str(output)]
        assert main() == 0

    def test_check_fails_when_outdated(self, tmp_path: Path) -> None:
        output = tmp_path / "types.ts"
        output.write_text("// stale content")

        main = _mod.main

        sys.argv = ["generate-ts-types.py", "--check", "--output", str(output)]
        assert main() == 1

    def test_check_fails_when_missing(self, tmp_path: Path) -> None:
        output = tmp_path / "nonexistent.ts"

        main = _mod.main

        sys.argv = ["generate-ts-types.py", "--check", "--output", str(output)]
        assert main() == 1

    def test_check_shows_diverged_type_names(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        # Write stale content with a modified type
        stale = generate_all().replace(
            "export interface TaskResponse {",
            "export interface TaskResponse {\n  _stale_field: string;",
        )
        output = tmp_path / "types.ts"
        output.write_text(stale)

        sys.argv = ["generate-ts-types.py", "--check", "--output", str(output)]
        assert _mod.main() == 1

        captured = capsys.readouterr()
        assert "TaskResponse" in captured.out
        assert "Diverged types" in captured.out


# ── Diverged type detection tests ─────────────────────────────────────


class TestFindDivergedTypeNames:
    def test_no_drift_returns_empty(self) -> None:
        content = generate_all()
        assert _find_diverged_type_names(content, content) == []

    def test_detects_modified_interface(self) -> None:
        original = generate_all()
        modified = original.replace(
            "export interface TaskResponse {",
            "export interface TaskResponse {\n  extra_field: string;",
        )
        diverged = _find_diverged_type_names(original, modified)
        assert "TaskResponse" in diverged

    def test_detects_modified_enum(self) -> None:
        original = generate_all()
        modified = original.replace(
            '"draft"',
            '"draft"\n  | "new_status"',
        )
        diverged = _find_diverged_type_names(original, modified)
        assert "TaskStatus" in diverged

    def test_unrelated_types_not_included(self) -> None:
        original = generate_all()
        modified = original.replace(
            "export interface TaskResponse {",
            "export interface TaskResponse {\n  extra_field: string;",
        )
        diverged = _find_diverged_type_names(original, modified)
        # Other types that weren't changed should not appear
        assert "ProjectResponse" not in diverged
        assert "TaskPriority" not in diverged

    def test_detects_multiple_changed_types(self) -> None:
        original = generate_all()
        modified = (
            original
            .replace("export interface TaskResponse {", "export interface TaskResponse {\n  a: string;")
            .replace("export interface ProjectResponse {", "export interface ProjectResponse {\n  b: string;")
        )
        diverged = _find_diverged_type_names(original, modified)
        assert "TaskResponse" in diverged
        assert "ProjectResponse" in diverged
