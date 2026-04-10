"""
Workflow Loader — Discovers and loads YAML workflow definitions.

Adopted from upstream Archon v0.3.2 workflow discovery pattern.
Searches multiple paths for .yaml/.yml workflow files and validates them.

Search order:
1. Project-local: {project_path}/.leankit/workflows/
2. User defaults: ~/.leankit/workflows/
3. Bundled defaults (future)

Usage:
    loader = WorkflowLoader()
    workflows, errors = loader.discover(project_path="/path/to/repo")
    workflow = loader.load_file("/path/to/workflow.yaml")
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ...config.logfire_config import get_logger
from .workflow_schema import WorkflowDefinition

logger = get_logger(__name__)

# Default search directories (relative to project root)
PROJECT_WORKFLOW_DIR = ".leankit/workflows"
USER_WORKFLOW_DIR = "~/.leankit/workflows"


class WorkflowLoadError:
    """Record of a failed workflow load."""

    def __init__(self, path: str, error: str) -> None:
        self.path = path
        self.error = error

    def __repr__(self) -> str:
        return f"WorkflowLoadError(path={self.path!r}, error={self.error!r})"


class WorkflowLoader:
    """Discovers and validates YAML workflow definitions."""

    def __init__(
        self,
        extra_search_paths: list[str] | None = None,
    ) -> None:
        self._extra_paths = extra_search_paths or []

    def discover(
        self,
        project_path: str,
    ) -> tuple[list[WorkflowDefinition], list[WorkflowLoadError]]:
        """Discover all workflow files in standard locations.

        Returns:
            (workflows, errors) — successfully loaded workflows and load errors.
        """
        workflows: list[WorkflowDefinition] = []
        errors: list[WorkflowLoadError] = []

        search_dirs = self._resolve_search_dirs(project_path)

        for search_dir in search_dirs:
            if not search_dir.is_dir():
                continue

            for fpath in sorted(search_dir.glob("*.y*ml")):
                if fpath.suffix not in (".yaml", ".yml"):
                    continue

                try:
                    wf = self.load_file(str(fpath))
                    workflows.append(wf)
                    logger.debug(f"Loaded workflow: {wf.name} from {fpath}")
                except Exception as e:
                    errors.append(WorkflowLoadError(str(fpath), str(e)))
                    logger.warning(f"Failed to load workflow {fpath}: {e}")

        logger.info(
            f"Workflow discovery: {len(workflows)} loaded, {len(errors)} errors "
            f"from {len(search_dirs)} search paths"
        )
        return workflows, errors

    def load_file(self, file_path: str) -> WorkflowDefinition:
        """Load and validate a single YAML workflow file.

        Args:
            file_path: Path to the .yaml/.yml file.

        Returns:
            Validated WorkflowDefinition.

        Raises:
            FileNotFoundError: If the file doesn't exist.
            yaml.YAMLError: If YAML parsing fails.
            pydantic.ValidationError: If schema validation fails.
        """
        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError(f"Workflow file not found: {file_path}")

        content = path.read_text(encoding="utf-8")
        data = yaml.safe_load(content)

        if not isinstance(data, dict):
            raise ValueError(f"Workflow file must contain a YAML mapping, got {type(data).__name__}")

        return WorkflowDefinition.model_validate(data)

    def load_string(self, yaml_content: str) -> WorkflowDefinition:
        """Load a workflow from a YAML string.

        Useful for testing and programmatic workflow creation.
        """
        data = yaml.safe_load(yaml_content)
        if not isinstance(data, dict):
            raise ValueError(f"Workflow YAML must be a mapping, got {type(data).__name__}")
        return WorkflowDefinition.model_validate(data)

    def _resolve_search_dirs(self, project_path: str) -> list[Path]:
        """Resolve all workflow search directories."""
        dirs: list[Path] = []

        # Project-local
        project_dir = Path(project_path) / PROJECT_WORKFLOW_DIR
        dirs.append(project_dir)

        # User-level
        user_dir = Path(USER_WORKFLOW_DIR).expanduser()
        dirs.append(user_dir)

        # Extra paths
        for p in self._extra_paths:
            dirs.append(Path(p))

        return dirs

    def list_available(self, project_path: str) -> list[dict[str, Any]]:
        """List available workflows with metadata (without full loading).

        Returns lightweight summary for each workflow file found.
        """
        result: list[dict[str, Any]] = []
        search_dirs = self._resolve_search_dirs(project_path)

        for search_dir in search_dirs:
            if not search_dir.is_dir():
                continue

            for fpath in sorted(search_dir.glob("*.y*ml")):
                if fpath.suffix not in (".yaml", ".yml"):
                    continue

                try:
                    content = fpath.read_text(encoding="utf-8")
                    data = yaml.safe_load(content)
                    if isinstance(data, dict):
                        result.append({
                            "name": data.get("name", fpath.stem),
                            "description": data.get("description", ""),
                            "node_count": len(data.get("nodes", [])),
                            "path": str(fpath),
                            "source": str(search_dir),
                        })
                except Exception:
                    result.append({
                        "name": fpath.stem,
                        "description": f"<load error>",
                        "node_count": 0,
                        "path": str(fpath),
                        "source": str(search_dir),
                    })

        return result
