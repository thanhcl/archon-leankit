"""
MCP Tool Count Audit Script

Counts all MCP tools registered by the Archon MCP server by parsing tool modules
via AST (no heavy imports required). For each tool, outputs the name and estimated
description token cost (len(docstring) / 4). Flags if total count > 80 or total
token cost > 30K.

Usage:
    python scripts/mcp_tool_audit.py
    # or from repo root:
    uv run python scripts/mcp_tool_audit.py
"""

import ast
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
MCP_FEATURES_DIR = REPO_ROOT / "python" / "src" / "mcp_server" / "features"
MCP_SERVER_FILE = REPO_ROOT / "python" / "src" / "mcp_server" / "mcp_server.py"

# Thresholds from CI-4 spec
TOOL_COUNT_THRESHOLD = 80
TOKEN_COST_THRESHOLD = 30_000

# Column widths for formatted output
COL_MODULE = 24
COL_TOOL = 40
COL_TOKENS = 10


# ---------------------------------------------------------------------------
# AST-based tool extraction
# ---------------------------------------------------------------------------

def extract_tools_from_file(filepath: Path) -> list[dict]:
    """
    Parse a Python source file with AST and extract all functions decorated
    with @mcp.tool(). Returns a list of dicts with 'name', 'docstring', and
    'token_cost'.
    """
    source = filepath.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError as exc:
        print(f"  [WARN] SyntaxError in {filepath}: {exc}", file=sys.stderr)
        return []

    tools: list[dict] = []

    for node in ast.walk(tree):
        # Tools can be top-level or nested inside a register_*_tools() function
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        # Check if any decorator is @mcp.tool() or @mcp.tool
        for decorator in node.decorator_list:
            is_mcp_tool = False

            # @mcp.tool()  → Call node whose func is Attribute mcp.tool
            if isinstance(decorator, ast.Call):
                func = decorator.func
                if isinstance(func, ast.Attribute) and func.attr == "tool":
                    if isinstance(func.value, ast.Name) and func.value.id == "mcp":
                        is_mcp_tool = True

            # @mcp.tool  (no parentheses, rare but handle it)
            elif isinstance(decorator, ast.Attribute):
                if decorator.attr == "tool":
                    if isinstance(decorator.value, ast.Name) and decorator.value.id == "mcp":
                        is_mcp_tool = True

            if is_mcp_tool:
                docstring = ast.get_docstring(node) or ""
                token_cost = len(docstring) // 4
                tools.append(
                    {
                        "name": node.name,
                        "docstring": docstring,
                        "token_cost": token_cost,
                    }
                )
                break  # Only count each function once

    return tools


def collect_all_tools() -> list[dict]:
    """
    Walk the features directory and the main mcp_server.py to collect every
    registered MCP tool. Returns list of dicts with 'module', 'name',
    'docstring', and 'token_cost'.
    """
    all_tools: list[dict] = []

    # Scan all .py files under features/
    for py_file in sorted(MCP_FEATURES_DIR.rglob("*.py")):
        if py_file.name == "__init__.py":
            continue
        relative = py_file.relative_to(REPO_ROOT / "python")
        module_label = str(relative).replace("/", ".").removesuffix(".py")
        tools = extract_tools_from_file(py_file)
        for tool in tools:
            tool["module"] = module_label
            all_tools.append(tool)

    # Also scan mcp_server.py for top-level @mcp.tool() definitions
    # (e.g., health_check and session_info)
    if MCP_SERVER_FILE.exists():
        relative = MCP_SERVER_FILE.relative_to(REPO_ROOT / "python")
        module_label = str(relative).replace("/", ".").removesuffix(".py")
        tools = extract_tools_from_file(MCP_SERVER_FILE)
        for tool in tools:
            tool["module"] = module_label
            all_tools.append(tool)

    return all_tools


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_separator(char: str = "-", width: int = 82) -> None:
    print(char * width)


def run_audit() -> int:
    """
    Run the MCP tool audit. Returns exit code: 0 = pass, 1 = thresholds exceeded.
    """
    tools = collect_all_tools()

    if not tools:
        print("[ERROR] No MCP tools found. Check that the features directory exists.")
        return 1

    # Sort by module then tool name for readability
    tools.sort(key=lambda t: (t["module"], t["name"]))

    # Header
    print()
    print_separator("=")
    print("  Archon MCP Tool Audit")
    print_separator("=")
    print(
        f"  {'MODULE':<{COL_MODULE}} {'TOOL':<{COL_TOOL}} {'TOKENS':>{COL_TOKENS}}"
    )
    print_separator()

    # Per-tool rows
    total_tokens = 0
    for tool in tools:
        module_short = tool["module"].split(".")[-2] if "." in tool["module"] else tool["module"]
        # Show module as last two path segments for readability
        parts = tool["module"].split(".")
        module_display = ".".join(parts[-2:]) if len(parts) >= 2 else tool["module"]
        print(
            f"  {module_display:<{COL_MODULE}} {tool['name']:<{COL_TOOL}} {tool['token_cost']:>{COL_TOKENS}}"
        )
        total_tokens += tool["token_cost"]

    print_separator()

    total_count = len(tools)
    print(f"  Total tools  : {total_count}")
    print(f"  Total tokens : {total_tokens:,}  (approx; docstring chars / 4)")
    print_separator("=")

    # Threshold checks
    violations: list[str] = []
    if total_count > TOOL_COUNT_THRESHOLD:
        violations.append(
            f"  [FLAG] Tool count {total_count} exceeds threshold of {TOOL_COUNT_THRESHOLD}"
        )
    if total_tokens > TOKEN_COST_THRESHOLD:
        violations.append(
            f"  [FLAG] Token cost {total_tokens:,} exceeds threshold of {TOKEN_COST_THRESHOLD:,}"
        )

    if violations:
        print()
        print("  THRESHOLD VIOLATIONS DETECTED:")
        for v in violations:
            print(v)
        print_separator("=")
        print()
        return 1

    print(
        f"  [OK] Count {total_count} <= {TOOL_COUNT_THRESHOLD}  |  "
        f"Tokens {total_tokens:,} <= {TOKEN_COST_THRESHOLD:,}"
    )
    print_separator("=")
    print()
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sys.exit(run_audit())
