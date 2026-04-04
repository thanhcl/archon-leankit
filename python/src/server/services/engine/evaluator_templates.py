"""Contract-aware evaluator templates for review stages."""

from copy import deepcopy
from typing import Any

DEFAULT_EVALUATOR_TEMPLATES: dict[str, dict[str, Any]] = {
    "architect-review": {
        "stage": "architect-review",
        "title": "Architect Review",
        "heading": "Architect Review — Contract Lock",
        "criteria_label": "Contract Criteria",
        "criteria_output_key": "contract_criteria",
        "score_range": "1-10",
        "scoring_guidance": (
            "Score each dimension and contract criterion by how measurable, complete, and independently reviewable "
            "the proposed contract is before implementation moves forward."
        ),
        "mandate": [
            "Treat every proposed contract as incomplete until each criterion is objectively measurable.",
            "Add missing edge cases, negative scenarios, and verification hooks before approving the contract.",
            "Reject contracts that cannot be independently verified by a downstream reviewer.",
        ],
        "approval_threshold": 7,
        "dimensions": [
            {
                "key": "contract_precision",
                "label": "CONTRACT_PRECISION",
                "description": "Are criteria specific, measurable, and bound to concrete pass conditions?",
            },
            {
                "key": "risk_coverage",
                "label": "RISK_COVERAGE",
                "description": "Do the criteria cover failure modes, negative tests, and operational risks?",
            },
            {
                "key": "design_fit",
                "label": "DESIGN_FIT",
                "description": "Does the proposed delivery align with the intended architecture and task scope?",
            },
            {
                "key": "verification_readiness",
                "label": "VERIFICATION_READINESS",
                "description": "Can code-review and owner-review verify this contract with observable evidence?",
            },
        ],
        "required_checks": [
            "Preserve the original acceptance criteria unless they are replaced with stricter measurable criteria.",
            "Convert vague wording into observable conditions and explicit error-path expectations.",
            "Identify any missing criteria that would allow a partial or misleading implementation to pass.",
        ],
    },
    "code-review": {
        "stage": "code-review",
        "title": "Independent Code Review",
        "heading": "Independent Code Review",
        "criteria_label": "Contract Criteria",
        "criteria_output_key": "contract_criteria",
        "score_range": "1-10",
        "scoring_guidance": (
            "Score each dimension and locked contract criterion against direct evidence from the diff, commands, "
            "tests, and observed reviewer checks."
        ),
        "mandate": [
            "Assume the implementation is wrong until evidence proves it satisfies the locked contract.",
            "Try to break the code through tests, edge cases, and policy checks before approving it.",
            "Use the contract criteria as the minimum bar for correctness, not as a suggestion.",
        ],
        "approval_threshold": 7,
        "dimensions": [
            {
                "key": "correctness",
                "label": "CORRECTNESS",
                "description": "Does implementation satisfy the locked contract and the stated task behavior?",
            },
            {
                "key": "tests",
                "label": "TESTS",
                "description": "Do tests prove the contract, cover edge cases, and actually pass?",
            },
            {
                "key": "security",
                "label": "SECURITY",
                "description": "Are there contract-breaking security failures such as auth bypass, injection, or leakage?",
            },
            {
                "key": "conventions",
                "label": "CONVENTIONS",
                "description": "Does the change follow project patterns, naming, and review policy expectations?",
            },
            {
                "key": "backward_compat",
                "label": "BACKWARD_COMPAT",
                "description": "Did the delivery silently break existing APIs, contracts, or migration expectations?",
            },
            {
                "key": "performance",
                "label": "PERFORMANCE",
                "description": "Are there regressions or needless complexity that undermine the contract in production?",
            },
        ],
        "required_checks": [
            "Run tests, linters, or other verification commands instead of relying on the diff alone.",
            "Score every contract criterion with explicit evidence tied to code, commands, or observed behavior.",
            "If evidence is missing, treat the criterion as unverified and score it accordingly.",
        ],
    },
    "owner-review": {
        "stage": "owner-review",
        "title": "Owner Review",
        "heading": "Owner Review — Contract vs Actual Delivery",
        "criteria_label": "Contract Criteria",
        "criteria_output_key": "contract_criteria",
        "score_range": "1-10",
        "scoring_guidance": (
            "Score each dimension and locked contract criterion against delivered behavior, upstream review evidence, "
            "and remaining ship risk from the owner perspective."
        ),
        "mandate": [
            "Validate the shipped behavior against the locked contract, not just the implementation summary.",
            "Use reviewer evidence to spot gaps between promised outcomes and actual delivery.",
            "Decide whether the feature is acceptable for the owner based on contract fulfillment and residual risk.",
        ],
        "approval_threshold": 7,
        "dimensions": [
            {
                "key": "contract_fulfillment",
                "label": "CONTRACT_FULFILLMENT",
                "description": "Does the delivered behavior satisfy the locked contract from the owner perspective?",
            },
            {
                "key": "delivery_completeness",
                "label": "DELIVERY_COMPLETENESS",
                "description": "Does the actual delivery cover the expected workflow, edge cases, and documentation?",
            },
            {
                "key": "residual_risk",
                "label": "RESIDUAL_RISK",
                "description": "Are any remaining findings, tradeoffs, or unknowns acceptable to ship?",
            },
            {
                "key": "ship_readiness",
                "label": "SHIP_READINESS",
                "description": "Would the owner approve this outcome for downstream users and operations?",
            },
        ],
        "required_checks": [
            "Read the contract comparison before making an owner-review decision.",
            "Pay special attention to user-visible behavior, operational fit, and unresolved findings.",
            "Reject the task if delivery evidence does not prove the contract is met.",
        ],
    },
}


