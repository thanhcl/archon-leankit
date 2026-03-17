#!/usr/bin/env python3
"""
LeanKit Task Engine — multi-project daemon entry point.

Fetches projects from Archon API, starts one TaskEngine per project,
and runs them concurrently. Each engine polls for assigned tasks
scoped to its project, spawns Claude Code sessions, and handles
the full task lifecycle.

Usage:
    uv run python start_engine.py
    Ctrl+C to gracefully shut down all engines.
"""

import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

# Add python dir to path so relative imports in src work
sys.path.insert(0, os.path.dirname(__file__))

from src.server.services.engine.task_engine import TaskEngine

ARCHON_API = os.environ.get("ARCHON_API_URL", "http://localhost:8181")


# ---------------------------------------------------------------------------
# Fetch projects from Archon API
# ---------------------------------------------------------------------------


def fetch_projects(max_retries: int = 3, delay: int = 5) -> list[dict]:
    """Fetch project configs from Archon API with retry."""
    for attempt in range(max_retries):
        try:
            url = f"{ARCHON_API}/api/projects/office-configs"
            resp = urlopen(Request(url), timeout=10)
            data = json.loads(resp.read())
            projects = data.get("projects", data) if isinstance(data, dict) else data

            result = []
            for p in projects:
                source_app = p.get("source_app")
                settings = p.get("office_settings") or {}
                project_path = settings.get("project_path")
                build_command = settings.get("build_command")

                if not project_path:
                    if source_app:
                        project_path = str(Path.home() / "Development" / "TrueAI" / source_app)
                    else:
                        print(f"  [warn] Skipping '{p.get('title')}' — no source_app or project_path")
                        continue

                if not Path(project_path).is_dir():
                    print(f"  [warn] Skipping '{p.get('title')}' — directory not found: {project_path}")
                    continue

                if not build_command:
                    pp = Path(project_path)
                    if (pp / "pnpm-lock.yaml").exists():
                        build_command = "pnpm build && pnpm test -- --run"
                    elif (pp / "yarn.lock").exists():
                        build_command = "yarn build && yarn test"
                    elif (pp / "package-lock.json").exists():
                        build_command = "npm run build && npm test"
                    elif (pp / "bun.lockb").exists():
                        build_command = "bun test"
                    elif (pp / "pom.xml").exists():
                        build_command = "mvn compile && mvn test"
                    elif (pp / "pyproject.toml").exists():
                        build_command = "uv run pytest tests/ -x --timeout=30"
                    else:
                        build_command = "echo 'no build command configured'"

                result.append({
                    "project_id": p["id"],
                    "name": p.get("title", source_app or "unknown"),
                    "source_app": source_app,
                    "path": project_path,
                    "build": build_command,
                })

            return result
        except Exception as e:
            print(f"  [warn] Archon unreachable (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(delay)

    print("  [error] Archon API unreachable after retries. Exiting.")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    print(f"[engine] Fetching projects from {ARCHON_API}...")
    projects = fetch_projects()

    if not projects:
        print("[engine] No projects found. Exiting.")
        sys.exit(0)

    print(f"[engine] Found {len(projects)} projects:")
    for p in projects:
        print(f"  - {p['name']} ({p['project_id'][:8]}...) -> {p['path']}")
        print(f"    build: {p['build']}")

    engines: list[tuple[str, TaskEngine]] = []
    for proj in projects:
        project_path = str(Path(proj["path"]).expanduser())
        engine = TaskEngine(
            project_path=project_path,
            project_id=proj["project_id"],
            build_command=proj["build"],
            poll_interval=30,
            max_parallel=3,
            default_timeout=600,
            shutdown_grace=60,
        )
        engines.append((proj["name"], engine))

    # Graceful shutdown via Ctrl+C / SIGTERM
    shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        if not shutdown_event.is_set():
            print("\n[engine] Shutdown signal received - stopping all engines...")
            shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    # Start all engines
    print(f"[engine] Starting {len(engines)} project engines...")
    for name, engine in engines:
        await engine.start()
        print(f"[engine]   + {name} -> {engine.project_config.project_path}")

    print("[engine] All engines running. Press Ctrl+C to stop.")

    # Wait for shutdown signal
    await shutdown_event.wait()

    # Stop all engines concurrently
    print("[engine] Stopping all engines...")
    await asyncio.gather(*(engine.stop() for _, engine in engines))
    print("[engine] All engines stopped. Goodbye.")


if __name__ == "__main__":
    asyncio.run(main())
