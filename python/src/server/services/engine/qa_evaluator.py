"""QA Evaluator — adversarial testing agent for the qa-eval lifecycle stage.

Reads the locked contract from architect-review, builds a hostile evaluator
prompt, and parses structured scoring output.  The evaluator acts as a hostile
user (not a peer reviewer) — it probes endpoints, tests UI, tries to break
things, and scores each contract criterion 1-10.

The evaluator MUST kill all background processes it spawns before exiting.
"""

import json
import re
from typing import Any

from ...config.logfire_config import get_logger
from .qa_evaluator_templates import get_qa_eval_probe_instructions

logger = get_logger(__name__)

# ── Output parsing ────────────────────────────────────────────────────────

_QA_SCORES_RE = re.compile(
    r"QA_EVAL_SCORES:\s*(\{.*?\})\s*(?:QA_EVAL_FINDINGS:|QA_EVAL_VERDICT:|$)",
    re.IGNORECASE | re.DOTALL,
)

_QA_VERDICT_RE = re.compile(
    r"QA_EVAL_VERDICT:\s*(PASS|FAIL|ESCALATE)",
    re.IGNORECASE,
)

_QA_FINDINGS_RE = re.compile(
    r"QA_EVAL_FINDINGS:\s*(\[.*?\])\s*(?:QA_EVAL_VERDICT:|QA_EVAL_SCORES:|$)",
    re.IGNORECASE | re.DOTALL,
)

# Minimum average score (1-10) required to auto-pass qa-eval
QA_EVAL_PASS_THRESHOLD = 7.0


def parse_qa_eval_verdict(stdout: str) -> str:
    """Extract QA_EVAL_VERDICT from evaluator stdout.  Returns uppercase or 'UNKNOWN'."""
    if not stdout:
        return "UNKNOWN"
    match = _QA_VERDICT_RE.search(stdout)
    return match.group(1).upper() if match else "UNKNOWN"


def parse_qa_eval_scores(stdout: str) -> dict[str, Any] | None:
    """Parse the QA_EVAL_SCORES JSON block from evaluator stdout.

    Expected shape::

        {
            "criteria": [
                {"criterion": "...", "score": 8, "evidence": "...", "notes": "..."},
                ...
            ],
            "average_score": 7.5
        }
    """
    if not stdout:
        return None
    match = _QA_SCORES_RE.search(stdout)
    if not match:
        return None
    try:
        scores = json.loads(match.group(1).strip())
    except json.JSONDecodeError:
        return None
    if not isinstance(scores, dict):
        return None
    return scores


def parse_qa_eval_findings(stdout: str) -> list[dict[str, Any]]:
    """Parse the QA_EVAL_FINDINGS JSON array from evaluator stdout."""
    if not stdout:
        return []
    match = _QA_FINDINGS_RE.search(stdout)
    if not match:
        return []
    try:
        findings = json.loads(match.group(1).strip())
    except json.JSONDecodeError:
        return []
    if not isinstance(findings, list):
        return []
    return findings


def compute_qa_eval_average(scores: dict[str, Any] | None) -> float:
    """Compute the average criterion score from parsed QA_EVAL_SCORES.

    Falls back to the pre-computed ``average_score`` field when present,
    otherwise computes the mean from individual criteria entries.
    Returns 0.0 when no scores are available.
    """
    if not scores:
        return 0.0
    if "average_score" in scores and isinstance(scores["average_score"], int | float):
        return float(scores["average_score"])
    criteria = scores.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        return 0.0
    numeric = [float(c["score"]) for c in criteria if isinstance(c, dict) and isinstance(c.get("score"), int | float)]
    return sum(numeric) / len(numeric) if numeric else 0.0


# ── Prompt builder ────────────────────────────────────────────────────────

