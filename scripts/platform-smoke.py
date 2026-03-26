#!/usr/bin/env python3
"""
Cross-repo platform smoke test — core flow end-to-end.

Steps:
  1. Health-check platform services
  2. List projects and pick a target (or create a scratch project)
  3. Create a smoke task via API
  4. Poll until engine picks it up (status leaves 'approved')
  5. Verify execution run appears in Observability
  6. Verify board reflects the new state
  7. Clean up the test task

Exit 0 = all checks passed.  Exit 1 = at least one check failed.

Usage:
    python scripts/platform-smoke.py
    python scripts/platform-smoke.py --base-url http://localhost:8181
    python scripts/platform-smoke.py --project-id <uuid>
    python scripts/platform-smoke.py --timeout 120 --no-cleanup
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
SMOKE_TASK_TITLE = "[smoke] Cross-repo platform smoke test"
# How long to wait for engine to pick up task (seconds)
DEFAULT_ENGINE_TIMEOUT = 90
# How long to wait between polls (seconds)
POLL_INTERVAL = 5
# Statuses that indicate the engine has picked up the task
ENGINE_ACTIVE_STATUSES = {"assigned", "executing", "architect-review", "code-review", "review", "done", "failed"}


# ── Helpers ──────────────────────────────────────────────────────────────────


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
    """Resolve the Archon control-plane base URL from env or default."""
    return (
        os.getenv("LEANKIT_CONTROL_PLANE_URL")
        or os.getenv("ARCHON_SERVER_URL")
        or os.getenv("ARCHON_URL")
        or DEFAULT_BASE_URL
    ).rstrip("/")


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


class SmokeResult:
    """Collects per-check results and prints a final summary."""

    def __init__(self) -> None:
        self._checks: list[tuple[str, bool, str]] = []

    def record(self, label: str, ok: bool, detail: str = "") -> bool:
        banner = "PASS" if ok else "FAIL"
        line = f"  [{banner}] {label}"
        if detail:
            line += f" — {detail}"
        print(line)
        self._checks.append((label, ok, detail))
        return ok

    def all_passed(self) -> bool:
        return all(ok for _, ok, _ in self._checks)

    def summary(self) -> None:
        total = len(self._checks)
        passed = sum(1 for _, ok, _ in self._checks if ok)
        failed = total - passed
        print()
        print("─" * 60)
        print(f"  Smoke test complete: {passed}/{total} checks passed", end="")
        if failed:
            print(f"  ({failed} FAILED)")
        else:
            print("  — ALL PASS")
        print("─" * 60)


# ── Smoke steps ───────────────────────────────────────────────────────────────


def check_platform_health(base_url: str, result: SmokeResult) -> bool:
    """Step 1 — verify core services respond."""
    print("\n[1] Platform health")
    ok, body = _request("GET", f"{base_url}/api/services/health")
    if ok:
        status = body.get("status", "unknown")
        result.record("Service health endpoint", True, f"status={status}")
    else:
        result.record("Service health endpoint", False, str(body.get("detail", body)))

    ok2, body2 = _request("GET", f"{base_url}/api/projects/health")
    if ok2:
        schema_ok = body2.get("schema", {}).get("valid", False)
        result.record("Projects/tasks schema", schema_ok, f"schema_valid={schema_ok}")
    else:
        result.record("Projects/tasks schema", False, str(body2.get("detail", body2)))

    return ok and ok2


def resolve_project(base_url: str, project_id: str | None, result: SmokeResult) -> str | None:
    """Step 2 — pick or create a scratch project, return its ID."""
    print("\n[2] Resolve target project")
    if project_id:
        ok, body = _request("GET", f"{base_url}/api/projects/{project_id}")
        if ok and body.get("id"):
            result.record("Project lookup (by arg)", True, f"id={project_id}")
            return project_id
        result.record("Project lookup (by arg)", False, f"id={project_id} not found")
        return None

    # Use first available project
    ok, body = _request("GET", f"{base_url}/api/projects?include_content=false")
    if not ok:
        result.record("List projects", False, str(body.get("detail", body)))
        return None

    projects = body.get("projects", [])
    if projects:
        pid = projects[0]["id"]
        result.record("List projects", True, f"using first project id={pid}")
        return pid

    result.record("List projects", False, "no projects found — create one first")
    return None


def create_smoke_task(base_url: str, project_id: str, result: SmokeResult) -> str | None:
    """Step 3 — create a smoke task in 'approved' status so the engine can pick it up."""
    print("\n[3] Create smoke task")
    payload = {
        "project_id": project_id,
        "title": SMOKE_TASK_TITLE,
        "description": "Automated smoke task — safe to delete.",
        "status": "approved",
        "assignee": "Archon",
        "priority": "low",
        "complexity": "simple",
    }
    ok, body = _request("POST", f"{base_url}/api/tasks", payload=payload)
    if not ok:
        result.record("Create smoke task", False, str(body.get("detail", body)))
        return None
    task = body.get("task") or body
    task_id = task.get("id")
    if not task_id:
        result.record("Create smoke task", False, f"no id in response: {body}")
        return None
    result.record("Create smoke task", True, f"task_id={task_id} status={task.get('status')}")
    return task_id


def poll_engine_pickup(
    base_url: str, task_id: str, timeout_sec: int, result: SmokeResult
) -> tuple[bool, str]:
    """Step 4 — poll until engine moves task out of 'approved' (or timeout)."""
    print(f"\n[4] Wait for engine pickup (timeout={timeout_sec}s)")
    deadline = time.monotonic() + timeout_sec
    last_status = "approved"
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        ok, body = _request("GET", f"{base_url}/api/tasks/{task_id}")
        if ok:
            task = body.get("task") or body
            current_status = task.get("status", "unknown")
            if current_status != last_status:
                print(f"  status changed: {last_status} → {current_status}")
                last_status = current_status
            if current_status in ENGINE_ACTIVE_STATUSES:
                elapsed = timeout_sec - (deadline - time.monotonic())
                result.record(
                    "Engine picked up task",
                    True,
                    f"status={current_status} after ~{elapsed:.0f}s",
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


def check_observability(base_url: str, task_id: str, result: SmokeResult) -> bool:
    """Step 5 — verify at least one execution run appears for this task."""
    print("\n[5] Observability — execution runs")
    ok, body = _request("GET", f"{base_url}/api/execution-runs?task_id={task_id}&limit=10")
    if not ok:
        result.record("Execution runs endpoint reachable", False, str(body.get("detail", body)))
        return False
    result.record("Execution runs endpoint reachable", True)

    runs = body.get("runs", [])
    has_runs = len(runs) > 0
    if has_runs:
        latest = runs[0]
        result.record(
            "Execution run recorded",
            True,
            f"count={len(runs)} latest_status={latest.get('status')} stage={latest.get('stage')}",
        )
    else:
        result.record("Execution run recorded", False, "no execution runs found for task")
    return has_runs


def check_board_state(
    base_url: str, project_id: str, task_id: str, expected_status: str, result: SmokeResult
) -> bool:
    """Step 6 — verify project task board reflects the task's current status."""
    print("\n[6] Board state")
    ok, body = _request("GET", f"{base_url}/api/projects/{project_id}/tasks")
    if not ok:
        result.record("Project tasks endpoint reachable", False, str(body.get("detail", body)))
        return False
    result.record("Project tasks endpoint reachable", True)

    tasks = body.get("tasks", [])
    found = next((t for t in tasks if t.get("id") == task_id), None)
    if found:
        board_status = found.get("status", "unknown")
        matches = board_status == expected_status
        result.record(
            "Board reflects task status",
            matches,
            f"board_status={board_status} expected={expected_status}",
        )
        return matches
    else:
        result.record("Task visible on board", False, f"task_id={task_id} not found in project tasks")
        return False


