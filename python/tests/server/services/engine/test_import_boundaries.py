"""
Import boundary enforcement tests for the engine package.

Package boundary rules
======================
The engine package (``server/services/engine/``) is the task-execution runtime.
It must remain decoupled from control-plane code so it can be ported, tested,
and run independently.

Forbidden imports for ALL engine modules
-----------------------------------------
- ``server.api_routes``   – HTTP request-handling layer
- ``server.mcp_server``   – MCP IDE-integration layer
- ``archon-ui-main``      – Frontend source (should never be imported)

Forbidden imports for core engine modules (all except ``notifier.py``)
-----------------------------------------------------------------------
- ``server.services.channels`` – Notification channel adapters

Rationale: ``notifier.py`` is the designated bridge between engine lifecycle
events and outbound notification channels.  All other engine modules must stay
isolated from the channel layer so they remain portable.

Runner-adapter / cc_spawner specific rule
------------------------------------------
These modules implement the low-level runner boundary and must not import any
route or channel code, reinforcing portability.
"""

import ast
import pathlib
from typing import NamedTuple

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ENGINE_DIR = (
    pathlib.Path(__file__).parent.parent.parent.parent.parent
    / "src" / "server" / "services" / "engine"
)


class ImportViolation(NamedTuple):
    file: str
    line: int
    statement: str


def _collect_imports(source: str) -> list[tuple[int, str]]:
    """Return list of (lineno, dotted-module-name) for every import in *source*."""
    tree = ast.parse(source)
    imports: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            # module can be None for relative imports without a base
            module = node.module or ""
            # Reconstruct absolute-ish name: prepend dots for relative imports
            relative = "." * (node.level or 0)
            imports.append((node.lineno, f"{relative}{module}"))
    return imports


def _violations_for_file(
    py_file: pathlib.Path,
    forbidden_prefixes: list[str],
) -> list[ImportViolation]:
    """Return all import violations in *py_file* for the given forbidden prefixes."""
    source = py_file.read_text()
    violations: list[ImportViolation] = []
    for lineno, module_name in _collect_imports(source):
        for prefix in forbidden_prefixes:
            if module_name == prefix or module_name.startswith(prefix + ".") or module_name.startswith(prefix + "/"):
                violations.append(
                    ImportViolation(
                        file=py_file.name,
                        line=lineno,
                        statement=module_name,
                    )
                )
                break
    return violations


def _engine_files() -> list[pathlib.Path]:
    return [
        f for f in ENGINE_DIR.glob("*.py")
        if f.name != "__init__.py"
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_engine_dir_exists() -> None:
    """Guard: engine directory must exist before boundary checks run."""
    assert ENGINE_DIR.is_dir(), f"Expected engine directory at {ENGINE_DIR}"


@pytest.mark.parametrize("py_file", _engine_files(), ids=lambda p: p.name)
def test_engine_does_not_import_api_routes(py_file: pathlib.Path) -> None:
    """No engine module may import from the HTTP route layer."""
    forbidden = [
        "server.api_routes",
        "src.server.api_routes",
        "api_routes",
    ]
    violations = _violations_for_file(py_file, forbidden)
    assert not violations, (
        f"{py_file.name} imports from api_routes (control-plane boundary violation):\n"
        + "\n".join(f"  line {v.line}: {v.statement}" for v in violations)
    )


@pytest.mark.parametrize("py_file", _engine_files(), ids=lambda p: p.name)
def test_engine_does_not_import_mcp_server(py_file: pathlib.Path) -> None:
    """No engine module may import from the MCP server layer."""
    forbidden = [
        "mcp_server",
        "src.mcp_server",
        "server.mcp_server",
    ]
    violations = _violations_for_file(py_file, forbidden)
    assert not violations, (
        f"{py_file.name} imports from mcp_server (boundary violation):\n"
        + "\n".join(f"  line {v.line}: {v.statement}" for v in violations)
    )


@pytest.mark.parametrize(
    "py_file",
    [f for f in _engine_files() if f.name != "notifier.py"],
    ids=lambda p: p.name,
)
def test_core_engine_does_not_import_channels(py_file: pathlib.Path) -> None:
    """Core engine modules must not import from the channels layer.

    ``notifier.py`` is explicitly excluded — it is the designated bridge
    between engine lifecycle events and outbound notification channels.
    """
    forbidden = [
        "server.services.channels",
        "src.server.services.channels",
        "..channels",
        ".channels",
    ]
    violations = _violations_for_file(py_file, forbidden)
    assert not violations, (
        f"{py_file.name} imports from channels (only notifier.py is permitted to do this):\n"
        + "\n".join(f"  line {v.line}: {v.statement}" for v in violations)
    )


def test_runner_adapter_does_not_import_api_routes() -> None:
    """runner_adapter.py must not import from api_routes."""
    py_file = ENGINE_DIR / "runner_adapter.py"
    assert py_file.exists(), f"runner_adapter.py not found at {py_file}"
    forbidden = ["server.api_routes", "src.server.api_routes", "api_routes"]
    violations = _violations_for_file(py_file, forbidden)
    assert not violations, (
        "runner_adapter.py imports from api_routes:\n"
        + "\n".join(f"  line {v.line}: {v.statement}" for v in violations)
    )


def test_cc_spawner_does_not_import_api_routes() -> None:
    """cc_spawner.py must not import from api_routes."""
    py_file = ENGINE_DIR / "cc_spawner.py"
    assert py_file.exists(), f"cc_spawner.py not found at {py_file}"
    forbidden = ["server.api_routes", "src.server.api_routes", "api_routes"]
    violations = _violations_for_file(py_file, forbidden)
    assert not violations, (
        "cc_spawner.py imports from api_routes:\n"
        + "\n".join(f"  line {v.line}: {v.statement}" for v in violations)
    )
