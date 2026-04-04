"""Tests for contract-aware evaluator templates and owner review payloads."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.server.services.engine.architect_reviewer import ArchitectReviewResult, ReviewAction, ReviewConfig
from src.server.services.engine.cc_spawner import CCExecutionResult
from src.server.services.engine.evaluator_templates import (
    build_contract_aware_evaluator,
    build_owner_review_summary,
    resolve_evaluator_template,
)
from src.server.services.engine.review_prompts import (
    build_adversarial_code_review_prompt,
    build_architect_review_prompt,
    build_owner_review_prompt,
    parse_code_review_scores,
)
from src.server.services.engine.task_engine import TaskEngine


def _make_task(**overrides):
    task = {
        "id": "task-001",
        "project_id": "proj-001",
        "title": "Add auth endpoint",
        "description": "Implement login and reject invalid credentials.",
        "status": "assigned",
        "priority": "high",
        "acceptance_criteria": [{"text": "Login works"}],
        "review_cycle": 0,
        "retry_count": 0,
        "architect_review": None,
        "execution_result": None,
        "created_at": "2026-01-01T00:00:00",
    }
    task.update(overrides)
    return task


def _code_review_approve_result() -> CCExecutionResult:
    return CCExecutionResult(
        success=True,
        stdout=(
            'CODE_REVIEW_VERDICT: APPROVE\n'
            'CODE_REVIEW_SCORES: {"dimensions":{"correctness":8},"contract_criteria":[],"lowest_score":8,'
            '"verdict_reason":"Verified"}\n'
            'CODE_REVIEW_FINDINGS: []\n'
        ),
        stderr="",
        exit_code=0,
        duration_seconds=18.0,
        parsed={"code_review_verdict": "APPROVE", "code_review_findings": []},
    )


def _setup_engine() -> TaskEngine:
    engine = TaskEngine(project_path="/tmp/test")
    engine.lifecycle_service = MagicMock()
    engine.lifecycle_service.execute_transition = AsyncMock(return_value=(True, {"task": _make_task()}))
    engine.task_service = MagicMock()
    engine.task_service.get_task.return_value = (True, {"task": _make_task()})
    engine.task_service.update_task = AsyncMock(return_value=(True, {}))
    engine.execution_run_service = MagicMock()
    engine.execution_run_service.create_run = AsyncMock(return_value=(True, {"run": {"id": "run-001"}}))
    engine.execution_run_service.update_run = AsyncMock(return_value=(True, {}))
    engine.spawner = MagicMock()
    engine.spawner.has_capacity = True
    engine.spawner.spawn = AsyncMock(return_value=_code_review_approve_result())
    engine.architect_reviewer = MagicMock()
    engine.architect_reviewer.review = AsyncMock(
        return_value=(
            ArchitectReviewResult(verdict="approve", confidence=0.94, summary="Architect approved"),
            ReviewAction(next_status="review", reason="Approved", changed_by="architect-reviewer"),
        )
    )
    engine.notifier = MagicMock()
    engine.notifier.on_task_review_ready = AsyncMock()
    engine.notifier.on_task_escalated = AsyncMock()
    engine.notifier.on_code_review_changes_requested = AsyncMock()
    engine.bug_task_creator = MagicMock()
    engine.bug_task_creator.create_bug_tasks_from_findings = AsyncMock(return_value=[])
    engine.learning_processor = MagicMock()
    engine.learning_processor.process = AsyncMock()
    return engine


def test_resolve_evaluator_template_defaults_for_all_review_stages():
    for stage in ("architect-review", "code-review", "owner-review"):
        template = resolve_evaluator_template(stage)

        assert template["stage"] == stage
        assert template["approval_threshold"] == 7
        assert len(template["dimensions"]) >= 3


def test_resolve_evaluator_template_applies_project_policy_override():
    policy = {
        "review_policy": {
            "evaluator_templates": {
                "code-review": {
                    "title": "Policy Code Review",
                    "approval_threshold": 8,
                    "dimensions": [
                        {
                            "key": "contract_alignment",
                            "label": "CONTRACT_ALIGNMENT",
                            "description": "Does the delivery match the locked contract exactly?",
                        }
                    ],
                    "required_checks": ["Verify migrations against project rollout policy."],
                }
            }
        }
    }

    template = resolve_evaluator_template("code-review", policy)

    assert template["title"] == "Policy Code Review"
    assert template["approval_threshold"] == 8
    assert template["dimensions"] == [
        {
            "key": "contract_alignment",
            "label": "CONTRACT_ALIGNMENT",
            "description": "Does the delivery match the locked contract exactly?",
        }
    ]
    assert template["required_checks"] == ["Verify migrations against project rollout policy."]


def test_architect_review_prompt_references_contract_criteria_explicitly():
    task = _make_task()
    execution_result = {
        "result": "SUCCESS",
        "summary": "Implemented login endpoint and validation.",
        "proposed_contract": [
            {
                "criterion": "Reject invalid credentials",
                "threshold": "HTTP 401 with descriptive error body",
                "category": "security",
            }
        ],
    }

    prompt = build_architect_review_prompt(task, execution_result)

    assert "Architect Review" in prompt
    assert "Reject invalid credentials" in prompt
    assert "HTTP 401 with descriptive error body" in prompt
    assert "Contract Criteria" in prompt
    assert "ARCHITECT_REVIEW_SCORES" in prompt
    assert "contract_readiness_summary" in prompt


def test_code_review_prompt_uses_stage_template_and_locked_contract():
    task = _make_task(
        architect_review={
            "locked_contract": [
                {
                    "criterion": "Locked login contract",
                    "threshold": "Reject invalid credentials with HTTP 401",
                    "category": "security",
                    "added_by": "adversarial-enrichment",
                }
            ]
        }
    )
    policy = {
        "review_policy": {
            "evaluator_templates": {
                "code-review": {
                    "heading": "Policy-Guided Code Review",
                    "dimensions": [
                        {
                            "key": "contract_alignment",
                            "label": "CONTRACT_ALIGNMENT",
                            "description": "Does the code satisfy the locked contract with direct evidence?",
                        },
                        {
                            "key": "tests",
                            "label": "TESTS",
                            "description": "Do tests prove the contract?",
                        },
                    ],
                }
            }
        }
    }

    prompt = build_adversarial_code_review_prompt(task, "+login", policy=policy)

    assert "Policy-Guided Code Review" in prompt
    assert "CONTRACT_ALIGNMENT" in prompt
    assert "Locked login contract" in prompt
    assert "Reject invalid credentials with HTTP 401" in prompt
    assert "Score each dimension independently against the contract" in prompt
    assert "ALL contract criteria scores >= 7" in prompt
    assert "ALL acceptance criteria scores" not in prompt
    assert '"contract_criteria": [' in prompt
    assert '"acceptance_criteria": [' not in prompt


def test_code_review_prompt_uses_proposed_contract_when_lock_not_available():
    task = _make_task(acceptance_criteria=[])
    execution_result = {
        "proposed_contract": [
            {
                "criterion": "Reject invalid credentials",
                "threshold": "HTTP 401 with descriptive error body",
                "category": "security",
            }
        ]
    }

    prompt = build_adversarial_code_review_prompt(task, "+login", execution_result=execution_result)

    assert "Reject invalid credentials" in prompt
    assert "HTTP 401 with descriptive error body" in prompt
    assert "Proposed Contract" in prompt


def test_owner_review_prompt_includes_contract_vs_actual_delivery_summary():
    task = _make_task(
        architect_review={
            "locked_contract": [
                {
                    "criterion": "Reject invalid credentials",
                    "threshold": "HTTP 401 with descriptive error body",
                    "category": "security",
                }
            ]
        }
    )
    execution_result = {"result": "SUCCESS", "summary": "Delivered login API and tests.", "files_changed": 3}
    prompt = build_owner_review_prompt(
        task,
        execution_result,
        review_context={"stage": "code-review", "verdict": "APPROVE", "summary": "Code review: APPROVE", "findings": []},
    )

    assert "Contract vs Actual Delivery" in prompt
    assert "Reject invalid credentials" in prompt
    assert "Delivered login API and tests." in prompt
    assert "APPROVE" in prompt
    assert "OWNER_REVIEW_SCORES" in prompt
    assert "ship_readiness_summary" in prompt


def test_build_contract_aware_evaluator_includes_stage_contract_and_owner_comparison():
    task = _make_task(
        architect_review={
            "locked_contract": [
                {
                    "criterion": "Reject invalid credentials",
                    "threshold": "HTTP 401 with descriptive error body",
                    "category": "security",
                }
            ]
        }
    )

    evaluator = build_contract_aware_evaluator(
        "owner-review",
        task,
        execution_result={"result": "SUCCESS", "summary": "Delivered login API and tests.", "files_changed": 3},
        review_context={"stage": "code-review", "verdict": "APPROVE", "summary": "Code review: APPROVE", "findings": []},
    )

    assert evaluator["stage"] == "owner-review"
    assert evaluator["contract_source"].startswith("Locked Contract")
    assert evaluator["contract_criteria"][0]["criterion"] == "Reject invalid credentials"
    assert evaluator["contract_comparison"][0]["status"] == "supported-by-review"
    assert "Delivered login API and tests." in evaluator["contract_delivery_summary"]


def test_owner_review_summary_returns_structured_comparison():
    task = _make_task(
        architect_review={
            "locked_contract": [
                {
                    "criterion": "Reject invalid credentials",
                    "threshold": "HTTP 401 with descriptive error body",
                    "category": "security",
                }
            ]
        }
    )

    summary = build_owner_review_summary(
        task,
        execution_result={"result": "SUCCESS", "summary": "Delivered login API and tests.", "files_changed": 3},
        review_context={"stage": "code-review", "verdict": "APPROVE", "summary": "Code review: APPROVE", "findings": []},
    )

    assert summary["contract_source"].startswith("Locked Contract")
    assert summary["comparison"][0]["criterion"] == "Reject invalid credentials"
    assert summary["comparison"][0]["observed_delivery"].startswith("Delivered login API and tests.")
    assert summary["comparison"][0]["review_verdict"] == "APPROVE"
    assert summary["comparison_digest"].startswith("Contract vs actual: 1 criteria")
    assert "delivery=Delivered login API and tests." in summary["comparison_digest"]
    assert "status=supported-by-review" in summary["comparison_digest"]
    assert "Delivered login API and tests." in summary["summary_text"]


def test_owner_review_summary_uses_stored_code_review_without_explicit_context():
    task = _make_task(
        architect_review={
            "locked_contract": [
                {
                    "criterion": "Reject invalid credentials",
                    "threshold": "HTTP 401 with descriptive error body",
                    "category": "security",
                }
            ],
            "verdict": "approve",
            "summary": "Architect approved locked contract.",
        },
        code_review={
            "verdict": "APPROVE",
            "summary": "Code review: APPROVE",
            "findings": [],
        },
    )

    summary = build_owner_review_summary(
        task,
        execution_result={"result": "SUCCESS", "summary": "Delivered login API and tests.", "files_changed": 3},
    )

    assert summary["comparison"][0]["status"] == "supported-by-review"
    assert summary["comparison"][0]["review_stage"] == "code-review"
    assert summary["comparison"][0]["review_verdict"] == "APPROVE"
    assert "code-review verdict=APPROVE" in summary["summary_text"]


def test_parse_code_review_scores_accepts_contract_criteria_key():
    scores = parse_code_review_scores(
        'CODE_REVIEW_VERDICT: APPROVE\n'
        'CODE_REVIEW_SCORES: {"dimensions":{"correctness":8},"contract_criteria":[{"criterion":"Works","score":8,'
        '"evidence":"verified"}],"lowest_score":8,"verdict_reason":"Verified"}\n'
        "CODE_REVIEW_FINDINGS: []\n"
    )

    assert scores is not None
    assert scores["contract_criteria"][0]["criterion"] == "Works"
    assert scores["acceptance_criteria"][0]["criterion"] == "Works"


@pytest.mark.asyncio
async def test_run_code_review_persists_owner_review_payload_for_owner_stage():
    task = _make_task(
        status="code-review",
        priority="critical",
        architect_review={
            "locked_contract": [
                {
                    "criterion": "Reject invalid credentials",
                    "threshold": "HTTP 401 with descriptive error body",
                    "category": "security",
                }
            ]
        },
    )
    engine = _setup_engine()
    engine.task_service.get_task.return_value = (True, {"task": task})
    engine._get_git_diff = AsyncMock(return_value="diff --git a/auth.py")

    await engine._run_code_review(
        "task-001",
        {"result": "SUCCESS", "summary": "Delivered login API and tests.", "files_changed": 3},
    )

    update_fields = engine.task_service.update_task.await_args.kwargs["update_fields"]
    code_review = update_fields["code_review"]

    assert code_review["evaluator_template"]["stage"] == "code-review"
    assert code_review["contract_evaluator"]["stage"] == "code-review"
    assert code_review["contract_evaluator"]["contract_criteria"][0]["criterion"] == "Reject invalid credentials"
    assert code_review["owner_review"]["template"]["stage"] == "owner-review"
    assert code_review["owner_review"]["contract_evaluator"]["stage"] == "owner-review"
    assert "Reject invalid credentials" in code_review["owner_review"]["summary"]["summary_text"]
    assert "Delivered login API and tests." in code_review["owner_review"]["summary"]["summary_text"]

    review_ready_payload = engine.notifier.on_task_review_ready.await_args.args[1]
    assert review_ready_payload["summary"] == review_ready_payload["owner_review"]["summary"]["comparison_digest"]
    assert review_ready_payload["owner_review"]["template"]["stage"] == "owner-review"
    assert review_ready_payload["owner_review"]["contract_evaluator"]["contract_comparison"][0]["status"] == "supported-by-review"


@pytest.mark.asyncio
async def test_run_architect_review_attaches_owner_review_payload_when_direct_to_owner_review():
    task = _make_task()
    engine = _setup_engine()
    engine.task_service.get_task.return_value = (True, {"task": task})
    engine.review_config = ReviewConfig(independent_review_enabled=False)

    await engine._run_architect_review(
        "task-001",
        {
            "result": "SUCCESS",
            "summary": "Delivered login API and tests.",
            "files_changed": 3,
            "proposed_contract": [
                {
                    "criterion": "Reject invalid credentials",
                    "threshold": "HTTP 401 with descriptive error body",
                    "category": "security",
                }
            ],
        },
    )

    review_ready_payload = engine.notifier.on_task_review_ready.await_args.args[1]

    assert review_ready_payload["summary"] == review_ready_payload["owner_review"]["summary"]["comparison_digest"]
    assert review_ready_payload["evaluator_template"]["stage"] == "architect-review"
    assert review_ready_payload["contract_evaluator"]["stage"] == "architect-review"
    assert review_ready_payload["contract_evaluator"]["contract_criteria"][0]["criterion"] == "Reject invalid credentials"
    assert review_ready_payload["owner_review"]["template"]["stage"] == "owner-review"
    assert review_ready_payload["owner_review"]["contract_evaluator"]["stage"] == "owner-review"
    assert review_ready_payload["owner_review"]["summary"]["contract_source"].startswith("Locked Contract")


# ── Formal contract loading (archon_task_contracts table) ─────────────────────


def test_extract_contract_context_prefers_formal_contract_criteria_over_jsonb_mirror():
    """_formal_contract_criteria takes precedence over architect_review.locked_contract for review stages."""
    from src.server.services.engine.evaluator_templates import extract_contract_context

    task = _make_task(
        architect_review={
            "locked_contract": [
                {
                    "criterion": "Old JSONB mirror criterion",
                    "threshold": "Should not appear",
                    "category": "functional",
                }
            ]
        },
        # Formal contract pre-loaded from archon_task_contracts
        _formal_contract_criteria=[
            {
                "criterion": "Formal DB contract criterion",
                "threshold": "Must appear in code-review",
                "category": "security",
            }
        ],
    )

    for stage in ("code-review", "qa-eval", "owner-review"):
        ctx = extract_contract_context(task, stage=stage)
        assert ctx["source_label"] == "Locked Contract (from archon_task_contracts)"
        assert len(ctx["criteria"]) == 1
        assert ctx["criteria"][0]["criterion"] == "Formal DB contract criterion"
        assert "Old JSONB mirror" not in str(ctx["criteria"])


def test_extract_contract_context_falls_back_to_jsonb_mirror_when_no_formal_criteria():
    """Falls back to architect_review.locked_contract when _formal_contract_criteria is absent."""
    from src.server.services.engine.evaluator_templates import extract_contract_context

    task = _make_task(
        architect_review={
            "locked_contract": [
                {
                    "criterion": "JSONB mirror criterion",
                    "threshold": "HTTP 401",
                    "category": "security",
                }
            ]
        },
    )

    for stage in ("code-review", "owner-review"):
        ctx = extract_contract_context(task, stage=stage)
        assert "adversarially enriched" in ctx["source_label"]
        assert ctx["criteria"][0]["criterion"] == "JSONB mirror criterion"


def test_extract_contract_context_missing_jsonb_does_not_break_review():
    """Missing architect_review.locked_contract falls through to acceptance_criteria gracefully."""
    from src.server.services.engine.evaluator_templates import extract_contract_context

    task = _make_task(
        architect_review=None,
        acceptance_criteria=[{"text": "Task completed"}],
    )

    ctx = extract_contract_context(task, stage="code-review")
    assert ctx["source_label"] == "Acceptance Criteria (original task contract)"
    assert len(ctx["criteria"]) >= 1


def test_load_formal_contract_criteria_returns_empty_when_no_contract_id():
    """Returns empty list when task has no current_contract_id."""
    engine = _setup_engine()
    task = _make_task()  # no current_contract_id key

    result = engine._load_formal_contract_criteria(task)
    assert result == []
    engine.task_service.get_contract.assert_not_called()


def test_load_formal_contract_criteria_fetches_from_db():
    """Fetches contract from task_service.get_contract and returns acceptance_criteria."""
    engine = _setup_engine()
    engine.task_service.get_contract = MagicMock(return_value=(
        True,
        {
            "contract": {
                "id": "contract-001",
                "acceptance_criteria": [
                    {"criterion": "DB contract criterion", "threshold": "pass", "category": "functional"}
                ],
                "locked_at": "2026-01-01T00:00:00Z",
            }
        },
    ))
    task = _make_task(current_contract_id="contract-001")

    result = engine._load_formal_contract_criteria(task)
    assert len(result) == 1
    assert result[0]["criterion"] == "DB contract criterion"
    engine.task_service.get_contract.assert_called_once_with("contract-001")


def test_load_formal_contract_criteria_returns_empty_on_fetch_failure():
    """Returns empty list when get_contract fails (non-blocking)."""
    engine = _setup_engine()
    engine.task_service.get_contract = MagicMock(return_value=(False, {"error": "not found"}))
    task = _make_task(current_contract_id="contract-missing")

    result = engine._load_formal_contract_criteria(task)
    assert result == []


@pytest.mark.asyncio
async def test_run_code_review_injects_formal_contract_from_db():
    """_run_code_review injects _formal_contract_criteria when current_contract_id is set."""
    formal_criteria = [
        {"criterion": "DB criterion", "threshold": "HTTP 200", "category": "functional"}
    ]
    task = _make_task(
        status="code-review",
        current_contract_id="contract-001",
    )
    engine = _setup_engine()
    engine.task_service.get_task.return_value = (True, {"task": task})
    engine.task_service.get_contract = MagicMock(return_value=(
        True,
        {"contract": {"id": "contract-001", "acceptance_criteria": formal_criteria}},
    ))
    engine._get_git_diff = AsyncMock(return_value="diff --git a/auth.py")

    await engine._run_code_review("task-001", {"result": "SUCCESS", "summary": "Done"})

    # Verify that get_contract was called with the contract_id from the task
    engine.task_service.get_contract.assert_called_once_with("contract-001")
    # Prompt was built (spawner was called)
    assert engine.spawner.spawn.await_count == 1
    spawned_prompt = engine.spawner.spawn.await_args.kwargs.get("prompt") or engine.spawner.spawn.await_args.args[1]
    assert "DB criterion" in spawned_prompt