def build_qa_eval_prompt(
    task: dict[str, Any],
    locked_contract: list[dict[str, Any]],
    git_diff: str = "",
    build_command: str = "",
    project_type: str = "",
) -> str:
    """Build the adversarial QA evaluator prompt.

    The evaluator receives:
    - Contract criteria (formal-contract-first, mirror as fallback)
    - The task description and acceptance criteria
    - A git diff of changes
    - Build / start commands

    It must act as a *hostile user*, not a peer reviewer.

    Contract resolution order (H4-R2: formal-contract-first):
    1. ``_formal_contract_criteria`` on task (DB-sourced via current_contract_id)
    2. ``locked_contract`` parameter (from caller, usually mirror)
    3. Empty list (untestable)
    """
    task_title = task.get("title", "Untitled")
    task_description = task.get("description", "")
    acceptance_criteria = task.get("acceptance_criteria") or []

    # Prefer formal contract criteria injected by task_engine
    effective_contract = task.get("_formal_contract_criteria")
    if not isinstance(effective_contract, list) or not effective_contract:
        effective_contract = locked_contract

    contract_text = _format_contract(effective_contract)
    acceptance_text = _format_acceptance_criteria(acceptance_criteria)

    diff_section = ""
    if git_diff:
        trimmed = git_diff[:8000]
        if len(git_diff) > 8000:
            trimmed += "\n... [diff truncated]"
        diff_section = f"\n## Git Diff (for reference — do NOT trust claims, verify behaviour)\n```\n{trimmed}\n```\n"

    build_section = ""
    if build_command:
        build_section = f"\n## Build & Start Command\n```bash\n{build_command}\n```\n"

    probe_section = "\n" + get_qa_eval_probe_instructions(project_type or "general-app")

    return f"""# QA Evaluator — Adversarial Testing Agent

You are a **hostile QA evaluator**. Your job is to **break things**, not rubber-stamp them.
You are NOT a peer reviewer — you are a hostile user trying to find every flaw.

## Task Under Test
**Title**: {task_title}
**Description**: {task_description}

{acceptance_text}

## Locked Contract (from architect-review)
{contract_text}

{diff_section}
{build_section}
{probe_section}
## Your Mission

1. **Build the app**: Run the build command. If it fails, score = 0 for all criteria.
2. **Start the app**: If there's a server, start it. Record the PID.
3. **Probe every contract criterion**: For each criterion, actively test it.
   - Hit endpoints with valid AND invalid inputs
   - Try edge cases, boundary values, empty strings, huge payloads
   - Check error responses, status codes, response shapes
   - Verify negative tests (what MUST NOT happen)
4. **Score each criterion 1-10**:
   - 1-3: Broken or missing functionality
   - 4-6: Partially working, issues found
   - 7-8: Working correctly with minor concerns
   - 9-10: Solid, handles edge cases well
5. **Kill ALL background processes** you started before exiting.

## CRITICAL RULES
- You have read-only access to source code + Bash for running commands
- You MUST kill all background processes (servers, watchers) before you finish
- Do NOT modify any source files
- Do NOT trust the code review's claims — verify everything yourself
- If you cannot test a criterion (e.g., no server to hit), score it 1 and note why

## Required Output Format

Output these blocks at the END of your response:

```
QA_EVAL_SCORES: {{"criteria": [{{"criterion": "<contract criterion text>", "score": <1-10>, "evidence": "<what you tested>", "notes": "<issues found or 'none'>"}}], "average_score": <float>}}
QA_EVAL_FINDINGS: [{{"severity": "critical|major|minor|info", "category": "functional|security|performance|compatibility", "description": "<what's wrong>", "evidence": "<reproduction steps>"}}]
QA_EVAL_VERDICT: PASS|FAIL|ESCALATE
```

- PASS: average score >= {QA_EVAL_PASS_THRESHOLD} AND no critical findings
- FAIL: average score < {QA_EVAL_PASS_THRESHOLD} OR critical findings exist
- ESCALATE: unable to test (build fails, app won't start, etc.)
"""


def _format_contract(locked_contract: list[dict[str, Any]]) -> str:
    if not locked_contract:
        return "_No locked contract available — score all criteria as 1 (untestable)._"
    lines = []
    for i, criterion in enumerate(locked_contract, 1):
        text = criterion.get("criterion", "???")
        threshold = criterion.get("threshold", "N/A")
        category = criterion.get("category", "unknown")
        lines.append(f"{i}. **[{category}]** {text}\n   Threshold: {threshold}")
    return "\n".join(lines)


def _format_acceptance_criteria(criteria: list[Any]) -> str:
    if not criteria:
        return ""
    lines = ["## Acceptance Criteria"]
    for i, c in enumerate(criteria, 1):
        if isinstance(c, dict):
            lines.append(f"{i}. {c.get('description', c.get('text', str(c)))}")
        else:
            lines.append(f"{i}. {c}")
    return "\n".join(lines)
