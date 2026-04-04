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
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen

# Add python dir to path so relative imports in src work
sys.path.insert(0, os.path.dirname(__file__))

from src.server.config.env_aliases import get_control_plane_url, get_env_value
from src.server.services.engine.architect_reviewer import ReviewConfig
from src.server.services.engine.capacity_tracker import GlobalCapacityTracker, SharedAgentPool
from src.server.services.engine.task_engine import TaskEngine


def resolve_engine_runtime_config(env: dict[str, str] | os._Environ[str] | None = None) -> dict[str, int | str]:
    """Resolve engine runtime configuration from platform and legacy env names."""
    source = env or os.environ
    return {
        "archon_api": get_control_plane_url(source),
        "default_max_parallel": int(
            get_env_value("LEANKIT_ENGINE_MAX_PARALLEL", "MAX_PARALLEL", default="3", env=source) or "3"
        ),
        "max_parallel_global": int(
            get_env_value("LEANKIT_ENGINE_MAX_PARALLEL_GLOBAL", "MAX_PARALLEL_GLOBAL", default="10", env=source)
            or "10"
        ),
        "task_timeout": int(
            get_env_value("LEANKIT_ENGINE_TASK_TIMEOUT_SECONDS", "TASK_ENGINE_TIMEOUT", default="1800", env=source)
            or "1800"
        ),
    }


# ---------------------------------------------------------------------------
# Fetch projects from Archon API
# ---------------------------------------------------------------------------


def register_engine_heartbeat(archon_api: str, started_at: str) -> None:
    """Register engine startup with Archon API for uptime tracking.

    Failure is non-fatal — the engine continues even if the heartbeat can't be stored.
    """
    try:
        url = f"{archon_api}/api/engine/heartbeat"
        body = json.dumps({"started_at": started_at}).encode()
        req = Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        urlopen(req, timeout=5)
        print(f"[engine] Heartbeat registered (started_at={started_at})")
    except Exception as e:
        print(f"  [warn] Could not register engine heartbeat: {e}")


def fetch_projects(max_retries: int = 3, delay: int = 5) -> list[dict]:
    """Fetch project configs from Archon API with retry."""
    runtime = resolve_engine_runtime_config()
    archon_api = str(runtime["archon_api"])
    default_max_parallel = int(runtime["default_max_parallel"])
    for attempt in range(max_retries):
        try:
            url = f"{archon_api}/api/projects/office-configs"
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

                # Per-project max_concurrent from office_settings, fallback to env default
                max_concurrent = settings.get("max_concurrent", default_max_parallel)
                try:
                    max_concurrent = int(max_concurrent)
                except (TypeError, ValueError):
                    max_concurrent = default_max_parallel

                result.append({
                    "project_id": p["id"],
                    "name": p.get("title", source_app or "unknown"),
                    "source_app": source_app,
                    "path": project_path,
                    "build": build_command,
                    "max_concurrent": max_concurrent,
                })

            return result
        except Exception as e:
            print(f"  [warn] Archon unreachable (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(delay)

    print("  [error] Archon API unreachable after retries. Exiting.")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Shared agent pool setup
# ---------------------------------------------------------------------------


def fetch_agent_pools(archon_api: str) -> dict:
    """Fetch shared agent pool configuration from Archon API.

    Returns empty pool config if the endpoint is unreachable or returns no pools.
    """
    try:
        url = f"{archon_api}/api/engine/agent-pools"
        resp = urlopen(Request(url), timeout=5)
        data = json.loads(resp.read())
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"  [warn] Could not fetch agent pools config: {e}")
        return {}


def _coerce_review_config_payload(payload: object) -> ReviewConfig:
    """Normalize API payload into a ReviewConfig dataclass."""
    if not isinstance(payload, dict):
        return ReviewConfig()

    kwargs: dict[str, object] = {}

    for field_name in ("review_mode", "provider", "model"):
        value = payload.get(field_name)
        if isinstance(value, str):
            kwargs[field_name] = value

    for field_name in ("temperature", "confidence_approve_threshold", "confidence_retry_threshold"):
        value = payload.get(field_name)
        if isinstance(value, (int, float)):
            kwargs[field_name] = float(value)

    for field_name in ("max_tokens", "timeout"):
        value = payload.get(field_name)
        if isinstance(value, int) and value > 0:
            kwargs[field_name] = value

    for field_name in (
        "security_override_to_api",
        "api_fallback_to_self_review",
        "independent_review_enabled",
    ):
        value = payload.get(field_name)
        if isinstance(value, bool):
            kwargs[field_name] = value

    return ReviewConfig(**kwargs)


def fetch_review_config(archon_api: str) -> ReviewConfig:
    """Fetch global architect-review configuration from the control plane."""
    try:
        url = f"{archon_api}/api/engine/review-config"
        resp = urlopen(Request(url), timeout=5)
        data = json.loads(resp.read())
        config = _coerce_review_config_payload(data)
        print(
            "[engine] Review config: "
            f"mode={config.review_mode} provider={config.provider} model={config.model or '<provider-default>'}"
        )
        return config
    except Exception as e:
        print(f"  [warn] Could not fetch review config, using defaults: {e}")
        return ReviewConfig()


