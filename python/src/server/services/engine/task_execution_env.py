"""
Per-task execution environment for isolated task execution.

Creates a structured directory for each task with work/, output/, logs/,
and context/ subdirectories. Provider-aware context injection ensures
runners receive correct instruction files (CLAUDE.md for Claude Code,
different format for Codex).

Adopted from Multica execenv/ isolation pattern (Workstream M-P2-02).
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...config.logfire_config import get_logger

logger = get_logger(__name__)

DEFAULT_EXEC_ENV_BASE = ".leankit-exec-envs"


class TaskExecutionEnv:
    """Manages per-task isolated execution environments.

    Directory structure per task:
        {base}/{task_id}/
            work/       — Agent working directory (where execution happens)
            output/     — Preserved results after completion
            logs/       — Preserved execution logs
            context/    — Injected context files (CLAUDE.md, skills, etc.)
    """

    def __init__(self, base_dir: str = DEFAULT_EXEC_ENV_BASE) -> None:
        self.base_dir = base_dir
        self._active_envs: dict[str, Path] = {}

    def _task_root(self, project_path: str, task_id: str) -> Path:
        return Path(project_path) / self.base_dir / task_id

    def create(
        self,
        task_id: str,
        project_path: str,
        runner_key: str = "claude-code-cli",
        task_context: dict[str, Any] | None = None,
        skills: list[dict[str, Any]] | None = None,
        guidance_packs: list[dict[str, Any]] | None = None,
    ) -> dict[str, str]:
        """Create an isolated execution environment for a task.

        Returns dict with paths: root, work, output, logs, context.
        """
        root = self._task_root(project_path, task_id)

        # Create directory structure
        work_dir = root / "work"
        output_dir = root / "output"
        logs_dir = root / "logs"
        context_dir = root / "context"

        for d in (work_dir, output_dir, logs_dir, context_dir):
            d.mkdir(parents=True, exist_ok=True)

        # Inject provider-aware context files
        self._inject_context(
            context_dir=context_dir,
            runner_key=runner_key,
            task_id=task_id,
            task_context=task_context,
            skills=skills,
            guidance_packs=guidance_packs,
        )

        self._active_envs[task_id] = root

        logger.info(f"Execution environment created: task_id={task_id} runner_key={runner_key} root={root}")

        return {
            "root": str(root),
            "work": str(work_dir),
            "output": str(output_dir),
            "logs": str(logs_dir),
            "context": str(context_dir),
        }

    def _inject_context(
        self,
        context_dir: Path,
        runner_key: str,
        task_id: str,
        task_context: dict[str, Any] | None = None,
        skills: list[dict[str, Any]] | None = None,
        guidance_packs: list[dict[str, Any]] | None = None,
    ) -> None:
        """Inject provider-aware context files into the execution environment."""

        # Task context file (shared across all runners)
        if task_context:
            issue_context = context_dir / "issue_context.md"
            issue_context.write_text(
                self._format_task_context(task_id, task_context),
                encoding="utf-8",
            )

        # Provider-specific instruction files
        if runner_key == "claude-code-cli":
            self._inject_claude_context(context_dir, task_id, task_context, skills, guidance_packs)
        elif runner_key == "codex-cli":
            self._inject_codex_context(context_dir, task_id, task_context, skills)
        else:
            # Generic runner — write a simple instructions file
            self._inject_generic_context(context_dir, task_id, task_context, skills)

    def _inject_claude_context(
        self,
        context_dir: Path,
        task_id: str,
        task_context: dict[str, Any] | None,
        skills: list[dict[str, Any]] | None,
        guidance_packs: list[dict[str, Any]] | None,
    ) -> None:
        """Inject CLAUDE.md and related files for Claude Code CLI."""
        lines = [
            "# Task Execution Context",
            "",
            f"Task ID: {task_id}",
            f"Generated at: {datetime.now(timezone.utc).isoformat()}",
            "",
        ]

        if task_context:
            title = task_context.get("title", "Untitled")
            description = task_context.get("description", "")
            prompt = task_context.get("execution_prompt", "")
            lines.extend([
                f"## Task: {title}",
                "",
                description or "",
                "",
            ])
            if prompt:
                lines.extend(["## Execution Instructions", "", prompt, ""])

        # Inject guidance packs
        if guidance_packs:
            lines.extend(["## Repo Guidance", ""])
            for pack in guidance_packs:
                content = pack.get("content", "")
                if content:
                    lines.extend([content, ""])

        # Inject skills
        if skills:
            lines.extend(["## Skills", ""])
            for skill in skills:
                name = skill.get("name", "unnamed")
                content = skill.get("content", "")
                lines.extend([f"### {name}", "", content, ""])

        claude_md = context_dir / "CLAUDE.md"
        claude_md.write_text("\n".join(lines), encoding="utf-8")

    def _inject_codex_context(
        self,
        context_dir: Path,
        task_id: str,
        task_context: dict[str, Any] | None,
        skills: list[dict[str, Any]] | None,
    ) -> None:
        """Inject instruction files for Codex CLI."""
        instructions = {
            "task_id": task_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        if task_context:
            instructions["title"] = task_context.get("title", "")
            instructions["description"] = task_context.get("description", "")
            instructions["execution_prompt"] = task_context.get("execution_prompt", "")

        if skills:
            instructions["skills"] = [
                {"name": s.get("name", ""), "content": s.get("content", "")}
                for s in skills
            ]

        codex_instructions = context_dir / "codex_instructions.json"
        codex_instructions.write_text(
            json.dumps(instructions, indent=2),
            encoding="utf-8",
        )

    def _inject_generic_context(
        self,
        context_dir: Path,
        task_id: str,
        task_context: dict[str, Any] | None,
        skills: list[dict[str, Any]] | None,
    ) -> None:
        """Inject a generic instructions file for unknown runners."""
        lines = [f"# Task {task_id}", ""]
        if task_context:
            lines.extend([
                f"Title: {task_context.get('title', '')}",
                f"Description: {task_context.get('description', '')}",
                "",
            ])
        instructions = context_dir / "instructions.md"
        instructions.write_text("\n".join(lines), encoding="utf-8")

    def _format_task_context(self, task_id: str, context: dict[str, Any]) -> str:
        """Format task context as markdown."""
        lines = [
            f"# Task Context: {context.get('title', task_id)}",
            "",
            f"**Task ID:** {task_id}",
            f"**Status:** {context.get('status', 'unknown')}",
            f"**Priority:** {context.get('priority', 'medium')}",
            "",
        ]
        desc = context.get("description")
        if desc:
            lines.extend(["## Description", "", desc, ""])

        criteria = context.get("acceptance_criteria", [])
        if criteria:
            lines.extend(["## Acceptance Criteria", ""])
            for i, c in enumerate(criteria, 1):
                if isinstance(c, str):
                    lines.append(f"{i}. {c}")
                elif isinstance(c, dict):
                    lines.append(f"{i}. {c.get('description', str(c))}")
            lines.append("")

        return "\n".join(lines)

    def preserve_logs(self, task_id: str, project_path: str) -> bool:
        """Preserve logs directory after task completion. Cleanup work/ and context/."""
        root = self._task_root(project_path, task_id)
        if not root.exists():
            return False

        # Keep logs/ and output/, remove work/ and context/
        for subdir in ("work", "context"):
            path = root / subdir
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)

        self._active_envs.pop(task_id, None)

        logger.info(f"Execution environment cleaned up (logs preserved): task_id={task_id}")
        return True

    def cleanup(self, task_id: str, project_path: str, preserve_logs: bool = True) -> bool:
        """Full cleanup of execution environment."""
        root = self._task_root(project_path, task_id)
        if not root.exists():
            self._active_envs.pop(task_id, None)
            return False

        if preserve_logs:
            return self.preserve_logs(task_id, project_path)

        shutil.rmtree(root, ignore_errors=True)
        self._active_envs.pop(task_id, None)

        logger.info(f"Execution environment fully removed: task_id={task_id}")
        return True

    def get_env(self, task_id: str) -> Path | None:
        """Get the root path of an active execution environment."""
        return self._active_envs.get(task_id)

    def list_active_envs(self) -> dict[str, str]:
        """List all active execution environments."""
        return {tid: str(p) for tid, p in self._active_envs.items()}
