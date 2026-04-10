"""
Verification prompt templates for LeanKit V3 Goal-Backward Verification.

These templates define the 4-level verification checks used by the
VerificationAgent. Currently used for documentation and future LLM-powered
verification. The rule-based verifier in verification_agent.py uses
heuristic proxies for these checks.

Future enhancement: spawn a Haiku session with these prompts to read
actual codebase and verify each criterion with full code context.
"""

# Level 1: Existence check
EXISTS_CHECK_TEMPLATE = """\
Verify that the following artifacts exist in the codebase after task execution:

Criterion: {criterion}
Expected files/paths: {expected_paths}

Check:
- Do the expected files exist?
- Were they created or modified during this execution?
- Are the file names consistent with the project conventions?

Answer: EXISTS=true/false, EVIDENCE="..."
"""

# Level 2: Substantive check
SUBSTANTIVE_CHECK_TEMPLATE = """\
Verify that the implementation for this criterion is substantive (not a stub):

Criterion: {criterion}
Files to check: {files}

Check for red flags:
- TODO/FIXME comments in place of implementation
- Empty function bodies or pass statements
- Hardcoded return values (e.g., return [], return null, return "placeholder")
- Components that render null or empty elements
- Routes without actual handler logic
- Test files with only pending/skip markers

Answer: SUBSTANTIVE=true/false, EVIDENCE="..."
"""

# Level 3: Wiring check
WIRED_CHECK_TEMPLATE = """\
Verify that this implementation is wired into the rest of the codebase:

Criterion: {criterion}
Implementation files: {files}

Check:
- Is this module imported by other modules?
- Are its exports called/used by other code?
- Is it registered in routing, dependency injection, or configuration?
- Are there integration points (API routes registered, components rendered, etc.)?

Answer: WIRED=true/false, EVIDENCE="..."
"""

# Level 4: Data flow check
DATA_FLOW_CHECK_TEMPLATE = """\
Verify that this implementation processes real data, not hardcoded values:

Criterion: {criterion}
Implementation files: {files}

Check:
- Does it make real API calls (not mocked in production code)?
- Does it query actual database tables (not hardcoded arrays)?
- Does it handle user input (not placeholder values)?
- Are there live data transformations (not identity functions)?
- Do tests use realistic assertions (not trivial checks)?

Answer: DATA_FLOWS=true/false, EVIDENCE="..."
"""

# Summary prompt for verification report
VERIFICATION_SUMMARY_TEMPLATE = """\
Summarize the goal-backward verification results:

Task: {task_title}
Criteria verified: {criteria_count}
Passed: {passed_count}
Failed: {failed_count}

Gap details:
{gaps}

Provide a concise recommendation:
- APPROVE: All criteria met at level 2+ (substantive)
- FOLLOW_UP: Most criteria met, minor gaps can be addressed later
- REWORK: Significant gaps require re-execution
"""