def fetch_project_capacity_policies(archon_api: str, project_ids: list[str]) -> dict[str, dict]:
    """Fetch capacity_policy for each project from its engine policy.

    Returns dict mapping project_id -> capacity_policy dict.
    """
    result: dict[str, dict] = {}
    for pid in project_ids:
        try:
            url = f"{archon_api}/api/engine-policies/{pid}"
            resp = urlopen(Request(url), timeout=5)
            data = json.loads(resp.read())
            policy = data.get("policy") or {}
            cap = policy.get("capacity_policy") or {}
            if cap:
                result[pid] = cap
        except Exception as e:
            print(f"  [warn] Could not fetch engine policy for {pid[:8]}: {e}")
    return result


def build_agent_pools(
    pool_configs: list[dict],
    project_capacity_policies: dict[str, dict],
) -> dict[str, SharedAgentPool]:
    """Instantiate SharedAgentPool objects from config and project policies.

    Returns dict mapping pool_id -> SharedAgentPool.
    """
    pools: dict[str, SharedAgentPool] = {}

    # Build per-project-limits for each pool from project capacity policies
    pool_project_limits: dict[str, dict[str, int]] = {}
    for project_id, cap in project_capacity_policies.items():
        pool_id = cap.get("pool_id")
        pool_slots = cap.get("pool_slots")
        if pool_id and isinstance(pool_slots, int) and pool_slots >= 1:
            pool_project_limits.setdefault(pool_id, {})[project_id] = pool_slots

    for cfg in pool_configs:
        pool_id = cfg.get("pool_id", "").strip()
        total_slots = cfg.get("total_slots", 0)
        if not pool_id or total_slots < 1:
            print(f"  [warn] Skipping invalid pool config: {cfg}")
            continue

        try:
            pool = SharedAgentPool(
                pool_id=pool_id,
                total_slots=total_slots,
                per_project_limits=pool_project_limits.get(pool_id),
                default_project_slots=cfg.get("default_project_slots", 1),
                priority_reserve=cfg.get("priority_reserve", 1),
            )
            pools[pool_id] = pool
            print(
                f"  [engine] Pool '{pool_id}': total_slots={total_slots} "
                f"priority_reserve={cfg.get('priority_reserve', 1)} "
                f"projects={list(pool_project_limits.get(pool_id, {}).keys())}"
            )
        except ValueError as e:
            print(f"  [warn] Skipping pool '{pool_id}': {e}")

    return pools


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    runtime = resolve_engine_runtime_config()
    archon_api = str(runtime["archon_api"])
    default_max_parallel = int(runtime["default_max_parallel"])
    max_parallel_global = int(runtime["max_parallel_global"])
    task_timeout = int(runtime["task_timeout"])

    print(f"[engine] Fetching projects from {archon_api}...")
    projects = fetch_projects()

    if not projects:
        print("[engine] No projects found. Exiting.")
        sys.exit(0)

    print(f"[engine] Found {len(projects)} projects:")
    for p in projects:
        print(f"  - {p['name']} ({p['project_id'][:8]}...) -> {p['path']}")
        print(f"    build: {p['build']}")

    print(f"[engine] Task timeout: {task_timeout}s ({task_timeout // 60}m)")
    print(f"[engine] Global parallel limit: {max_parallel_global} | Default per-project: {default_max_parallel}")

    global_tracker = GlobalCapacityTracker(max_global=max_parallel_global)
    review_config = fetch_review_config(archon_api)

    # Load shared agent pools from configuration
    pool_configs_data = fetch_agent_pools(archon_api)
    pool_configs = pool_configs_data.get("pools", [])
    project_ids = [p["project_id"] for p in projects]
    project_capacity_policies = fetch_project_capacity_policies(archon_api, project_ids)
    agent_pools = build_agent_pools(pool_configs, project_capacity_policies)
    if agent_pools:
        print(f"[engine] Loaded {len(agent_pools)} shared agent pool(s): {list(agent_pools.keys())}")
    else:
        print("[engine] No shared agent pools configured (per-project and global limits only)")

    engines: list[tuple[str, TaskEngine]] = []
    for proj in projects:
        project_path = str(Path(proj["path"]).expanduser())
        per_project_limit = proj.get("max_concurrent", default_max_parallel)

        # Resolve the pool this project belongs to (if any)
        cap_policy = project_capacity_policies.get(proj["project_id"], {})
        pool_id = cap_policy.get("pool_id")
        agent_pool = agent_pools.get(pool_id) if pool_id else None

        engine = TaskEngine(
            project_path=project_path,
            project_id=proj["project_id"],
            source_app=proj.get("source_app"),
            build_command=proj["build"],
            poll_interval=30,
            max_parallel=per_project_limit,
            default_timeout=task_timeout,
            shutdown_grace=60,
            review_config=review_config,
            global_tracker=global_tracker,
            agent_pool=agent_pool,
        )
        engines.append((proj["name"], engine))
        pool_label = f" pool={pool_id}" if pool_id else ""
        print(f"  - {proj['name']}: max_concurrent={per_project_limit}{pool_label}")

    # Graceful shutdown via Ctrl+C / SIGTERM
    shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        if not shutdown_event.is_set():
            print("\n[engine] Shutdown signal received - stopping all engines...")
            shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    # Register engine startup heartbeat for health endpoint uptime tracking
    engine_started_at = datetime.now(UTC).isoformat()
    register_engine_heartbeat(archon_api, engine_started_at)

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
