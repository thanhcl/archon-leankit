#!/usr/bin/env python3
"""Generate TypeScript interfaces from Pydantic models in api_contracts.py.

Reads Pydantic BaseModel classes and str Enums, maps Python types to TypeScript,
and writes a .ts file with matching interfaces and union types.

Usage:
    python scripts/generate-ts-types.py [--output PATH] [--check]

Options:
    --output PATH   Output file path (default: archon-ui-main/src/types/api-contracts.generated.ts)
    --check         Check mode: exit 1 if generated output differs from existing file (for CI)
"""

from __future__ import annotations

import argparse
import enum
import inspect
import sys
import textwrap
from pathlib import Path
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo

# ── Bootstrap imports ─────────────────────────────────────────────────
# Add project root so we can import the models package
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "python"))

from src.server.models.api_contracts import (  # noqa: E402
    CreateProjectRequest,
    CreateRuleRequest,
    CreateTaskRequest,
    OfficeConfigListResponse,
    OfficeConfigResponse,
    ProjectListResponse,
    ProjectResponse,
    ReviewConfigRequest,
    ReviewConfigResponse,
    RuleListResponse,
    RuleResponse,
    RuleSource,
    SprintDay,
    SprintStatsResponse,
    SprintSummary,
    SprintTrends,
    TaskComplexity,
    TaskCountsListResponse,
    TaskCountsResponse,
    TaskListResponse,
    TaskPriority,
    TaskResponse,
    TaskStatus,
    TransitionResponse,
    TransitionTaskRequest,
    UpdateProjectRequest,
    UpdateRuleRequest,
    UpdateTaskRequest,
)

# ── Configuration ─────────────────────────────────────────────────────

DEFAULT_OUTPUT = PROJECT_ROOT / "archon-ui-main" / "src" / "types" / "api-contracts.generated.ts"

# Enums to export (order matters for output)
ENUMS: list[type[enum.Enum]] = [
    TaskStatus,
    TaskPriority,
    TaskComplexity,
    RuleSource,
]

# Models to export (order matters — dependencies first)
MODELS: list[type[BaseModel]] = [
    TaskResponse,
    TaskListResponse,
    CreateTaskRequest,
    UpdateTaskRequest,
    TransitionTaskRequest,
    TransitionResponse,
    ProjectResponse,
    ProjectListResponse,
    CreateProjectRequest,
    UpdateProjectRequest,
    OfficeConfigResponse,
    OfficeConfigListResponse,
    TaskCountsResponse,
    TaskCountsListResponse,
    RuleResponse,
    RuleListResponse,
    CreateRuleRequest,
    UpdateRuleRequest,
    ReviewConfigRequest,
    ReviewConfigResponse,
    SprintSummary,
    SprintDay,
    SprintTrends,
    SprintStatsResponse,
]

# ── Type Mapping ──────────────────────────────────────────────────────


def _python_type_to_ts(annotation: Any) -> str:
    """Convert a Python type annotation to a TypeScript type string."""
    if annotation is type(None):
        return "null"

    # Handle str Enums — map to their TS union type name
    if inspect.isclass(annotation) and issubclass(annotation, enum.Enum):
        return annotation.__name__

    # Primitives
    if annotation is str:
        return "string"
    if annotation is int:
        return "number"
    if annotation is float:
        return "number"
    if annotation is bool:
        return "boolean"

    # Any
    if annotation is Any:
        return "unknown"

    origin = get_origin(annotation)
    args = get_args(annotation)

    # Union (includes X | None from PEP 604)
    if origin is Union:
        ts_parts = [_python_type_to_ts(a) for a in args]
        # Collapse "T | null" into optional handling at field level
        return " | ".join(ts_parts)

    # list[X]
    if origin is list:
        if not args:
            return "unknown[]"
        inner = _python_type_to_ts(args[0])
        # Wrap complex union types in parens
        if " | " in inner:
            return f"({inner})[]"
        return f"{inner}[]"

    # dict[K, V]
    if origin is dict:
        if not args or len(args) < 2:
            return "Record<string, unknown>"
        key_type = _python_type_to_ts(args[0])
        val_type = _python_type_to_ts(args[1])
        return f"Record<{key_type}, {val_type}>"

    # tuple
    if origin is tuple:
        if not args:
            return "unknown[]"
        parts = [_python_type_to_ts(a) for a in args]
        return f"[{', '.join(parts)}]"

    # Pydantic model reference
    if inspect.isclass(annotation) and issubclass(annotation, BaseModel):
        return annotation.__name__

    # Fallback
    return "unknown"


