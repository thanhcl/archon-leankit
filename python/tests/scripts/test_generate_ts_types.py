"""Tests for the TypeScript type generation script."""

from __future__ import annotations

import enum
import sys
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
from src.server.models.api_contracts import (
    TaskStatus,
    TaskPriority,
    TaskComplexity,
    RuleSource,
    TaskResponse,
    CreateTaskRequest,
    UpdateTaskRequest,
    ProjectResponse,
    SprintStatsResponse,
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
    def test_task_status_has_14_values(self) -> None:
        result = generate_enum(TaskStatus)
        assert result.startswith("export type TaskStatus =")
        # Count the union members
        assert result.count('| "') == 14

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

    def test_rule_source_values(self) -> None:
        result = generate_enum(RuleSource)
        assert '"manual"' in result
        assert '"auto"' in result
        assert '"system"' in result

    def test_all_enums_registered(self) -> None:
        assert len(ENUMS) == 4
        assert TaskStatus in ENUMS
        assert TaskPriority in ENUMS
        assert TaskComplexity in ENUMS
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
        assert len(MODELS) == 24


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
