"""Tests for bootstrap plan dry-run preview (CI-7)."""

from src.server.services.projects.bootstrap_architect import (
    BootstrapPlanEnvelope,
)
from src.server.services.projects.bootstrap_plan_service import BootstrapPlanService
from src.server.services.projects.bootstrap_planner import BootstrapPlanItem, build_bootstrap_context


def _make_context(project_type: str = "web-app", bootstrap_policy: str = "standard"):
    return build_bootstrap_context(
        project_title="NextJS Dry-Run Project",
        project_description="CI-7 verification project",
        github_repo="https://github.com/example/nextjs-app",
        bootstrap_template="nextjs",
        project_type=project_type,
        bootstrap_policy=bootstrap_policy,
    )


def _scaffold_item() -> BootstrapPlanItem:
    return BootstrapPlanItem(
        key="scaffold",
        title="Bootstrap workspace",
        description="Scaffold the project",
        task_type="feature",
        priority="high",
        complexity="simple",
        max_retries=2,
        created_from="project-bootstrap",
        execution_prompt="Bootstrap the workspace",
        acceptance_criteria=["Create initial structure"],
        tags=["project-bootstrap", "scaffold"],
        blocked_on_key=None,
    )


def _validation_item() -> BootstrapPlanItem:
    return BootstrapPlanItem(
        key="validation",
        title="Validate workspace",
        description="Validate the scaffold",
        task_type="test",
        priority="high",
        complexity="simple",
        max_retries=1,
        created_from="project-bootstrap-validation",
        execution_prompt="Validate the workspace",
        acceptance_criteria=["Workspace is runnable"],
        tags=["project-bootstrap", "bootstrap-validation"],
        blocked_on_key="scaffold",
    )


def _followup_item() -> BootstrapPlanItem:
    return BootstrapPlanItem(
        key="followup",
        title="Seed follow-up plan",
        description="Create initial backlog",
        task_type="improvement",
        priority="medium",
        complexity="simple",
        max_retries=1,
        created_from="project-bootstrap-followup",
        execution_prompt="Seed next tasks",
        acceptance_criteria=["Propose actionable tasks"],
        tags=["project-bootstrap", "bootstrap-followup"],
        blocked_on_key="validation",
    )


def _backlog_item() -> BootstrapPlanItem:
    return BootstrapPlanItem(
        key="api-setup",
        title="Set up API routes",
        description="Create baseline API",
        task_type="feature",
        priority="medium",
        complexity="simple",
        max_retries=1,
        created_from="project-bootstrap-derived-1",
        execution_prompt="Create API routes",
        acceptance_criteria=["API routes created"],
        tags=["project-bootstrap", "bootstrap-derived-backlog"],
        blocked_on_key="followup",
    )


def _make_envelope_standard() -> BootstrapPlanEnvelope:
    return BootstrapPlanEnvelope(
        requested_provider="rule-based",
        resolved_provider="rule-based",
        strategy="rule-based",
        model=None,
        items=[_scaffold_item(), _validation_item(), _followup_item()],
        backlog_items=[],
    )


def _make_envelope_with_backlog() -> BootstrapPlanEnvelope:
    return BootstrapPlanEnvelope(
        requested_provider="chatgpt-codex",
        resolved_provider="chatgpt-codex",
        strategy="provider-generated",
        model="gpt-5.4",
        items=[_scaffold_item(), _validation_item(), _followup_item()],
        backlog_items=[_backlog_item()],
    )


def test_preview_plan_returns_dry_run_flag():
    service = BootstrapPlanService(supabase_client=None)
    context = _make_context()
    envelope = _make_envelope_standard()

    ok, result = service.preview_plan(
        project_id="proj-ci7",
        source_app="ci-7-test",
        context=context,
        envelope=envelope,
    )

    assert ok is True
    assert result["dry_run"] is True


def test_preview_plan_contains_all_plan_items():
    service = BootstrapPlanService(supabase_client=None)
    context = _make_context()
    envelope = _make_envelope_standard()

    ok, result = service.preview_plan(
        project_id="proj-ci7",
        source_app=None,
        context=context,
        envelope=envelope,
    )

    assert ok is True
    plan_items = result["plan_items"]
    assert len(plan_items) == 3
    keys = {item["key"] for item in plan_items}
    assert {"scaffold", "validation", "followup"} == keys