def _is_optional(annotation: Any) -> bool:
    """Check if a type annotation is Optional (union with None)."""
    origin = get_origin(annotation)
    if origin is Union:
        return type(None) in get_args(annotation)
    return False


def _unwrap_optional(annotation: Any) -> Any:
    """Remove None from a Union type, returning the inner type."""
    origin = get_origin(annotation)
    if origin is Union:
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0]
        # Multi-type union minus None
        return Union[tuple(args)]
    return annotation


# ── Generators ────────────────────────────────────────────────────────


def generate_enum(enum_cls: type[enum.Enum]) -> str:
    """Generate a TypeScript union type from a Python str Enum."""
    values = [f'  | "{member.value}"' for member in enum_cls]
    return f"export type {enum_cls.__name__} =\n" + "\n".join(values) + ";\n"


def generate_interface(model: type[BaseModel]) -> str:
    """Generate a TypeScript interface from a Pydantic BaseModel."""
    lines = [f"export interface {model.__name__} {{"]

    for field_name, field_info in model.model_fields.items():
        annotation = field_info.annotation
        optional = _is_optional(annotation)

        if optional:
            inner_type = _unwrap_optional(annotation)
            ts_type = _python_type_to_ts(inner_type)
            # Keep null in the union for explicit null-ability
            lines.append(f"  {field_name}?: {ts_type} | null;")
        else:
            ts_type = _python_type_to_ts(annotation)
            lines.append(f"  {field_name}: {ts_type};")

    lines.append("}")
    return "\n".join(lines) + "\n"


def generate_all() -> str:
    """Generate the complete TypeScript file content."""
    parts: list[str] = []

    parts.append(
        textwrap.dedent("""\
        // ─── AUTO-GENERATED ─────────────────────────────────────────────────
        // Source: python/src/server/models/api_contracts.py
        // Generator: scripts/generate-ts-types.py
        //
        // DO NOT EDIT MANUALLY. Run `pnpm generate:types` to regenerate.
        // ─────────────────────────────────────────────────────────────────────
        """)
    )

    # Enums
    parts.append("// ── Enums ──────────────────────────────────────────────────────────\n")
    for enum_cls in ENUMS:
        parts.append(generate_enum(enum_cls))

    # Interfaces
    parts.append("// ── Interfaces ─────────────────────────────────────────────────────\n")
    for model in MODELS:
        parts.append(generate_interface(model))

    return "\n".join(parts)


# ── CLI ───────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate TypeScript types from Pydantic models")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Output .ts file path",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check mode: exit 1 if output would change (for CI)",
    )
    args = parser.parse_args()

    content = generate_all()

    if args.check:
        output_path: Path = args.output
        if not output_path.exists():
            print(f"FAIL: {output_path} does not exist. Run `pnpm generate:types` first.")
            return 1
        existing = output_path.read_text()
        if existing != content:
            print(f"FAIL: {output_path} is out of date. Run `pnpm generate:types` to regenerate.")
            return 1
        print(f"OK: {output_path} is up to date.")
        return 0

    # Write mode
    output_path = args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content)
    print(f"Generated {output_path} ({len(ENUMS)} enums, {len(MODELS)} interfaces)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
