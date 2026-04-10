"""Tests for WorkflowLoader — YAML workflow discovery and loading."""

import os
import tempfile

import pytest

from src.server.services.engine.workflow_loader import WorkflowLoader


@pytest.fixture
def loader() -> WorkflowLoader:
    return WorkflowLoader()


@pytest.fixture
def project_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


def _write_workflow(project_dir: str, filename: str, content: str) -> str:
    """Write a workflow YAML file in the standard location."""
    wf_dir = os.path.join(project_dir, ".leankit", "workflows")
    os.makedirs(wf_dir, exist_ok=True)
    path = os.path.join(wf_dir, filename)
    with open(path, "w") as f:
        f.write(content)
    return path


SIMPLE_WORKFLOW = """\
name: build-feature
description: Build a new feature
nodes:
  - id: plan
    prompt: "Create an implementation plan"
  - id: implement
    prompt: "Implement the feature"
    depends_on: [plan]
  - id: test
    bash: "npm test"
    depends_on: [implement]
"""

LOOP_WORKFLOW = """\
name: refine-loop
nodes:
  - id: setup
    bash: "echo 0 > counter.txt"
  - id: refine
    depends_on: [setup]
    loop:
      prompt: "Increment counter"
      until: COMPLETE
      max_iterations: 5
"""

INVALID_WORKFLOW = """\
name: broken
nodes:
  - id: a
    prompt: "A"
    depends_on: [nonexistent]
"""


class TestLoadFile:
    """Test loading individual workflow files."""

    def test_load_valid_workflow(self, loader: WorkflowLoader, project_dir: str) -> None:
        path = _write_workflow(project_dir, "build.yaml", SIMPLE_WORKFLOW)
        wf = loader.load_file(path)
        assert wf.name == "build-feature"
        assert len(wf.nodes) == 3

    def test_load_loop_workflow(self, loader: WorkflowLoader, project_dir: str) -> None:
        path = _write_workflow(project_dir, "loop.yaml", LOOP_WORKFLOW)
        wf = loader.load_file(path)
        assert wf.name == "refine-loop"
        assert wf.nodes[1].loop is not None
        assert wf.nodes[1].loop.until == "COMPLETE"

    def test_load_nonexistent_file(self, loader: WorkflowLoader) -> None:
        with pytest.raises(FileNotFoundError):
            loader.load_file("/nonexistent/workflow.yaml")

    def test_load_invalid_workflow(self, loader: WorkflowLoader, project_dir: str) -> None:
        path = _write_workflow(project_dir, "broken.yaml", INVALID_WORKFLOW)
        with pytest.raises(Exception):  # ValidationError
            loader.load_file(path)


class TestLoadString:
    """Test loading from YAML string."""

    def test_load_string(self, loader: WorkflowLoader) -> None:
        wf = loader.load_string(SIMPLE_WORKFLOW)
        assert wf.name == "build-feature"

    def test_load_invalid_string(self, loader: WorkflowLoader) -> None:
        with pytest.raises(ValueError):
            loader.load_string("just a plain string")


class TestDiscover:
    """Test workflow discovery from project directories."""

    def test_discover_workflows(self, loader: WorkflowLoader, project_dir: str) -> None:
        _write_workflow(project_dir, "build.yaml", SIMPLE_WORKFLOW)
        _write_workflow(project_dir, "loop.yml", LOOP_WORKFLOW)

        workflows, errors = loader.discover(project_dir)
        assert len(workflows) == 2
        assert len(errors) == 0
        names = {wf.name for wf in workflows}
        assert names == {"build-feature", "refine-loop"}

    def test_discover_with_errors(self, loader: WorkflowLoader, project_dir: str) -> None:
        _write_workflow(project_dir, "good.yaml", SIMPLE_WORKFLOW)
        _write_workflow(project_dir, "bad.yaml", INVALID_WORKFLOW)

        workflows, errors = loader.discover(project_dir)
        assert len(workflows) == 1
        assert len(errors) == 1

    def test_discover_empty_directory(self, loader: WorkflowLoader, project_dir: str) -> None:
        workflows, errors = loader.discover(project_dir)
        assert len(workflows) == 0
        assert len(errors) == 0


class TestListAvailable:
    """Test lightweight workflow listing."""

    def test_list_available(self, loader: WorkflowLoader, project_dir: str) -> None:
        _write_workflow(project_dir, "build.yaml", SIMPLE_WORKFLOW)
        _write_workflow(project_dir, "loop.yaml", LOOP_WORKFLOW)

        available = loader.list_available(project_dir)
        assert len(available) == 2

        build = next(w for w in available if w["name"] == "build-feature")
        assert build["node_count"] == 3
        assert build["description"] == "Build a new feature"
