"""Contract-aware review prompt builders for LeanKit V3 Task Engine."""

import json
import re
from typing import Any

from .evaluator_templates import build_contract_aware_evaluator, build_owner_review_summary

_SCORES_RE = re.compile(
    r"CODE_REVIEW_SCORES:\s*(\{.*?\})\s*(?:CODE_REVIEW_FINDINGS:|$)",
    re.IGNORECASE | re.DOTALL,
)

def parse_code_review_scores(stdout: str) -> dict[str, Any] | None:
    """Parse the CODE_REVIEW_SCORES JSON block from CC stdout."""
    if not stdout:
        return None

    match = _SCORES_RE.search(stdout)
    if not match:
        return None

    try:
        scores = json.loads(match.group(1).strip())
    except json.JSONDecodeError:
        return None

    if not isinstance(scores, dict):
        return None

    if not isinstance(scores.get("dimensions"), dict):
        return None

    contract_scores = scores.get("contract_criteria")
    acceptance_scores = scores.get("acceptance_criteria")
    if isinstance(contract_scores, list) and not isinstance(acceptance_scores, list):
        scores["acceptance_criteria"] = contract_scores
    elif isinstance(acceptance_scores, list) and not isinstance(contract_scores, list):
        scores["contract_criteria"] = acceptance_scores

    return scores