def cleanup_task(base_url: str, task_id: str, result: SmokeResult) -> None:
    """Step 7 — delete the smoke task."""
    print("\n[7] Cleanup")
    ok, body = _request("DELETE", f"{base_url}/api/tasks/{task_id}")
    if ok:
        result.record("Delete smoke task", True, f"task_id={task_id}")
    else:
        # Non-fatal: only warn
        result.record(
            "Delete smoke task (non-fatal)",
            False,
            str(body.get("detail", body)),
        )


# ── Main flow ─────────────────────────────────────────────────────────────────


def run_smoke(
    base_url: str,
    project_id: str | None,
    engine_timeout: int,
    no_cleanup: bool,
) -> bool:
    result = SmokeResult()
    task_id: str | None = None
    final_status = "approved"

    print("=" * 60)
    print(f"  Archon Platform Smoke Test")
    print(f"  Base URL : {base_url}")
    print(f"  Engine timeout: {engine_timeout}s")
    print("=" * 60)

    # Step 1
    health_ok = check_platform_health(base_url, result)
    if not health_ok:
        print("\n  Platform health failed — aborting remaining steps.")
        result.summary()
        return result.all_passed()

    # Step 2
    resolved_project_id = resolve_project(base_url, project_id, result)
    if not resolved_project_id:
        print("\n  No project available — aborting remaining steps.")
        result.summary()
        return result.all_passed()

    # Step 3
    task_id = create_smoke_task(base_url, resolved_project_id, result)
    if not task_id:
        result.summary()
        return result.all_passed()

    # Step 4
    engine_ok, final_status = poll_engine_pickup(base_url, task_id, engine_timeout, result)

    # Step 5 — check observability regardless of engine pickup
    check_observability(base_url, task_id, result)

    # Step 6 — re-fetch current status for board check
    ok, body = _request("GET", f"{base_url}/api/tasks/{task_id}")
    if ok:
        task = body.get("task") or body
        final_status = task.get("status", final_status)
    check_board_state(base_url, resolved_project_id, task_id, final_status, result)

    # Step 7 — cleanup
    if not no_cleanup and task_id:
        cleanup_task(base_url, task_id, result)
    elif no_cleanup:
        print(f"\n[7] Cleanup skipped (--no-cleanup).  Task id={task_id}")

    result.summary()
    return result.all_passed()


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
        help="Target project UUID.  If omitted, the first available project is used.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_ENGINE_TIMEOUT,
        metavar="SECONDS",
        help=f"How long to wait for engine to pick up task (default: {DEFAULT_ENGINE_TIMEOUT}s)",
    )
    parser.add_argument(
        "--no-cleanup",
        action="store_true",
        help="Skip deletion of the smoke task after the test",
    )
    return parser


def main() -> int:
    _load_repo_env_defaults()
    parser = _build_parser()
    args = parser.parse_args()
    base_url = (args.base_url or _resolve_base_url()).rstrip("/")
    passed = run_smoke(
        base_url=base_url,
        project_id=args.project_id,
        engine_timeout=args.timeout,
        no_cleanup=args.no_cleanup,
    )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