def test_preview_plan_total_task_count():
    service = BootstrapPlanService(supabase_client=None)
    context = _make_context()
    envelope = _make_envelope_with_backlog()

    ok, result = service.preview_plan(
        project_id="proj-ci7",
        source_app=None,
        context=context,
        envelope=envelope,
    )

    assert ok is True
    assert len(result["plan_items"]) == 3
    assert len(result["backlog_items"]) == 1
    assert result["total_task_count"] == 4


def test_preview_plan_backlog_items_flagged():
    service = BootstrapPlanService(supabase_client=None)
    context = _make_context()
    envelope = _make_envelope_with_backlog()

    ok, result = service.preview_plan(
        project_id="proj-ci7",
        source_app=None,
        context=context,
        envelope=envelope,
    )

    assert ok is True
    for item in result["plan_items"]:
        assert item["is_backlog"] is False
    for item in result["backlog_items"]:
        assert item["is_backlog"] is True


def test_preview_plan_reflects_context_metadata():
    service = BootstrapPlanService(supabase_client=None)
    context = _make_context(project_type="web-app", bootstrap_policy="standard")
    envelope = _make_envelope_standard()

    ok, result = service.preview_plan(
        project_id="proj-ci7",
        source_app="nextjs-verifier",
        context=context,
        envelope=envelope,
    )

    assert ok is True
    assert result["project_id"] == "proj-ci7"
    assert result["project_type"] == "web-app"
    assert result["bootstrap_policy"] == "standard"
    assert result["template"] == "nextjs"
    assert result["source_app"] == "nextjs-verifier"
    assert result["strategy"] == "rule-based"


def test_preview_plan_does_not_call_supabase():
    """preview_plan must never touch the database."""
    from unittest.mock import MagicMock

    mock_client = MagicMock()
    service = BootstrapPlanService(supabase_client=mock_client)
    context = _make_context()
    envelope = _make_envelope_standard()

    ok, _ = service.preview_plan(
        project_id="proj-ci7",
        source_app=None,
        context=context,
        envelope=envelope,
    )

    assert ok is True
    mock_client.table.assert_not_called()


def test_preview_plan_strict_policy_envelope_with_architecture():
    """strict policy may include an architecture item — verify it is included."""
    from src.server.services.projects.bootstrap_planner import plan_project_bootstrap

    context = _make_context(project_type="web-app", bootstrap_policy="strict")
    items = plan_project_bootstrap(context)
    envelope = BootstrapPlanEnvelope(
        requested_provider="rule-based",
        resolved_provider="rule-based",
        strategy="rule-based",
        model=None,
        items=items,
        backlog_items=[],
    )

    service = BootstrapPlanService(supabase_client=None)
    ok, result = service.preview_plan(
        project_id="proj-strict",
        source_app=None,
        context=context,
        envelope=envelope,
    )

    assert ok is True
    plan_keys = {item["key"] for item in result["plan_items"]}
    assert "scaffold" in plan_keys
    assert "validation" in plan_keys
    assert "followup" in plan_keys
    assert "architecture" in plan_keys
    assert result["total_task_count"] == 4


def test_preview_plan_nextjs_web_app_rule_based():
    """End-to-end rule-based generation for tech_stack=nextjs (web-app type)."""
    from src.server.services.projects.bootstrap_planner import plan_project_bootstrap

    context = build_bootstrap_context(
        project_title="NextJS App",
        project_description="A NextJS application",
        github_repo=None,
        bootstrap_template="nextjs",
        project_type="web-app",
        bootstrap_policy="standard",
    )
    items = plan_project_bootstrap(context)
    envelope = BootstrapPlanEnvelope(
        requested_provider="rule-based",
        resolved_provider="rule-based",
        strategy="rule-based",
        model=None,
        items=items,
        backlog_items=[],
    )

    service = BootstrapPlanService(supabase_client=None)
    ok, result = service.preview_plan(
        project_id="proj-nextjs",
        source_app=None,
        context=context,
        envelope=envelope,
    )

    assert ok is True
    assert result["dry_run"] is True
    assert result["template"] == "nextjs"
    assert result["project_type"] == "web-app"
    plan_keys = {item["key"] for item in result["plan_items"]}
    assert {"scaffold", "validation", "followup"} == plan_keys

    # Verify scaffold acceptance criteria include nextjs/web-app notes
    scaffold = next(item for item in result["plan_items"] if item["key"] == "scaffold")
    assert len(scaffold["acceptance_criteria"]) >= 3
    assert scaffold["priority"] == "high"
    assert scaffold["complexity"] == "simple"
