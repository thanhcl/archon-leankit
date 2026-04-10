"""
RTK (Rust Token Killer) integration for LeanKit execution engine.

Provides transparent shell command output compression for Claude Code
sessions, achieving 60-90% token reduction on shell command outputs.

RTK intercepts Bash tool calls via PreToolUse hooks and rewrites commands
(e.g., ``git status`` → ``rtk git status``). The filtered output preserves
essential information while stripping noise (progress bars, passing tests,
verbose git output, etc.).

**Limitation:** RTK only intercepts explicit Bash tool calls. Claude Code's
built-in tools (Read, Grep, Glob) bypass hooks entirely.

Architecture boundary:
- This module owns RTK lifecycle (setup, teardown, analytics collection)
- It does NOT own the runner subprocess — CCSpawner owns that
- It does NOT modify the execution prompt — PromptBuilder owns that

See: docs/design/what-we-adopt-from-rtk.md
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...config.logfire_config import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Environment variable helpers
# ---------------------------------------------------------------------------

def is_rtk_enabled() -> bool:
    """Check if RTK integration is enabled via environment variable."""
    return os.environ.get("LEANKIT_RTK_ENABLED", "").strip().lower() in {
        "1", "true", "yes",
    }


def get_rtk_binary() -> str | None:
    """Resolve the RTK binary path.

    Checks LEANKIT_RTK_BINARY_PATH first, then falls back to ``rtk``
    on PATH via shutil.which().
    """
    explicit = os.environ.get("LEANKIT_RTK_BINARY_PATH", "").strip()
    if explicit:
        return explicit if os.path.isfile(explicit) else None
    return shutil.which("rtk")


def get_rtk_config_path() -> str | None:
    """Resolve the optional RTK config.toml override path."""
    path = os.environ.get("LEANKIT_RTK_CONFIG_PATH", "").strip()
    return path if path else None


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class RTKSetupResult:
    """Result of RTK workspace setup."""

    enabled: bool
    binary_path: str | None = None
    hook_installed: bool = False
    tee_dir: str | None = None
    error: str | None = None


@dataclass
class RTKSessionStats:
    """Token savings statistics from an RTK session."""

    total_commands: int = 0
    filtered_commands: int = 0
    tokens_before: int = 0
    tokens_after: int = 0
    savings_pct: float = 0.0
    command_breakdown: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize for execution-run metadata JSONB."""
        result: dict[str, Any] = {
            "total_commands": self.total_commands,
            "filtered_commands": self.filtered_commands,
            "tokens_before": self.tokens_before,
            "tokens_after": self.tokens_after,
            "savings_pct": round(self.savings_pct, 1),
        }
        if self.command_breakdown:
            result["command_breakdown"] = self.command_breakdown
        return result


# ---------------------------------------------------------------------------
# RTK workspace setup
# ---------------------------------------------------------------------------

