"""QA Evaluator Templates — project-type-specific probing strategies.

Different project types require different adversarial evaluation approaches.
This module maps project types to tailored probe instructions and provides
policy-driven opt-out and type resolution.

Supported project types mirror the bootstrap planner's PROJECT_TYPE_TAGS:
  - api-service   : HTTP endpoint probing, status codes, error responses
  - web-app       : dev server startup, rendering checks, interaction tests
  - library       : import/call surface, type checks, edge-case inputs
  - automation    : runner execution, output/side-effect validation
  - general-app   : generic functional probing (fallback)

The special composite type ``full-stack`` combines api-service + web-app.

Template selection order (first match wins):
  1. Explicit ``project_type`` in ``qa_eval_policy`` section of policy
  2. Top-level ``project_type`` on the policy object
  3. Fallback to ``general-app``
"""

from __future__ import annotations

from typing import Any

# ── Recognised project types ──────────────────────────────────────────────────

QA_EVAL_PROJECT_TYPE_API = "api-service"
QA_EVAL_PROJECT_TYPE_WEB = "web-app"
QA_EVAL_PROJECT_TYPE_LIBRARY = "library"
QA_EVAL_PROJECT_TYPE_AUTOMATION = "automation"
QA_EVAL_PROJECT_TYPE_FULL_STACK = "full-stack"
QA_EVAL_PROJECT_TYPE_GENERAL = "general-app"

SUPPORTED_QA_EVAL_PROJECT_TYPES: frozenset[str] = frozenset(
    {
        QA_EVAL_PROJECT_TYPE_API,
        QA_EVAL_PROJECT_TYPE_WEB,
        QA_EVAL_PROJECT_TYPE_LIBRARY,
        QA_EVAL_PROJECT_TYPE_AUTOMATION,
        QA_EVAL_PROJECT_TYPE_FULL_STACK,
        QA_EVAL_PROJECT_TYPE_GENERAL,
    }
)

# ── Per-type probe instruction blocks ─────────────────────────────────────────

_API_PROBE_INSTRUCTIONS = """\
## Project Type: Backend API Service

Testing strategy — probe HTTP endpoints as a hostile client:

1. **Start the server**: Run the build/start command (e.g. `uvicorn`, `gunicorn`, `node server.js`).
   Record the process PID and the bound port.
2. **Discover endpoints**: Check `openapi.json`, README, or route definitions for the full surface.
3. **Happy-path probing**: Send well-formed requests and verify:
   - Correct HTTP status codes (200/201/204)
   - Response schema matches documented shape
   - Required fields are present and have the expected types
4. **Adversarial probing** — try each of the following:
   - Missing required fields → expect 400/422
   - Wrong field types (string where int expected, etc.) → expect 400/422
   - Oversized payloads and boundary values
   - Duplicate or idempotent requests (POST twice, etc.)
   - Unauthorised access if auth is in scope → expect 401/403
5. **Error response quality**: Verify error bodies contain structured details, not raw stack traces.
6. **Kill the server process** before exiting.
"""

_WEB_PROBE_INSTRUCTIONS = """\
## Project Type: Frontend Web Application

Testing strategy — verify rendering and interactions as a hostile user:

1. **Start the dev server**: Run the build/start command (e.g. `npm run dev`, `vite`, `next dev`).
   Record the process PID and the bound port (default: 3000/5173).
2. **Check page loads**: Use `curl` or a headless browser to fetch the root URL.
   Verify HTTP 200 and presence of expected HTML markers (title, root element).
3. **Asset delivery**: Verify JS/CSS bundles load without 404.
4. **Component rendering**: If using SSR or static export, check rendered HTML for
   required UI elements from the acceptance criteria.
5. **Console errors**: Look for JS runtime errors or failed network requests in
   the server-side output or by inspecting the HTML for error boundaries.
6. **Interaction paths**: If testable via CLI (Playwright, Puppeteer, curl form posts),
   exercise at least one primary user interaction described in the task.
7. **Kill the dev server process** before exiting.
"""

_LIBRARY_PROBE_INSTRUCTIONS = """\
## Project Type: Library / SDK

Testing strategy — exercise the public API as a downstream consumer:

1. **Install / build**: Run the build command. Verify the package is importable.
2. **Happy-path usage**: Import the module and call each public function/class
   documented in the acceptance criteria with valid inputs. Verify return values.
3. **Type contract checks**: Pass wrong types to typed functions — the library
   should raise clear errors, not silently coerce or corrupt data.
4. **Edge cases**:
   - Empty inputs (empty string, empty list, zero, None)
   - Boundary values (max int, very long strings)
   - Repeated calls (idempotency)
5. **Error message quality**: Verify exceptions include enough context for a caller
   to understand what went wrong without reading the source.
6. **No side effects**: Confirm functions that should be pure do not mutate inputs
   or write to disk/network unless documented.
"""

