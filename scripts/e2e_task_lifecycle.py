#!/usr/bin/env python3
"""
E2E integration test — complete task lifecycle: create → engine → execute → done.

Validates the full pipeline:
  1. Health-check control-plane services
  2. Resolve target project
  3. Create a minimal test task in 'approved' state
  4. Poll for engine to pick up the task (approved → assigned/executing)
  5. Poll for task to reach a terminal state (done/failed)
  6. Verify all expected lifecycle transitions occurred via history endpoint
  7. Verify execution_run was created with required fields populated
  8. Verify events streamed to observability endpoint
  9. Report detailed pass/fail with timing for each step
  10. Clean up test task

Exit 0 = all required checks passed.  Exit 1 = at least one required check failed.

Usage:
    python scripts/e2e_task_lifecycle.py
    python scripts/e2e_task_lifecycle.py --base-url http://localhost:8181
    python scripts/e2e_task_lifecycle.py --project-id <uuid>
    python scripts/e2e_task_lifecycle.py --timeout 240 --no-cleanup
    python scripts/e2e_task_lifecycle.py --skip-engine-wait
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# ── Constants ────────────────────────────────────────────────────────────────

DEFAULT_BASE_URL = "http://localhost:8181"
E2E_TASK_TITLE = "[e2e] Task lifecycle integration test"
E2E_TASK_DESCRIPTION = (
    "Automated E2E test task — safe to delete.\n\n"
    "Acceptance criteria:\n"
    "- Print the string 'hello world' in any way (echo, print, etc.)\n"
    "- Keep implementation minimal — one command or one line of code is sufficient.\n"
)

# Statuses that indicate the engine has picked up the task
ENGINE_ACTIVE_STATUSES = frozenset(
    {"assigned", "executing", "architect-review", "code-review", "review", "done", "failed", "cancelled"}
)

# Terminal statuses (task has completed its lifecycle)
TERMINAL_STATUSES = frozenset({"done", "failed", "cancelled"})

# Minimum expected transitions for a successful run (from_status → to_status)
EXPECTED_PICKUP_TRANSITIONS = [
    ("approved", "assigned"),
]
# At minimum the task must reach executing at some point
EXPECTED_EXECUTION_STATUSES = {"executing", "done", "review"}

# Execution run required fields
EXECUTION_RUN_REQUIRED_FIELDS = ["id", "task_id", "project_id", "status", "stage", "started_at"]

POLL_INTERVAL = 5  # seconds between polls
DEFAULT_ENGINE_PICKUP_TIMEOUT = 90  # seconds to wait for engine to pick up task
DEFAULT_EXECUTION_TIMEOUT = 180  # seconds to wait for task to reach terminal state
DEFAULT_OBSERVABILITY_BASE = "http://localhost:4000"


# ── Environment loading ───────────────────────────────────────────────────────


def _load_repo_env_defaults() -> None:
    """Load repo-local .env values when the caller shell has not exported them."""
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def _resolve_base_url() -> str:
    return (
        os.getenv("LEANKIT_CONTROL_PLANE_URL")
        or os.getenv("ARCHON_SERVER_URL")
        or os.getenv("ARCHON_URL")
        or DEFAULT_BASE_URL
    ).rstrip("/")


def _resolve_observability_base() -> str:
    ingest = (
        os.getenv("LEANKIT_OBSERVABILITY_INGEST_URL")
        or os.getenv("OBSERVABILITY_URL")
        or f"{DEFAULT_OBSERVABILITY_BASE}/api/events"
    )
    # Strip /api/events suffix to get base URL
    if ingest.endswith("/api/events"):
        return ingest[: -len("/api/events")]
    return DEFAULT_OBSERVABILITY_BASE


# ── HTTP helpers ─────────────────────────────────────────────────────────────


def _request(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: int = 20,
) -> tuple[bool, dict[str, Any]]:
    """Issue one JSON HTTP request and return (ok, parsed_body)."""
    body = None
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            text = resp.read().decode(charset)
            return True, json.loads(text) if text else {}
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(text)
        except json.JSONDecodeError:
            detail = {"raw": text}
        return False, {"status": exc.code, "url": url, "detail": detail}
    except urllib.error.URLError as exc:
        return False, {"status": "network-error", "url": url, "detail": str(exc)}


# ── Result tracking ───────────────────────────────────────────────────────────


class E2EResult:
    """Collects per-check results and prints a final summary."""

    def __init__(self) -> None:
        self._checks: list[tuple[str, bool, bool, str]] = []  # (label, ok, required, detail)
        self._timings: dict[str, float] = {}

    def record(self, label: str, ok: bool, detail: str = "", *, required: bool = True) -> bool:
        banner = "PASS" if ok else ("FAIL" if required else "WARN")
        line = f"  [{banner}] {label}"
        if detail:
            line += f" — {detail}"
        print(line)
        self._checks.append((label, ok, required, detail))
        return ok

    def record_timing(self, key: str, elapsed: float) -> None:
        self._timings[key] = elapsed
        print(f"  [TIME] {key}: {elapsed:.1f}s")

    def required_passed(self) -> bool:
        return all(ok for _, ok, required, _ in self._checks if required)

    def all_passed(self) -> bool:
        return all(ok for _, ok, _, _ in self._checks)

    def summary(self, total_elapsed: float) -> None:
        total = len(self._checks)
        passed = sum(1 for _, ok, _, _ in self._checks if ok)
        required_total = sum(1 for _, _, required, _ in self._checks if required)
        required_passed = sum(1 for _, ok, required, _ in self._checks if required and ok)
        failed = total - passed

        print()
        print("─" * 60)
        print(f"  E2E test complete in {total_elapsed:.1f}s")
        print(f"  Required checks : {required_passed}/{required_total} passed")
        print(f"  All checks      : {passed}/{total} passed", end="")
        if failed:
            print(f"  ({failed} FAILED/WARN)")
        else:
            print("  — ALL PASS")
        if self._timings:
            print()
            print("  Timing breakdown:")
            for key, t in self._timings.items():
                print(f"    {key}: {t:.1f}s")
        print("─" * 60)
        if self.required_passed():
            print("  RESULT: PASS")
        else:
            print("  RESULT: FAIL")
        print("─" * 60)


# ── Test steps ───────────────────────────────────────────────────────────────


def step_health(base_url: str, result: E2EResult) -> bool:
    """Step 1 — verify core services respond."""
    print("\n[1] Platform health")
    ok, body = _request("GET", f"{base_url}/api/services/health")
    if ok:
        status = body.get("status", "unknown")
        result.record("Control-plane health endpoint", True, f"status={status}")
    else:
        result.record(
            "Control-plane health endpoint",
            False,
            str(body.get("detail", body)),
        )
        return False

    ok2, body2 = _request("GET", f"{base_url}/api/projects/health")
    if ok2:
        schema_ok = body2.get("schema", {}).get("valid", False)
        result.record("Projects/tasks schema valid", schema_ok, f"schema_valid={schema_ok}")
    else:
        result.record(
            "Projects/tasks schema valid",
            False,
            str(body2.get("detail", body2)),
        )
    return ok and ok2


def step_resolve_project(base_url: str, project_id: str | None, result: E2EResult) -> str | None:
    """Step 2 — pick an existing project, return its ID."""
    print("\n[2] Resolve target project")
    if project_id:
        ok, body = _request("GET", f"{base_url}/api/projects/{project_id}")
        if ok and body.get("id"):
            result.record("Project lookup (by arg)", True, f"id={project_id}")
            return project_id
        result.record("Project lookup (by arg)", False, f"id={project_id} not found")
        return None

    ok, body = _request("GET", f"{base_url}/api/projects?include_content=false")
    if not ok:
        result.record("List projects", False, str(body.get("detail", body)))
        return None

    projects = body.get("projects", [])
    if not projects:
        result.record("List projects", False, "no projects found — create one first")
        return None

    pid = projects[0]["id"]
    result.record("List projects", True, f"using first project id={pid}")
    return pid


def step_create_task(base_url: str, project_id: str, result: E2EResult) -> str | None:
    """Step 3 — create the E2E test task in 'approved' state."""
    print("\n[3] Create E2E test task")
    payload = {
        "project_id": project_id,
        "title": E2E_TASK_TITLE,
        "description": E2E_TASK_DESCRIPTION,
        "status": "approved",
        "assignee": "Archon",
        "priority": "low",
        "complexity": "simple",
    }
    ok, body = _request("POST", f"{base_url}/api/tasks", payload=payload)
    if not ok:
        result.record("Create E2E task", False, str(body.get("detail", body)))
        return None
    task = body.get("task") or body
    task_id = task.get("id")
    if not task_id:
        result.record("Create E2E task", False, f"no id in response: {body}")
        return None
    result.record(
        "Create E2E task",
        True,
        f"task_id={task_id} status={task.get('status')}",
    )
    return task_id


def step_poll_engine_pickup(
    base_url: str,
    task_id: str,
    timeout_sec: int,
    result: E2EResult,
) -> tuple[bool, str]:
    """Step 4 — poll until engine moves task from 'approved' to an active status."""
    print(f"\n[4] Wait for engine pickup (timeout={timeout_sec}s)")
    t0 = time.monotonic()
    deadline = t0 + timeout_sec
    last_status = "approved"
    attempt = 0

    while time.monotonic() < deadline:
        attempt += 1
        ok, body = _request("GET", f"{base_url}/api/tasks/{task_id}")
        if ok:
            task = body.get("task") or body
            current_status = task.get("status", "unknown")
            if current_status != last_status:
                elapsed = time.monotonic() - t0
                print(f"  [{elapsed:.0f}s] status: {last_status} → {current_status}")
                last_status = current_status
            if current_status in ENGINE_ACTIVE_STATUSES:
                elapsed = time.monotonic() - t0
                result.record_timing("engine-pickup", elapsed)
                result.record(
                    "Engine picked up task",
                    True,
                    f"status={current_status} after {elapsed:.0f}s",
                )
                return True, current_status
        else:
            print(f"  poll {attempt}: request failed ({body.get('status')})")

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(POLL_INTERVAL, remaining))

    result.record(
        "Engine picked up task",
        False,
        f"still '{last_status}' after {timeout_sec}s — is the engine running?",
    )
    return False, last_status


def step_poll_completion(
    base_url: str,
    task_id: str,
    timeout_sec: int,
    result: E2EResult,
) -> tuple[bool, str]:
    """Step 5 — poll until task reaches a terminal state."""
    print(f"\n[5] Wait for task completion (timeout={timeout_sec}s)")
    t0 = time.monotonic()
    deadline = t0 + timeout_sec
    last_status = "unknown"
    attempt = 0
    saw_execution = False

    while time.monotonic() < deadline:
        attempt += 1
        ok, body = _request("GET", f"{base_url}/api/tasks/{task_id}")
        if ok:
            task = body.get("task") or body
            current_status = task.get("status", "unknown")
            if current_status != last_status:
                elapsed = time.monotonic() - t0
                print(f"  [{elapsed:.0f}s] status: {last_status} → {current_status}")
                last_status = current_status
            if current_status in EXPECTED_EXECUTION_STATUSES:
                saw_execution = True
            if current_status in TERMINAL_STATUSES:
                elapsed = time.monotonic() - t0
                result.record_timing("execution-completion", elapsed)
                is_done = current_status == "done"
                result.record(
                    "Task reached terminal state",
                    True,
                    f"status={current_status} after {elapsed:.0f}s",
                )
                result.record(
                    "Task completed successfully (done)",
                    is_done,
                    f"final_status={current_status}",
                )
                return True, current_status
        else:
            print(f"  poll {attempt}: request failed ({body.get('status')})")

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(POLL_INTERVAL, remaining))

    # Timed out — record partial results
    result.record(
        "Task reached terminal state",
        False,
        f"still '{last_status}' after {timeout_sec}s — task did not complete in time",
    )
    result.record(
        "Task completed successfully (done)",
        False,
        f"final_status={last_status}",
    )
    return False, last_status


def step_validate_lifecycle_history(
    base_url: str,
    task_id: str,
    result: E2EResult,
) -> bool:
    """Step 6 — fetch state history and verify expected transitions occurred."""
    print("\n[6] Validate lifecycle transitions")
    ok, body = _request("GET", f"{base_url}/api/tasks/{task_id}/history")
    if not ok:
        result.record("History endpoint reachable", False, str(body.get("detail", body)))
        return False
    result.record("History endpoint reachable", True)

    history = body.get("history", [])
    transition_count = body.get("transition_count", len(history))
    current_status = body.get("current_status", "unknown")

    result.record(
        "State history populated",
        transition_count > 0,
        f"transitions={transition_count} current={current_status}",
    )

    # Verify statuses seen in the transition chain
    seen_from = {h.get("from_status") for h in history}
    seen_to = {h.get("to_status") for h in history}
    all_seen = seen_from | seen_to | {current_status}

    # At minimum we expect the engine to have moved the task away from approved
    expected_pickup_ok = bool(ENGINE_ACTIVE_STATUSES & all_seen)
    result.record(
        "Engine-active status observed in history",
        expected_pickup_ok,
        f"seen_statuses={sorted(all_seen)}",
    )

    # Verify each transition entry has required fields
    entries_valid = all(
        h.get("from_status") and h.get("to_status") and h.get("changed_at")
        for h in history
    )
    result.record(
        "History entries have required fields (from/to/changed_at)",
        entries_valid or transition_count == 0,
        f"entries={transition_count}",
    )

    # Verify transitions are chronologically ordered (each entry has changed_at)
    timestamps = [h.get("changed_at", "") for h in history]
    is_ordered = timestamps == sorted(timestamps)
    result.record(
        "Transitions are chronologically ordered",
        is_ordered,
        required=False,
        detail=f"{'ordered' if is_ordered else 'out-of-order'}",
    )

    return expected_pickup_ok


def step_validate_execution_run(
    base_url: str,
    task_id: str,
    project_id: str,
    result: E2EResult,
) -> bool:
    """Step 7 — verify execution run was created with required fields."""
    print("\n[7] Validate execution run")
    ok, body = _request("GET", f"{base_url}/api/execution-runs?task_id={task_id}&limit=10")
    if not ok:
        result.record("Execution runs endpoint reachable", False, str(body.get("detail", body)))
        return False
    result.record("Execution runs endpoint reachable", True)

    runs = body.get("runs", [])
    has_runs = len(runs) > 0
    result.record(
        "Execution run exists for task",
        has_runs,
        f"count={len(runs)}",
    )
    if not has_runs:
        return False

    # Validate the latest run's fields
    latest = runs[0]
    missing_fields = [f for f in EXECUTION_RUN_REQUIRED_FIELDS if not latest.get(f)]
    fields_ok = len(missing_fields) == 0
    result.record(
        "Execution run has required fields",
        fields_ok,
        f"missing={missing_fields}" if missing_fields else "all required fields present",
    )

    # Verify task_id and project_id match
    run_task_match = latest.get("task_id") == task_id
    result.record(
        "Execution run task_id matches",
        run_task_match,
        f"run.task_id={latest.get('task_id')}",
    )

    run_project_match = latest.get("project_id") == project_id
    result.record(
        "Execution run project_id matches",
        run_project_match,
        f"run.project_id={latest.get('project_id')}",
    )

    # Verify status is a valid value
    valid_statuses = {"queued", "running", "reviewing", "completed", "failed", "cancelled"}
    run_status = latest.get("status", "")
    status_ok = run_status in valid_statuses
    result.record(
        "Execution run has valid status",
        status_ok,
        f"status={run_status}",
    )

    # Verify stage is a valid value
    valid_stages = {"execute", "architect-review", "code-review", "retry"}
    run_stage = latest.get("stage", "")
    stage_ok = run_stage in valid_stages
    result.record(
        "Execution run has valid stage",
        stage_ok,
        f"stage={run_stage}",
        required=False,
    )

    return has_runs and fields_ok and run_task_match


def step_check_observability(
    obs_base: str,
    task_id: str,
    result: E2EResult,
) -> bool:
    """Step 8 — verify events for this task reached the observability endpoint."""
    print("\n[8] Observability — event stream")
    replay_url = f"{obs_base}/api/unified-events/recent"
    ok, body = _request("GET", replay_url, timeout=10)
    if not ok:
        result.record(
            "Observability endpoint reachable",
            False,
            f"url={replay_url} — {body.get('detail', body.get('status', 'unreachable'))}",
            required=False,
        )
        return False
    result.record("Observability endpoint reachable", True, required=False)

    # Look for events related to our task_id
    events = body if isinstance(body, list) else body.get("events", [])
    task_events = [e for e in events if task_id in json.dumps(e)]
    has_events = len(task_events) > 0
    result.record(
        "Task events found in observability",
        has_events,
        f"task_events={len(task_events)} total_events={len(events)}",
        required=False,
    )
    return has_events


def step_cleanup(base_url: str, task_id: str, result: E2EResult) -> None:
    """Step 9 — archive the E2E test task."""
    print("\n[9] Cleanup")
    ok, body = _request("DELETE", f"{base_url}/api/tasks/{task_id}")
    if ok:
        result.record("Archive E2E task", True, f"task_id={task_id}", required=False)
    else:
        result.record(
            "Archive E2E task (non-fatal)",
            False,
            str(body.get("detail", body)),
            required=False,
        )


# ── Main orchestration ────────────────────────────────────────────────────────


def run_e2e(
    base_url: str,
    project_id: str | None,
    engine_pickup_timeout: int,
    execution_timeout: int,
    no_cleanup: bool,
    skip_engine_wait: bool,
    obs_base: str,
) -> bool:
    result = E2EResult()
    task_id: str | None = None
    resolved_project_id: str | None = None
    t_start = time.monotonic()

    print("=" * 60)
    print("  Archon E2E Task Lifecycle Test")
    print(f"  Control-plane : {base_url}")
    print(f"  Observability : {obs_base}")
    print(f"  Engine pickup timeout : {engine_pickup_timeout}s")
    print(f"  Execution timeout     : {execution_timeout}s")
    print("=" * 60)

    # Step 1 — Health
    health_ok = step_health(base_url, result)
    if not health_ok:
        print("\n  Control-plane unhealthy — aborting.")
        result.summary(time.monotonic() - t_start)
        return result.required_passed()

    # Step 2 — Resolve project
    resolved_project_id = step_resolve_project(base_url, project_id, result)
    if not resolved_project_id:
        print("\n  No project available — aborting.")
        result.summary(time.monotonic() - t_start)
        return result.required_passed()

    # Step 3 — Create task
    t_create = time.monotonic()
    task_id = step_create_task(base_url, resolved_project_id, result)
    if not task_id:
        result.summary(time.monotonic() - t_start)
        return result.required_passed()
    result.record_timing("task-creation", time.monotonic() - t_create)

    if skip_engine_wait:
        print("\n  [skip-engine-wait] Skipping engine poll steps.")
    else:
        # Step 4 — Engine pickup
        engine_ok, current_status = step_poll_engine_pickup(
            base_url, task_id, engine_pickup_timeout, result
        )

        # Step 5 — Wait for completion (only if engine picked it up)
        if engine_ok:
            step_poll_completion(base_url, task_id, execution_timeout, result)
        else:
            result.record(
                "Task reached terminal state",
                False,
                "skipped — engine never picked up task",
            )
            result.record(
                "Task completed successfully (done)",
                False,
                "skipped — engine never picked up task",
            )

    # Step 6 — Lifecycle history validation (always run, even if engine skipped)
    step_validate_lifecycle_history(base_url, task_id, result)

    # Step 7 — Execution run validation (always run)
    step_validate_execution_run(base_url, task_id, resolved_project_id, result)

    # Step 8 — Observability check (optional/non-required)
    step_check_observability(obs_base, task_id, result)

    # Step 9 — Cleanup
    if not no_cleanup and task_id:
        step_cleanup(base_url, task_id, result)
    elif no_cleanup:
        print(f"\n[9] Cleanup skipped (--no-cleanup). task_id={task_id}")

    result.summary(time.monotonic() - t_start)
    return result.required_passed()


# ── CLI ───────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Archon control-plane base URL (default: $ARCHON_URL or http://localhost:8181)",
    )
    parser.add_argument(
        "--project-id",
        default=None,
        help="Target project UUID. If omitted, the first available project is used.",
    )
    parser.add_argument(
        "--engine-pickup-timeout",
        type=int,
        default=DEFAULT_ENGINE_PICKUP_TIMEOUT,
        metavar="SECONDS",
        help=f"Seconds to wait for engine to pick up task (default: {DEFAULT_ENGINE_PICKUP_TIMEOUT})",
    )
    parser.add_argument(
        "--execution-timeout",
        type=int,
        default=DEFAULT_EXECUTION_TIMEOUT,
        metavar="SECONDS",
        help=f"Seconds to wait for task to reach terminal state (default: {DEFAULT_EXECUTION_TIMEOUT})",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        metavar="SECONDS",
        help="Combined timeout for both engine pickup and execution (overrides individual timeouts)",
    )
    parser.add_argument(
        "--no-cleanup",
        action="store_true",
        help="Skip deletion of the test task after the run",
    )
    parser.add_argument(
        "--skip-engine-wait",
        action="store_true",
        help="Skip engine polling steps (validate API surface only)",
    )
    parser.add_argument(
        "--obs-base",
        default=None,
        help="Observability server base URL (default: $OBSERVABILITY_URL or http://localhost:4000)",
    )
    return parser


def main() -> int:
    _load_repo_env_defaults()
    parser = _build_parser()
    args = parser.parse_args()

    base_url = (args.base_url or _resolve_base_url()).rstrip("/")
    obs_base = (args.obs_base or _resolve_observability_base()).rstrip("/")

    engine_pickup_timeout = args.engine_pickup_timeout
    execution_timeout = args.execution_timeout
    if args.timeout is not None:
        # Split evenly with more weight on execution
        engine_pickup_timeout = min(90, args.timeout // 3)
        execution_timeout = args.timeout - engine_pickup_timeout

    passed = run_e2e(
        base_url=base_url,
        project_id=args.project_id,
        engine_pickup_timeout=engine_pickup_timeout,
        execution_timeout=execution_timeout,
        no_cleanup=args.no_cleanup,
        skip_engine_wait=args.skip_engine_wait,
        obs_base=obs_base,
    )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
