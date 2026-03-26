#!/usr/bin/env python3
"""
CI-7: Multi-Project Registration Dry-Run Verification

Registers a project with tech_stack=nextjs via POST /api/projects, then calls
the bootstrap plan preview endpoint (dry_run=true) to verify that scaffold,
validation, and follow-up tasks are sensible without materialising any tasks in
the database.

Usage:
    python scripts/verify_multi_project_bootstrap.py [--base-url URL] [--project-title TITLE]

Exit codes:
    0  All checks passed
    1  One or more checks failed (details printed to stdout)
    2  Network or unexpected error
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = "http://localhost:8181"

REQUIRED_PLAN_KEYS = {"scaffold", "validation", "followup"}


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
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _api(base_url: str, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Perform a JSON HTTP request and return the parsed response body."""
    url = base_url.rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {exc.code} {exc.reason} — {url}\n{raw}") from exc


# ---------------------------------------------------------------------------
# Verification helpers
# ---------------------------------------------------------------------------

class VerificationResult:
    def __init__(self) -> None:
        self.passed: list[str] = []
        self.failed: list[str] = []

    def ok(self, msg: str) -> None:
        self.passed.append(msg)
        print(f"  [PASS] {msg}")

    def fail(self, msg: str) -> None:
        self.failed.append(msg)
        print(f"  [FAIL] {msg}")

    def check(self, condition: bool, pass_msg: str, fail_msg: str) -> None:
        if condition:
            self.ok(pass_msg)
        else:
            self.fail(fail_msg)


def _verify_project_registration(project: dict[str, Any], result: VerificationResult) -> str:
    """Verify the project was registered and return its ID."""
    project_id = project.get("project_id") or ""
    result.check(bool(project_id), "project_id is present", "project_id is missing in creation response")

    nested = project.get("project") or {}
    title = nested.get("title") or project.get("title") or ""
    result.check(bool(title), f"project title returned: '{title}'", "project title is missing")

    return str(project_id)


def _verify_dry_run_plan(preview: dict[str, Any], result: VerificationResult) -> None:
    """Verify the dry-run bootstrap plan structure."""
    result.check(preview.get("dry_run") is True, "dry_run=true confirmed in response", "dry_run flag missing or false")

    plan_items: list[dict[str, Any]] = preview.get("plan_items") or []
    backlog_items: list[dict[str, Any]] = preview.get("backlog_items") or []

    result.check(len(plan_items) >= 2, f"plan_items count={len(plan_items)} (>=2)", f"too few plan_items: {len(plan_items)}")

    found_keys = {item.get("key") for item in plan_items}
    for required_key in REQUIRED_PLAN_KEYS:
        result.check(
            required_key in found_keys,
            f"required task key present: '{required_key}'",
            f"required task key missing: '{required_key}'",
        )

    _verify_task_items(plan_items, "plan_items", result)
    _verify_dependency_chain(plan_items, result)

    result.ok(f"backlog_items count={len(backlog_items)}")

    project_type = preview.get("project_type") or ""
    strategy = preview.get("strategy") or ""
    result.check(bool(project_type), f"project_type returned: '{project_type}'", "project_type missing in preview")
    result.check(bool(strategy), f"strategy returned: '{strategy}'", "strategy missing in preview")

    total = preview.get("total_task_count", -1)
    expected_total = len(plan_items) + len(backlog_items)
    result.check(
        total == expected_total,
        f"total_task_count={total} matches plan+backlog count",
        f"total_task_count={total} does not match plan+backlog sum ({expected_total})",
    )


def _verify_task_items(items: list[dict[str, Any]], label: str, result: VerificationResult) -> None:
    """Verify each task item has required fields populated."""
    required_fields = {"key", "title", "description", "task_type", "priority", "complexity",
                       "execution_prompt", "acceptance_criteria"}
    for item in items:
        key = item.get("key") or "(unknown)"
        missing = [f for f in required_fields if not item.get(f)]
        result.check(
            not missing,
            f"{label}[{key}] has all required fields",
            f"{label}[{key}] missing fields: {missing}",
        )
        ac = item.get("acceptance_criteria") or []
        result.check(
            len(ac) >= 1,
            f"{label}[{key}] has acceptance_criteria (count={len(ac)})",
            f"{label}[{key}] acceptance_criteria is empty",
        )