_AUTOMATION_PROBE_INSTRUCTIONS = """\
## Project Type: Automation / Worker

Testing strategy — run the automation and verify its outputs:

1. **Dry run**: Execute the automation with a minimal/safe sample input.
   Record stdout, stderr, and exit code.
2. **Output validation**: Check that expected output files, database rows,
   API calls, or messages were produced by the run.
3. **Error handling**: Provide invalid or missing inputs and verify the
   automation exits with a non-zero code and a meaningful error message
   rather than silently doing nothing or crashing with a traceback.
4. **Idempotency**: Run the automation twice on the same input. Verify
   no duplicate side effects (double-inserts, duplicate messages, etc.).
5. **Resource cleanup**: Check that temporary files, lock files, or
   background processes are cleaned up after the run finishes.
"""

_FULL_STACK_PROBE_INSTRUCTIONS = """\
## Project Type: Full-Stack Application

Testing strategy — probe both the API layer and the frontend, then verify integration:

### Backend API layer
1. Start the backend server. Record PID and port.
2. Probe key endpoints with valid and invalid inputs (see api-service strategy).
3. Verify HTTP status codes, response schemas, and error handling.

### Frontend layer
4. Start the frontend dev server. Record PID and port.
5. Fetch the root URL — verify HTTP 200 and expected HTML structure.
6. Check for JS errors and missing assets.

### Integration
7. Verify the frontend correctly calls the backend (look for configured API base URLs
   or proxy settings matching the running backend port).
8. Test at least one end-to-end user flow: frontend action → API call → expected response.

### Cleanup
9. Kill ALL background processes (backend + frontend) before exiting.
"""

_GENERAL_PROBE_INSTRUCTIONS = """\
## Project Type: General Application

Testing strategy — run the application and probe its primary functionality:

1. **Start the application**: Run the build/start command and verify it starts without errors.
2. **Basic smoke test**: Exercise the primary entry point described in the acceptance criteria.
3. **Error path**: Provide invalid or missing inputs and verify the application handles them
   gracefully with clear error messages.
4. **State validation**: Check that the application produces the expected outputs, files,
   or side effects described in the task.
5. **Cleanup**: Kill any background processes before exiting.
"""

# ── Template registry ──────────────────────────────────────────────────────────

_QA_EVAL_PROBE_INSTRUCTIONS: dict[str, str] = {
    QA_EVAL_PROJECT_TYPE_API: _API_PROBE_INSTRUCTIONS,
    QA_EVAL_PROJECT_TYPE_WEB: _WEB_PROBE_INSTRUCTIONS,
    QA_EVAL_PROJECT_TYPE_LIBRARY: _LIBRARY_PROBE_INSTRUCTIONS,
    QA_EVAL_PROJECT_TYPE_AUTOMATION: _AUTOMATION_PROBE_INSTRUCTIONS,
    QA_EVAL_PROJECT_TYPE_FULL_STACK: _FULL_STACK_PROBE_INSTRUCTIONS,
    QA_EVAL_PROJECT_TYPE_GENERAL: _GENERAL_PROBE_INSTRUCTIONS,
}


# ── Public API ─────────────────────────────────────────────────────────────────


def get_qa_eval_probe_instructions(project_type: str) -> str:
    """Return the project-type-specific probing instructions for the QA evaluator.

    Falls back to general-app instructions for unrecognised types.
    """
    return _QA_EVAL_PROBE_INSTRUCTIONS.get(project_type, _GENERAL_PROBE_INSTRUCTIONS)


def resolve_qa_eval_project_type(
    policy: dict[str, Any] | None,
    task: dict[str, Any] | None = None,
) -> str:
    """Determine the effective project type for QA evaluation.

    Resolution order (first non-empty value wins):
    1. ``policy["qa_eval_policy"]["project_type"]``
    2. ``policy["project_type"]``
    3. ``"general-app"`` fallback

    The ``task`` parameter is reserved for future project-metadata lookup
    and is not used in the current implementation.
    """
    if isinstance(policy, dict):
        qa_eval_policy = policy.get("qa_eval_policy")
        if isinstance(qa_eval_policy, dict):
            pt = qa_eval_policy.get("project_type")
            if isinstance(pt, str) and pt.strip() in SUPPORTED_QA_EVAL_PROJECT_TYPES:
                return pt.strip()

        pt = policy.get("project_type")
        if isinstance(pt, str) and pt.strip() in SUPPORTED_QA_EVAL_PROJECT_TYPES:
            return pt.strip()

    return QA_EVAL_PROJECT_TYPE_GENERAL


def is_qa_eval_disabled(policy: dict[str, Any] | None) -> bool:
    """Return True when the project policy explicitly opts out of qa-eval.

    Policy location: ``policy["qa_eval_policy"]["disabled"]``.
    Any truthy value (``True``, ``1``, ``"true"``) disables qa-eval.
    """
    if not isinstance(policy, dict):
        return False
    qa_eval_policy = policy.get("qa_eval_policy")
    if not isinstance(qa_eval_policy, dict):
        return False
    disabled = qa_eval_policy.get("disabled")
    if isinstance(disabled, bool):
        return disabled
    if isinstance(disabled, str):
        return disabled.strip().lower() in {"true", "1", "yes"}
    return bool(disabled)