def resolve_evaluator_template(stage: str, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the effective evaluator template for one review stage."""
    if stage not in DEFAULT_EVALUATOR_TEMPLATES:
        raise ValueError(f"Unsupported evaluator template stage: {stage}")

    template = deepcopy(DEFAULT_EVALUATOR_TEMPLATES[stage])
    override = _get_stage_template_override(policy, stage)
    if not override:
        return template

    return _merge_template(template, override)


def extract_contract_context(
    task: dict[str, Any],
    stage: str,
    execution_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the contract criteria and source relevant to a review stage.

    Contract resolution order for review stages (code-review, qa-eval, owner-review):
    1. Formal contract criteria pre-loaded from archon_task_contracts (injected as
       ``_formal_contract_criteria`` by the caller before invoking this function).
    2. Compatibility mirror: ``architect_review.locked_contract`` JSONB field.
    3. Proposed contract from the execution result (execution output).
    4. Original acceptance criteria as a last resort.

    The ``architect_review.locked_contract`` field is kept as a temporary compatibility
    mirror and is no longer the primary source for review stages.  Its absence does not
    break the review — the caller may simply not have set it yet.
    """
    proposed_contract = _normalize_contract_items(
        _get_execution_result(execution_result, task).get("proposed_contract")
    )

    # Primary source: formal contract criteria loaded from archon_task_contracts
    # (injected by the caller as a private key to avoid mutating the DB record).
    formal_criteria = _normalize_contract_items(task.get("_formal_contract_criteria"))

    # Compatibility mirror: kept for tasks that completed architect-review before
    # the formal contract table was in use.
    architect_review = task.get("architect_review") if isinstance(task.get("architect_review"), dict) else {}
    mirror_criteria = _normalize_contract_items(architect_review.get("locked_contract"))

    # For review stages, prefer the formal DB record, then the JSONB mirror.
    review_stages = {"code-review", "qa-eval", "owner-review"}
    if stage in review_stages:
        if formal_criteria:
            return {
                "source_label": "Locked Contract (from archon_task_contracts)",
                "criteria": formal_criteria,
            }
        if mirror_criteria:
            return {
                "source_label": "Locked Contract (adversarially enriched during architect-review)",
                "criteria": mirror_criteria,
            }

    if proposed_contract:
        return {
            "source_label": "Proposed Contract (captured from execution output)",
            "criteria": proposed_contract,
        }

    # Fall through: architect-review stage or no locked contract available yet.
    if mirror_criteria:
        return {
            "source_label": "Locked Contract (adversarially enriched during architect-review)",
            "criteria": mirror_criteria,
        }

    acceptance_criteria = _normalize_contract_items(task.get("acceptance_criteria") or [])
    return {
        "source_label": "Acceptance Criteria (original task contract)",
        "criteria": acceptance_criteria or [
            {
                "criterion": "Task completed as described",
                "threshold": "Behavior matches the task description without regressions",
                "category": "functional",
            }
        ],
    }


def build_owner_review_summary(
    task: dict[str, Any],
    execution_result: dict[str, Any] | None = None,
    review_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a contract-vs-delivery summary for the owner review stage."""
    contract = extract_contract_context(task, stage="owner-review", execution_result=execution_result)
    criteria = contract["criteria"]
    actual_delivery = build_actual_delivery_snapshot(task, execution_result, review_context=review_context)

    evidence_parts: list[str] = []
    if actual_delivery.get("result"):
        evidence_parts.append(f"Execution result: {actual_delivery['result']}")
    if actual_delivery.get("summary"):
        evidence_parts.append(f"Execution summary: {actual_delivery['summary']}")
    if actual_delivery.get("files_changed") is not None:
        evidence_parts.append(f"Files changed: {actual_delivery['files_changed']}")
    if actual_delivery.get("latest_review_stage") and actual_delivery.get("latest_review_verdict"):
        evidence_parts.append(
            f"{actual_delivery['latest_review_stage']} verdict: {actual_delivery['latest_review_verdict']}"
        )
    if actual_delivery.get("latest_review_summary"):
        evidence_parts.append(f"Review summary: {actual_delivery['latest_review_summary']}")
    if actual_delivery.get("latest_review_findings_count") is not None:
        evidence_parts.append(f"Open findings: {actual_delivery['latest_review_findings_count']}")

    evidence_text = "; ".join(evidence_parts) or "No automated delivery evidence captured."
    comparison_status = _derive_contract_comparison_status(actual_delivery)
    comparison_digest = _build_contract_comparison_digest(
        contract["source_label"],
        criteria,
        actual_delivery,
        comparison_status,
    )
    comparison: list[dict[str, Any]] = []
    summary_lines = [
        f"Contract source: {contract['source_label']}",
        "",
        "Actual delivery snapshot:",
    ]

    for label, value in _format_snapshot_items(actual_delivery):
        summary_lines.append(f"- {label}: {value}")

    summary_lines.extend(["", "Criterion-by-criterion comparison:"])

    for index, criterion in enumerate(criteria, start=1):
        observed_delivery = _build_observed_delivery_text(actual_delivery)
        review_signal = _build_review_signal(actual_delivery)
        comparison.append(
            {
                "criterion": criterion["criterion"],
                "threshold": criterion["threshold"],
                "category": criterion.get("category", "functional"),
                "actual_evidence": evidence_text,
                "observed_delivery": observed_delivery,
                "review_stage": actual_delivery.get("latest_review_stage"),
                "review_verdict": actual_delivery.get("latest_review_verdict"),
                "status": comparison_status,
            }
        )
        summary_lines.append(f"{index}. Contract: {criterion['criterion']}")
        summary_lines.append(f"   Pass condition: {criterion['threshold']}")
        summary_lines.append(f"   Comparison status: {comparison_status}")
        summary_lines.append(f"   Observed delivery: {observed_delivery}")
        if review_signal:
            summary_lines.append(f"   Review signal: {review_signal}")
        summary_lines.append(f"   Delivery evidence: {evidence_text}")

    return {
        "contract_source": contract["source_label"],
        "contract_criteria": criteria,
        "actual_delivery": actual_delivery,
        "comparison": comparison,
        "comparison_digest": comparison_digest,
        "summary_text": "\n".join(summary_lines),
    }


def build_contract_aware_evaluator(
    stage: str,
    task: dict[str, Any],
    execution_result: dict[str, Any] | None = None,
    review_context: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the effective stage evaluator with contract-specific context."""
    template = resolve_evaluator_template(stage, policy)
    contract = extract_contract_context(task, stage=stage, execution_result=execution_result)

    evaluator = {
        "stage": stage,
        "title": template["title"],
        "heading": template["heading"],
        "criteria_label": template.get("criteria_label", "Contract Criteria"),
        "criteria_output_key": template.get("criteria_output_key", "contract_criteria"),
        "score_range": template.get("score_range", "1-10"),
        "scoring_guidance": template.get("scoring_guidance", "Score each dimension against the contract."),
        "approval_threshold": template["approval_threshold"],
        "mandate": deepcopy(template.get("mandate", [])),
        "dimensions": deepcopy(template.get("dimensions", [])),
        "required_checks": deepcopy(template.get("required_checks", [])),
        "contract_source": contract["source_label"],
        "contract_criteria": deepcopy(contract["criteria"]),
    }

    if stage == "owner-review":
        comparison = build_owner_review_summary(task, execution_result, review_context=review_context)
        evaluator["contract_comparison"] = comparison["comparison"]
        evaluator["contract_delivery_summary"] = comparison["summary_text"]

    return evaluator


def build_actual_delivery_snapshot(
    task: dict[str, Any],
    execution_result: dict[str, Any] | None = None,
    review_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a normalized snapshot of actual delivery evidence."""
    latest_execution = _get_execution_result(execution_result, task)
    review = review_context if isinstance(review_context, dict) else {}
    architect_review = task.get("architect_review") if isinstance(task.get("architect_review"), dict) else {}
    code_review = task.get("code_review") if isinstance(task.get("code_review"), dict) else {}

    findings = review.get("findings")
    findings_count = len(findings) if isinstance(findings, list) else None
    inferred_stage, inferred_review = _infer_latest_review(architect_review, code_review)

    latest_review_stage = str(review.get("stage") or inferred_stage or "").strip() or None
    latest_review_verdict = review.get("verdict")
    if latest_review_verdict is None:
        latest_review_verdict = inferred_review.get("verdict")

    latest_review_summary = review.get("summary")
    if not latest_review_summary:
        latest_review_summary = code_review.get("summary") or architect_review.get("summary")

    if findings_count is None:
        review_findings = inferred_review.get("findings")
        findings_count = len(review_findings) if isinstance(review_findings, list) else None

    return {
        "result": latest_execution.get("result"),
        "summary": latest_execution.get("summary"),
        "files_changed": latest_execution.get("files_changed"),
        "architect_review_summary": architect_review.get("summary"),
        "code_review_verdict": code_review.get("verdict") or latest_review_verdict,
        "latest_review_stage": latest_review_stage,
        "latest_review_verdict": latest_review_verdict,
        "latest_review_summary": latest_review_summary,
        "latest_review_findings_count": findings_count,
    }


def _get_stage_template_override(policy: dict[str, Any] | None, stage: str) -> dict[str, Any] | None:
    if not isinstance(policy, dict):
        return None

    review_policy = policy.get("review_policy")
    policy_source = review_policy if isinstance(review_policy, dict) else policy
    templates = policy_source.get("evaluator_templates")
    if not isinstance(templates, dict):
        return None

    override = templates.get(stage)
    return override if isinstance(override, dict) else None


def _merge_template(template: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(template)

    for key, value in override.items():
        if key == "dimensions":
            dimensions = _normalize_dimensions(value)
            if dimensions:
                merged[key] = dimensions
            continue

        if isinstance(value, str):
            if value.strip():
                merged[key] = value.strip()
            continue

        if isinstance(value, list):
            merged[key] = [item for item in value if isinstance(item, str | int | float | bool | dict)]
            continue

        if isinstance(value, int | float | bool | dict):
            merged[key] = deepcopy(value)

    return merged


def _normalize_dimensions(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []

    dimensions: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue

        key = str(item.get("key") or "").strip()
        if not key:
            continue

        label = str(item.get("label") or key.upper()).strip()
        description = str(item.get("description") or item.get("prompt") or "").strip()
        if not description:
            continue

        dimensions.append({"key": key, "label": label, "description": description})

    return dimensions


def _normalize_contract_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []

    normalized: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            criterion = str(item.get("criterion") or item.get("text") or "").strip()
            threshold = str(item.get("threshold") or "Fully implemented as originally specified").strip()
            category = str(item.get("category") or "functional").strip()
            normalized_item = {
                "criterion": criterion,
                "threshold": threshold,
                "category": category,
            }
            added_by = item.get("added_by")
            if isinstance(added_by, str) and added_by.strip():
                normalized_item["added_by"] = added_by.strip()
        else:
            criterion = str(item).strip()
            normalized_item = {
                "criterion": criterion,
                "threshold": "Fully implemented as originally specified",
                "category": "functional",
            }

        if criterion:
            normalized.append(normalized_item)

    return normalized


def _get_execution_result(
    execution_result: dict[str, Any] | None,
    task: dict[str, Any],
) -> dict[str, Any]:
    if isinstance(execution_result, dict):
        return execution_result
    stored_result = task.get("execution_result")
    return stored_result if isinstance(stored_result, dict) else {}


def _format_snapshot_items(snapshot: dict[str, Any]) -> list[tuple[str, Any]]:
    labels = {
        "result": "Execution result",
        "summary": "Execution summary",
        "files_changed": "Files changed",
        "architect_review_summary": "Architect review summary",
        "code_review_verdict": "Code review verdict",
        "latest_review_stage": "Latest review stage",
        "latest_review_verdict": "Latest review verdict",
        "latest_review_summary": "Latest review summary",
        "latest_review_findings_count": "Latest review findings",
    }
    return [(label, snapshot[key]) for key, label in labels.items() if snapshot.get(key) is not None]


def _derive_contract_comparison_status(actual_delivery: dict[str, Any]) -> str:
    verdict = _normalize_review_verdict(actual_delivery.get("latest_review_verdict"))
    result = str(actual_delivery.get("result") or "").upper()

    if verdict in {"APPROVE", "APPROVE_CONTRACT", "APPROVE_DELIVERY"}:
        return "supported-by-review"
    if verdict in {
        "CHANGES_REQUESTED",
        "REQUEST_CHANGES",
        "REJECT",
        "REJECT_DELIVERY",
        "REVISE_CONTRACT",
        "ESCALATE",
    }:
        return "blocked-by-review"
    if result == "FAILURE":
        return "delivery-failed"
    if result == "SUCCESS":
        return "owner-validation-required"
    return "evidence-pending"


def _infer_latest_review(
    architect_review: dict[str, Any],
    code_review: dict[str, Any],
) -> tuple[str | None, dict[str, Any]]:
    if _has_review_evidence(code_review):
        return "code-review", code_review
    if _has_review_evidence(architect_review):
        return "architect-review", architect_review
    return None, {}


def _has_review_evidence(review: dict[str, Any]) -> bool:
    return any(review.get(key) is not None for key in ("verdict", "summary", "findings"))


def _normalize_review_verdict(value: Any) -> str:
    return str(value or "").strip().upper().replace("-", "_").replace(" ", "_")


def _build_observed_delivery_text(actual_delivery: dict[str, Any]) -> str:
    observed_parts: list[str] = []
    if actual_delivery.get("summary"):
        observed_parts.append(str(actual_delivery["summary"]))
    elif actual_delivery.get("result"):
        observed_parts.append(f"Execution result: {actual_delivery['result']}")

    if actual_delivery.get("files_changed") is not None:
        observed_parts.append(f"Files changed: {actual_delivery['files_changed']}")

    if actual_delivery.get("latest_review_summary"):
        observed_parts.append(f"Latest review summary: {actual_delivery['latest_review_summary']}")

    return "; ".join(observed_parts) or "No delivery summary captured."


def _build_review_signal(actual_delivery: dict[str, Any]) -> str | None:
    stage = actual_delivery.get("latest_review_stage")
    verdict = actual_delivery.get("latest_review_verdict")
    if not stage and verdict is None:
        return None
    if stage and verdict is not None:
        return f"{stage} verdict={verdict}"
    if stage:
        return f"{stage} evidence recorded"
    return f"Verdict={verdict}"


def _build_contract_comparison_digest(
    contract_source: str,
    criteria: list[dict[str, Any]],
    actual_delivery: dict[str, Any],
    comparison_status: str,
) -> str:
    parts = [f"Contract vs actual: {len(criteria)} criteria from {contract_source}"]

    delivery_summary = str(actual_delivery.get("summary") or "").strip()
    if delivery_summary:
        parts.append(f"delivery={delivery_summary[:160]}")

    result = actual_delivery.get("result")
    if result:
        parts.append(f"execution={result}")

    stage = actual_delivery.get("latest_review_stage")
    verdict = actual_delivery.get("latest_review_verdict")
    if stage and verdict is not None:
        parts.append(f"{stage}={verdict}")
    elif stage:
        parts.append(f"{stage}=recorded")

    files_changed = actual_delivery.get("files_changed")
    if files_changed is not None:
        parts.append(f"files_changed={files_changed}")

    findings_count = actual_delivery.get("latest_review_findings_count")
    if findings_count is not None:
        parts.append(f"open_findings={findings_count}")

    parts.append(f"status={comparison_status}")
    return "; ".join(parts)
