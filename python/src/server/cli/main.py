"""
LeanKit CLI Toolkit.

Developer operations interface for LeanKit platform.
Complements Archon MCP, does not replace it.

Commands:
    leankit status          — Project summary
    leankit tasks           — List tasks with filters
    leankit task assign     — Assign task to agent/human
    leankit runtime start   — Start local runtime daemon
    leankit runtime status  — Show registered runtimes
    leankit runtime logs    — Tail runtime logs
    leankit sprint status   — Current sprint summary
    leankit sprint run      — Trigger sprint batch

Adopted from Multica CLI-first experience (Workstream M-P4-03/04/05).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

import httpx


DEFAULT_ARCHON_URL = "http://localhost:8100"


class LeankitCLI:
    """LeanKit CLI client for Archon API."""

    def __init__(self, archon_url: str = DEFAULT_ARCHON_URL) -> None:
        self.archon_url = archon_url.rstrip("/")

    def _api_sync(self, method: str, path: str, json_data: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Synchronous API call for CLI usage."""
        url = f"{self.archon_url}{path}"
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.request(method, url, json=json_data)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as e:
            print(f"Error {e.response.status_code}: {e.response.text}", file=sys.stderr)
            return None
        except Exception as e:
            print(f"Connection error: {e}", file=sys.stderr)
            return None

    # ── Status (M-P4-03) ─────────────────────────────────────────────────

    def cmd_status(self, args: argparse.Namespace) -> None:
        """Show project summary."""
        result = self._api_sync("GET", "/api/projects")
        if not result:
            return

        projects = result.get("projects") or result.get("data") or []
        if not projects:
            print("No projects found.")
            return

        print(f"{'Project':<40} {'Tasks':<8} {'Active Runs':<12}")
        print("-" * 60)
        for p in projects[:20]:
            name = p.get("name", p.get("id", "?"))[:39]
            task_count = p.get("task_count", "?")
            active_runs = p.get("active_run_count", "?")
            print(f"{name:<40} {task_count:<8} {active_runs:<12}")

    # ── Tasks (M-P4-03) ──────────────────────────────────────────────────

    def cmd_tasks(self, args: argparse.Namespace) -> None:
        """List tasks with optional filters."""
        params: list[str] = []
        if args.status:
            params.append(f"status={args.status}")
        if args.project:
            params.append(f"project_id={args.project}")
        if args.limit:
            params.append(f"limit={args.limit}")

        qs = "&".join(params)
        path = f"/api/projects/tasks?{qs}" if qs else "/api/projects/tasks"
        result = self._api_sync("GET", path)
        if not result:
            return

        tasks = result.get("tasks") or result.get("data") or []
        if not tasks:
            print("No tasks found.")
            return

        print(f"{'ID':<10} {'Status':<16} {'Priority':<10} {'Assignee':<12} {'Title'}")
        print("-" * 80)
        for t in tasks[:50]:
            tid = t.get("id", "?")[:9]
            status = t.get("status", "?")[:15]
            priority = t.get("priority", "-")[:9]
            a_type = t.get("assignee_type", "-")[:11]
            title = t.get("title", "?")[:40]
            print(f"{tid:<10} {status:<16} {priority:<10} {a_type:<12} {title}")

    def cmd_task_assign(self, args: argparse.Namespace) -> None:
        """Assign task to agent or human."""
        result = self._api_sync("POST", f"/api/runtimes/tasks/{args.task_id}/assign", json_data={
            "assignee_type": args.type,
            "assignee_id": args.assignee_id,
            "reason": args.reason,
        })
        if result:
            print(f"Task {args.task_id} assigned to {args.type}" +
                  (f" ({args.assignee_id})" if args.assignee_id else ""))

    # ── Runtime (M-P4-04) ────────────────────────────────────────────────

    def cmd_runtime_start(self, args: argparse.Namespace) -> None:
        """Start local runtime daemon."""
        from ..services.runtime_daemon import RuntimeDaemon

        daemon = RuntimeDaemon(
            archon_url=self.archon_url,
            max_concurrent_tasks=args.max_concurrent or 1,
            agent_id=args.agent_id,
        )

        print(f"Starting runtime daemon...")
        print(f"  Archon URL: {self.archon_url}")
        print(f"  Detected tools: {', '.join(daemon.supported_runners) or 'none'}")
        print(f"  Capabilities: {', '.join(daemon.capabilities[:5])}...")
        print(f"  Max concurrent: {daemon.max_concurrent_tasks}")
        print()

        if not daemon.supported_runners:
            print("Error: No CLI tools detected (claude, codex).", file=sys.stderr)
            sys.exit(1)

        try:
            asyncio.run(daemon.start())
        except KeyboardInterrupt:
            print("\nDaemon stopped.")

    def cmd_runtime_status(self, args: argparse.Namespace) -> None:
        """Show registered runtimes."""
        result = self._api_sync("GET", "/api/runtimes")
        if not result:
            return

        runtimes = result.get("runtimes", [])
        if not runtimes:
            print("No runtimes registered.")
            return

        print(f"{'ID':<10} {'Status':<10} {'Device':<25} {'Runners':<20} {'Tasks'}")
        print("-" * 75)
        for r in runtimes:
            rid = r.get("id", "?")[:9]
            status = r.get("status", "?")[:9]
            device = r.get("device_name", "?")[:24]
            runners = ", ".join(r.get("supported_runners", []))[:19]
            tasks = f"{r.get('current_task_count', 0)}/{r.get('max_concurrent_tasks', 1)}"
            print(f"{rid:<10} {status:<10} {device:<25} {runners:<20} {tasks}")

    def cmd_runtime_logs(self, args: argparse.Namespace) -> None:
        """Show runtime health info."""
        result = self._api_sync("GET", f"/api/runtimes/{args.runtime_id}/health")
        if result:
            print(json.dumps(result, indent=2))

    # ── Sprint (M-P4-05) ─────────────────────────────────────────────────

    def cmd_sprint_status(self, args: argparse.Namespace) -> None:
        """Show sprint summary."""
        result = self._api_sync("GET", "/api/sprint-stats")
        if not result:
            return
        print(json.dumps(result, indent=2))

    def cmd_sprint_run(self, args: argparse.Namespace) -> None:
        """Trigger sprint batch execution."""
        print("Sprint batch execution is managed via the Archon UI or MCP.")
        print("Use: leankit sprint status — to check current sprint state.")


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="leankit",
        description="LeanKit Platform CLI",
    )
    parser.add_argument("--url", default=DEFAULT_ARCHON_URL, help="Archon API URL")
    sub = parser.add_subparsers(dest="command")

    # status
    sub.add_parser("status", help="Project summary")

    # tasks
    tasks_p = sub.add_parser("tasks", help="List tasks")
    tasks_p.add_argument("--status", help="Filter by status")
    tasks_p.add_argument("--project", help="Filter by project ID")
    tasks_p.add_argument("--limit", type=int, default=20)

    # task assign
    assign_p = sub.add_parser("assign", help="Assign task")
    assign_p.add_argument("task_id", help="Task ID")
    assign_p.add_argument("--type", required=True, choices=["agent", "human", "unassigned"])
    assign_p.add_argument("--assignee-id", help="Assignee UUID")
    assign_p.add_argument("--reason", help="Assignment reason")

    # runtime
    rt_sub = sub.add_parser("runtime", help="Runtime management").add_subparsers(dest="runtime_cmd")

    rt_start = rt_sub.add_parser("start", help="Start daemon")
    rt_start.add_argument("--max-concurrent", type=int, default=1)
    rt_start.add_argument("--agent-id", help="Agent definition ID")

    rt_sub.add_parser("status", help="Show runtimes")

    rt_logs = rt_sub.add_parser("logs", help="Runtime logs")
    rt_logs.add_argument("runtime_id", help="Runtime ID")

    # sprint
    sp_sub = sub.add_parser("sprint", help="Sprint management").add_subparsers(dest="sprint_cmd")
    sp_sub.add_parser("status", help="Sprint summary")
    sp_sub.add_parser("run", help="Trigger sprint batch")

    return parser


def main() -> None:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    cli = LeankitCLI(archon_url=args.url)

    dispatch = {
        "status": cli.cmd_status,
        "tasks": cli.cmd_tasks,
        "assign": cli.cmd_task_assign,
    }

    if args.command in dispatch:
        dispatch[args.command](args)
    elif args.command == "runtime":
        rt_dispatch = {
            "start": cli.cmd_runtime_start,
            "status": cli.cmd_runtime_status,
            "logs": cli.cmd_runtime_logs,
        }
        if args.runtime_cmd in rt_dispatch:
            rt_dispatch[args.runtime_cmd](args)
        else:
            print("Usage: leankit runtime {start|status|logs}")
    elif args.command == "sprint":
        sp_dispatch = {
            "status": cli.cmd_sprint_status,
            "run": cli.cmd_sprint_run,
        }
        if args.sprint_cmd in sp_dispatch:
            sp_dispatch[args.sprint_cmd](args)
        else:
            print("Usage: leankit sprint {status|run}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