def _verify_dependency_chain(plan_items: list[dict[str, Any]], result: VerificationResult) -> None:
    """Verify that blocked_on_key references resolve to keys present in the plan."""
    known_keys = {item.get("key") for item in plan_items if item.get("key")}
    for item in plan_items:
        blocked_on = item.get("blocked_on_key")
        key = item.get("key") or "(unknown)"
        if blocked_on is None:
            continue
        result.check(
            blocked_on in known_keys,
            f"dependency '{key}' blocked_on_key='{blocked_on}' resolves",
            f"dependency '{key}' blocked_on_key='{blocked_on}' is unresolvable (not in plan)",
        )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(base_url: str, project_title: str) -> int:
    """Execute the multi-project registration dry-run and return an exit code."""
    print(f"\n=== CI-7 Multi-Project Bootstrap Dry-Run Verification ===")
    print(f"Server : {base_url}")
    print(f"Project: {project_title}\n")

    result = VerificationResult()

    # Step 1 — Register the project (creates real project + default bootstrap tasks)
    print("-- Step 1: Register project via POST /api/projects")
    try:
        project_response = _api(base_url, "POST", "/api/projects", {
            "title": project_title,
            "description": "CI-7 dry-run verification project — safe to delete",
            "github_repo": None,
            "project_type": "web-app",
            "bootstrap_template": "nextjs",
            "bootstrap_policy": "standard",
        })
    except RuntimeError as exc:
        print(f"  [ERROR] Failed to register project: {exc}")
        return 2

    project_id = _verify_project_registration(project_response, result)
    if not project_id:
        print("\n[ABORT] Cannot proceed without a valid project_id.\n")
        return 1

    print(f"  project_id = {project_id}\n")

    # Step 2 — Call the dry-run preview endpoint
    print("-- Step 2: Generate bootstrap plan preview (dry_run=true)")
    preview_path = f"/api/projects/{project_id}/bootstrap-plans/preview?dry_run=true"
    try:
        preview_response = _api(base_url, "POST", preview_path, {
            "project_title": project_title,
            "project_description": "CI-7 dry-run verification project",
            "github_repo": None,
            "project_type": "web-app",
            "bootstrap_template": "nextjs",
            "bootstrap_policy": "standard",
        })
    except RuntimeError as exc:
        print(f"  [ERROR] Failed to fetch dry-run preview: {exc}")
        return 2

    _verify_dry_run_plan(preview_response, result)

    # Step 3 — Confirm no tasks were created by the dry-run call
    print("\n-- Step 3: Confirm dry-run did NOT materialise tasks")
    try:
        plans_response = _api(base_url, "GET", f"/api/projects/{project_id}/bootstrap-plans")
        plans: list[dict[str, Any]] = plans_response.get("plans") or []
        # The project creation in Step 1 will have created one real plan.  The
        # dry-run must not have added a second one.
        result.check(
            len(plans) == 1,
            f"exactly 1 bootstrap plan persisted (dry-run did not create a second plan)",
            f"unexpected plan count={len(plans)} — dry-run may have persisted data",
        )
    except RuntimeError as exc:
        print(f"  [WARN] Could not verify plan count: {exc}")

    # Summary
    total = len(result.passed) + len(result.failed)
    print(f"\n=== Results: {len(result.passed)}/{total} checks passed ===")
    if result.failed:
        print("\nFailed checks:")
        for msg in result.failed:
            print(f"  - {msg}")
        print()
        return 1

    print("\nAll checks passed. Plan structure is sensible for operator review.\n")
    print("Dry-run plan summary:")
    plan_items = preview_response.get("plan_items") or []
    for item in plan_items:
        key = item.get("key", "?")
        title = item.get("title", "?")
        blocked = item.get("blocked_on_key")
        dep = f" (blocked_on: {blocked})" if blocked else ""
        print(f"  [{key}] {title}{dep}")
    backlog_items = preview_response.get("backlog_items") or []
    if backlog_items:
        print(f"\n  Backlog items ({len(backlog_items)}):")
        for item in backlog_items:
            print(f"    [{item.get('key', '?')}] {item.get('title', '?')}")
    print()
    return 0


def main() -> None:
    _load_repo_env_defaults()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default=os.environ.get("ARCHON_API_URL", DEFAULT_BASE_URL),
                        help=f"API base URL (default: {DEFAULT_BASE_URL})")
    parser.add_argument("--project-title", default="CI-7 NextJS Dry-Run Verification",
                        help="Title for the test project")
    args = parser.parse_args()
    sys.exit(run(args.base_url, args.project_title))


if __name__ == "__main__":
    main()