def enrich_contract_adversarially(
    task: dict[str, Any],
    proposed_criteria: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Enrich proposed contract criteria with adversarial additions."""
    title = (task.get("title") or "").lower()
    description = (task.get("description") or "").lower()
    original_criteria = task.get("acceptance_criteria") or []

    enriched: list[dict[str, Any]] = list(proposed_criteria)
    existing_texts = {criterion["criterion"].lower() for criterion in enriched if criterion.get("criterion")}
    additions: list[dict[str, Any]] = []

    if not any("test" in text for text in existing_texts):
        additions.append(
            {
                "criterion": "All tests pass without modification to test files",
                "threshold": "Exit code 0; no skipped or xfail tests in changed test files",
                "category": "functional",
                "added_by": "adversarial-enrichment",
            }
        )

    if any(keyword in title + description for keyword in ("api", "endpoint", "route", "http")):
        if not any(keyword in text for text in existing_texts for keyword in ("error", "4xx", "invalid", "reject")):
            additions.append(
                {
                    "criterion": "Invalid input returns appropriate error response (4xx), not 500",
                    "threshold": "HTTP 4xx status with descriptive error body on malformed request",
                    "category": "security",
                    "added_by": "adversarial-enrichment",
                }
            )

    if any(keyword in title + description for keyword in ("auth", "login", "token", "permission", "access")):
        if not any(
            keyword in text for text in existing_texts for keyword in ("bypass", "inject", "unauthorized", "403", "401")
        ):
            additions.append(
                {
                    "criterion": "Unauthorized access attempt is rejected (401/403)",
                    "threshold": "No data leaked on unauthenticated or unauthorized request",
                    "category": "security",
                    "added_by": "adversarial-enrichment",
                }
            )

    if any(keyword in title + description for keyword in ("database", "db", "sql", "migration", "schema")):
        if not any(keyword in text for text in existing_texts for keyword in ("rollback", "transaction", "atomic", "constraint")):
            additions.append(
                {
                    "criterion": "Database operation is atomic — rolls back on partial failure",
                    "threshold": "No orphaned records on failure; constraints enforced",
                    "category": "functional",
                    "added_by": "adversarial-enrichment",
                }
            )

    if not any(keyword in text for text in existing_texts for keyword in ("debug", "todo", "fixme", "print")):
        additions.append(
            {
                "criterion": "No debug code, TODOs, or temporary stubs in production paths",
                "threshold": "Zero TODO/FIXME/print/console.log occurrences in changed production files",
                "category": "functional",
                "added_by": "adversarial-enrichment",
            }
        )

    for original in original_criteria:
        original_text = original.get("text", str(original)) if isinstance(original, dict) else str(original)
        if original_text.lower() not in existing_texts:
            additions.append(
                {
                    "criterion": original_text,
                    "threshold": "Fully implemented as originally specified",
                    "category": "functional",
                    "added_by": "spec-enforcement",
                }
            )

    enriched.extend(additions)
    return enriched


def build_architect_review_prompt(
    task: dict[str, Any],
    execution_result: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
) -> str:
    """Build a contract-aware architect review prompt."""
    title = task.get("title", "Untitled")
    description = task.get("description") or "_No description_"
    evaluator = build_contract_aware_evaluator(
        "architect-review",
        task,
        execution_result=execution_result,
        policy=policy,
    )
    delivery_summary = build_owner_review_summary(task, execution_result, review_context={"stage": "architect-review"})

    return "\n".join(
        [
            f"# {evaluator['heading']}",
            "",
            *evaluator.get("mandate", []),
            "",
            f"## Task: {title}",
            "",
            "### Description",
            description,
            "",
            f"## Stage Scoring Template (threshold >= {evaluator['approval_threshold']})",
            f"Score every dimension and contract criterion from {evaluator['score_range']}.",
            evaluator["scoring_guidance"],
            "",
            "### Review Dimensions",
            _format_dimension_lines(evaluator),
            "",
            f"### {evaluator['criteria_label']} — {evaluator['contract_source']} "
            f"(score each {evaluator['score_range']})",
            _format_contract_lines(evaluator["contract_criteria"]),
            "",
            f"Total contract criteria to score: {len(evaluator['contract_criteria'])}",
            "",
            "## Required Checks",
            _format_check_lines(evaluator),
            "",
            "## Delivery Snapshot",
            delivery_summary["summary_text"],
            "",
            "## Required Output (STRICT FORMAT)",
            _format_required_output_block("architect-review", evaluator),
        ]
    )


def build_owner_review_prompt(
    task: dict[str, Any],
    execution_result: dict[str, Any] | None = None,
    review_context: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
) -> str:
    """Build an owner review prompt with a contract-vs-delivery comparison."""
    title = task.get("title", "Untitled")
    description = task.get("description") or "_No description_"
    evaluator = build_contract_aware_evaluator(
        "owner-review",
        task,
        execution_result=execution_result,
        review_context=review_context,
        policy=policy,
    )
    comparison = build_owner_review_summary(task, execution_result, review_context=review_context)

    return "\n".join(
        [
            f"# {evaluator['heading']}",
            "",
            *evaluator.get("mandate", []),
            "",
            f"## Task: {title}",
            "",
            "### Description",
            description,
            "",
            f"## Contract vs Actual Delivery (threshold >= {evaluator['approval_threshold']})",
            comparison["summary_text"],
            "",
            "## Stage Scoring Template",
            f"Score every dimension and contract criterion from {evaluator['score_range']}.",
            evaluator["scoring_guidance"],
            "",
            "### Owner Review Dimensions",
            _format_dimension_lines(evaluator),
            "",
            f"### {evaluator['criteria_label']} — {evaluator['contract_source']} "
            f"(score each {evaluator['score_range']})",
            _format_contract_lines(evaluator["contract_criteria"]),
            "",
            f"Total contract criteria to score: {len(evaluator['contract_criteria'])}",
            "",
            "## Required Checks",
            _format_check_lines(evaluator),
            "",
            "## Required Output (STRICT FORMAT)",
            _format_required_output_block("owner-review", evaluator),
        ]
    )


def build_adversarial_code_review_prompt(
    task: dict[str, Any],
    git_diff: str,
    execution_result: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
) -> str:
    """Build an adversarial reviewer prompt for an independent code review CC session."""
    title = task.get("title", "Untitled")
    description = task.get("description") or "_No description_"
    evaluator = build_contract_aware_evaluator(
        "code-review",
        task,
        execution_result=execution_result,
        policy=policy,
    )
    mandate = list(evaluator.get("mandate", []))

    numbered_mandate = [f"{index}. {line}" for index, line in enumerate(mandate, start=1)]
    numbered_mandate.append(
        f"{len(numbered_mandate) + 1}. Evidence required: Every finding MUST include file path, line number, and what you observed."
    )
    numbered_mandate.append(
        f"{len(numbered_mandate) + 1}. No benefit of the doubt: If something looks wrong but you're unsure, flag it as a finding."
    )

    return "\n".join(
        [
            f"# {evaluator['heading']}",
            "",
            "You are an adversarial code reviewer. Your job is to TRY TO BREAK THIS CODE.",
            "Do NOT be generous. Do NOT praise the work. Resist the urge to approve.",
            "Assume the code is guilty until proven innocent.",
            "You are NOT the author of this code. Provide a ruthlessly honest, independent review.",
            "",
            "## Your Adversarial Mandate",
            "",
            *numbered_mandate,
            "",
            "## Bash Tool Access",
            "",
            "You have access to the Bash tool. USE IT. Do not just read the diff — actively verify:",
            "",
            "- Run tests: Execute the project's test suite. Report exact failures.",
            "- Run linters: Execute linters (ruff, mypy, eslint, tsc). Report violations.",
            "- Check file existence: Verify imported modules and referenced files exist.",
            "- Grep for patterns: Search for security anti-patterns, hardcoded secrets, TODO/FIXME left behind.",
            "- Verify boundaries: If the task specifies allowed paths, verify no files outside those paths were changed.",
            "",
            "If you cannot run a verification command, state that explicitly in your findings.",
            "",
            "## Review Dimensions (score each 1-10)",
            "",
            (
                f"Score each dimension independently against the contract. "
                f"Threshold: ALL scores must be >= {evaluator['approval_threshold']} to APPROVE."
            ),
            evaluator["scoring_guidance"],
            "",
            _format_dimension_lines(evaluator),
            "",
            "## Required Checks",
            _format_check_lines(evaluator),
            "",
            f"## Task: {title}",
            "",
            "### Description",
            description,
            "",
            f"### {evaluator['criteria_label']} — {evaluator['contract_source']} "
            f"(score each {evaluator['score_range']})",
            _format_contract_lines(evaluator["contract_criteria"]),
            "",
            f"Total contract criteria to score: {len(evaluator['contract_criteria'])}",
            "",
            "## Git Diff to Review",
            "```diff",
            git_diff[:50000] if git_diff else "_No diff available_",
            "```",
            "",
            "## Required Output (STRICT FORMAT)",
            "",
            "After your adversarial review, output EXACTLY this JSON block.",
            "Do NOT output anything after the JSON block.",
            "",
            "```",
            "CODE_REVIEW_VERDICT: APPROVE|REQUEST_CHANGES",
            "CODE_REVIEW_SCORES: {",
            _format_dimension_output_lines(evaluator),
            f'  "{evaluator["criteria_output_key"]}": [',
            '    {"criterion": "<text>", "score": <1-10>, "evidence": "<contract evidence and verification steps>"}',
            "  ],",
            '  "lowest_score": <1-10>,',
            '  "verdict_reason": "<one sentence: why approve or reject>"',
            "}",
            'CODE_REVIEW_FINDINGS: [{"severity":"critical|warning|suggestion","category":"...","description":"...","file":"...","line":"..."}]',
            "```",
            "",
            "## Scoring Rules",
            "",
            (
                f"- APPROVE only if ALL dimension scores >= {evaluator['approval_threshold']} "
                f"AND ALL contract criteria scores >= {evaluator['approval_threshold']}"
            ),
            f"- REQUEST_CHANGES if ANY score < {evaluator['approval_threshold']}",
            "- If you could not verify something (e.g., tests wouldn't run), score it 5 max",
            "- Empty findings list is suspicious — look harder",
        ]
    )


def _format_contract_lines(criteria: list[dict[str, Any]]) -> str:
    if not criteria:
        return "1. Task completed as described — pass condition: Behavior matches the task description"

    lines: list[str] = []
    for index, criterion in enumerate(criteria, start=1):
        line = f"{index}. {criterion['criterion']}"
        threshold = criterion.get("threshold")
        if threshold:
            line += f" — pass condition: {threshold}"
        added_by = criterion.get("added_by")
        if added_by:
            line += f" [{added_by}]"
        lines.append(line)
    return "\n".join(lines)


def _format_dimension_lines(template: dict[str, Any]) -> str:
    return "\n".join(
        f"- {dimension['label']} (`{dimension['key']}`): {dimension['description']}"
        for dimension in template.get("dimensions", [])
    )


def _format_check_lines(template: dict[str, Any]) -> str:
    return "\n".join(f"- {line}" for line in template.get("required_checks", []))


def _format_dimension_output_lines(template: dict[str, Any]) -> str:
    dimensions = template.get("dimensions", [])
    lines = ['  "dimensions": {']
    for index, dimension in enumerate(dimensions):
        suffix = "," if index < len(dimensions) - 1 else ""
        lines.append(f'    "{dimension["key"]}": <1-10>{suffix}')
    lines.append("  },")
    return "\n".join(lines)


def _format_required_output_block(stage: str, evaluator: dict[str, Any]) -> str:
    criteria_output_key = evaluator.get("criteria_output_key", "contract_criteria")
    if stage == "architect-review":
        verdict_line = "ARCHITECT_REVIEW_VERDICT: APPROVE_CONTRACT|REVISE_CONTRACT"
        scores_label = "ARCHITECT_REVIEW_SCORES"
        findings_label = "ARCHITECT_REVIEW_FINDINGS"
        evidence_hint = "<why this criterion is measurable or what remains ambiguous>"
        summary_line = '  "contract_readiness_summary": "<one sentence>"'
        findings_hint = '[{"severity":"critical|warning|suggestion","category":"contract|scope|risk|verification","description":"..."}]'
    elif stage == "owner-review":
        verdict_line = "OWNER_REVIEW_VERDICT: APPROVE_DELIVERY|REJECT_DELIVERY|ESCALATE"
        scores_label = "OWNER_REVIEW_SCORES"
        findings_label = "OWNER_REVIEW_FINDINGS"
        evidence_hint = "<delivered evidence or unresolved gap>"
        summary_line = '  "ship_readiness_summary": "<one sentence>"'
        findings_hint = '[{"severity":"critical|warning|suggestion","category":"contract|delivery|risk|operations","description":"..."}]'
    else:
        raise ValueError(f"Unsupported stage for required output block: {stage}")

    return "\n".join(
        [
            "```",
            verdict_line,
            f"{scores_label}: {{",
            _format_dimension_output_lines(evaluator),
            f'  "{criteria_output_key}": [',
            f'    {{"criterion": "<text>", "score": <1-10>, "evidence": "{evidence_hint}"}}',
            "  ],",
            '  "lowest_score": <1-10>,',
            summary_line,
            "}",
            f"{findings_label}: {findings_hint}",
            "```",
        ]
    )