async def setup_rtk_for_workspace(workspace_path: str) -> RTKSetupResult:
    """Set up RTK for a workspace before spawning a CC session.

    Steps:
    1. Check RTK is enabled and binary is available
    2. Create tee directory inside workspace for failure recovery
    3. Run ``rtk init -g --auto-patch`` to install PreToolUse hook

    The hook rewrites Bash commands transparently so the CC session
    benefits from output compression without any prompt changes.

    Args:
        workspace_path: The working directory for the CC session.

    Returns:
        RTKSetupResult with setup status and paths.
    """
    if not is_rtk_enabled():
        return RTKSetupResult(enabled=False)

    binary = get_rtk_binary()
    if not binary:
        logger.warning("RTK enabled but binary not found on PATH")
        return RTKSetupResult(
            enabled=False,
            error="RTK binary not found. Install with: brew install rtk-ai/tap/rtk",
        )

    # Create tee directory for full output recovery on failures (B-2)
    tee_dir = os.path.join(workspace_path, ".rtk-tee")
    try:
        os.makedirs(tee_dir, exist_ok=True)
    except OSError as exc:
        logger.warning(f"Failed to create RTK tee dir | path={tee_dir} | error={exc}")
        tee_dir = None

    # Install RTK hook for this session.
    # Use ``rtk init -g --auto-patch`` for global hook installation.
    # The --auto-patch flag ensures non-interactive operation.
    hook_installed = False
    try:
        proc = await asyncio.create_subprocess_exec(
            binary, "init", "-g", "--auto-patch",
            cwd=workspace_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

        if proc.returncode == 0:
            hook_installed = True
            logger.info(
                f"RTK hook installed | workspace={workspace_path} | "
                f"binary={binary}"
            )
        else:
            stderr_text = stderr.decode().strip() if stderr else "unknown"
            logger.warning(
                f"RTK init failed | exit_code={proc.returncode} | "
                f"stderr={stderr_text[:200]}"
            )
    except TimeoutError:
        logger.warning("RTK init timed out (30s)")
    except FileNotFoundError:
        logger.warning(f"RTK binary not executable | path={binary}")
    except Exception as exc:
        logger.warning(f"RTK init error | error={exc}")

    return RTKSetupResult(
        enabled=True,
        binary_path=binary,
        hook_installed=hook_installed,
        tee_dir=tee_dir,
    )


# ---------------------------------------------------------------------------
# RTK analytics collection (A-2, B-3)
# ---------------------------------------------------------------------------

async def collect_rtk_analytics(binary_path: str | None = None) -> RTKSessionStats | None:
    """Collect token savings analytics from the most recent RTK session.

    Calls ``rtk gain --format json --session latest`` and parses the output
    into an RTKSessionStats object suitable for execution-run metadata.

    Args:
        binary_path: Path to RTK binary. If None, resolves from env/PATH.

    Returns:
        RTKSessionStats if collection succeeds, None otherwise.
    """
    binary = binary_path or get_rtk_binary()
    if not binary:
        return None

    try:
        proc = await asyncio.create_subprocess_exec(
            binary, "gain", "--format", "json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)

        if proc.returncode != 0:
            logger.debug(
                f"RTK gain failed | exit_code={proc.returncode} | "
                f"stderr={stderr.decode()[:200] if stderr else 'none'}"
            )
            return None

        if not stdout:
            return None

        data = json.loads(stdout.decode())
        return _parse_rtk_gain_output(data)

    except TimeoutError:
        logger.debug("RTK gain timed out (15s)")
        return None
    except json.JSONDecodeError as exc:
        logger.debug(f"RTK gain JSON parse error | error={exc}")
        return None
    except Exception as exc:
        logger.debug(f"RTK gain error | error={exc}")
        return None


def _parse_rtk_gain_output(data: dict[str, Any] | list[Any]) -> RTKSessionStats:
    """Parse the JSON output from ``rtk gain --format json``.

    RTK's JSON output structure may vary by version. This parser handles
    both summary-level and per-command breakdown formats gracefully.
    """
    # Handle both dict and list formats
    if isinstance(data, list):
        # Per-command breakdown format
        total_before = 0
        total_after = 0
        breakdown: list[dict[str, Any]] = []
        for entry in data:
            if not isinstance(entry, dict):
                continue
            before = entry.get("input_tokens", 0) or entry.get("tokens_before", 0)
            after = entry.get("output_tokens", 0) or entry.get("tokens_after", 0)
            total_before += before
            total_after += after
            cmd = entry.get("original_cmd") or entry.get("command") or entry.get("rtk_cmd", "")
            if cmd:
                breakdown.append({
                    "command": cmd[:80],
                    "tokens_before": before,
                    "tokens_after": after,
                    "savings_pct": round((1 - after / before) * 100, 1) if before > 0 else 0,
                })

        savings_pct = (1 - total_after / total_before) * 100 if total_before > 0 else 0
        return RTKSessionStats(
            total_commands=len(data),
            filtered_commands=len([e for e in data if isinstance(e, dict)]),
            tokens_before=total_before,
            tokens_after=total_after,
            savings_pct=savings_pct,
            command_breakdown=breakdown[:20] if breakdown else None,
        )

    # Summary format (dict)
    return RTKSessionStats(
        total_commands=data.get("total_commands", 0),
        filtered_commands=data.get("filtered_commands", data.get("total_commands", 0)),
        tokens_before=data.get("tokens_before", data.get("input_tokens", 0)),
        tokens_after=data.get("tokens_after", data.get("output_tokens", 0)),
        savings_pct=data.get("savings_pct", data.get("savings_percent", 0)),
        command_breakdown=data.get("commands") or data.get("command_breakdown"),
    )


# ---------------------------------------------------------------------------
# RTK tee recovery (B-2)
# ---------------------------------------------------------------------------

def get_tee_recovery_path(tee_dir: str | None) -> str | None:
    """Return the tee directory path if it contains recovery files.

    Used on run failure to record the path in execution-run metadata
    so operators can retrieve full unfiltered output for debugging.
    """
    if not tee_dir or not os.path.isdir(tee_dir):
        return None

    try:
        entries = os.listdir(tee_dir)
        if entries:
            return tee_dir
    except OSError:
        pass

    return None


def cleanup_tee(tee_dir: str | None, keep_on_failure: bool = True, run_success: bool = True) -> None:
    """Clean up RTK tee directory after run completion.

    Args:
        tee_dir: Path to the .rtk-tee directory.
        keep_on_failure: When True, retain tee files for failed runs.
        run_success: Whether the run succeeded.
    """
    if not tee_dir or not os.path.isdir(tee_dir):
        return

    if not run_success and keep_on_failure:
        logger.debug(f"RTK tee retained for failed run | path={tee_dir}")
        return

    try:
        shutil.rmtree(tee_dir, ignore_errors=True)
        logger.debug(f"RTK tee cleaned up | path={tee_dir}")
    except OSError as exc:
        logger.debug(f"RTK tee cleanup error | path={tee_dir} | error={exc}")


# ---------------------------------------------------------------------------
# RTK environment injection
# ---------------------------------------------------------------------------

def get_rtk_spawn_env() -> dict[str, str]:
    """Build RTK-specific environment variables for the runner subprocess.

    These are injected into the CC subprocess environment alongside
    token profile vars (CI-1) and API key (CI-8).
    """
    env: dict[str, str] = {}

    config_path = get_rtk_config_path()
    if config_path:
        env["RTK_CONFIG_PATH"] = config_path

    # Ensure RTK tracking is enabled for analytics collection
    env["RTK_TRACKING"] = "true"

    return env
