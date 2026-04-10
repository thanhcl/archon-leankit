"""
Runtime Daemon for LeanKit Agent Runtimes.

Self-registering daemon that:
1. Detects available CLI tools (Claude Code, Codex)
2. Registers runtime with Archon control plane
3. Polls for tasks via claim API
4. Executes tasks in isolated per-task environments
5. Reports progress and heartbeats
6. Handles cancellation detection and graceful shutdown

Adopted from Multica daemon architecture (Workstream M-P1/P2).
"""

from __future__ import annotations

import asyncio
import os
import platform
import shutil
import signal
from datetime import datetime, timezone
from typing import Any

import httpx

from ..config.logfire_config import get_logger

logger = get_logger(__name__)

HEARTBEAT_INTERVAL = 30  # seconds
CANCEL_POLL_INTERVAL = 5  # seconds
SHUTDOWN_GRACE_PERIOD = 30  # seconds
CLAIM_POLL_INTERVAL = 10  # seconds


def _detect_cli_tools() -> dict[str, str | None]:
    """Detect available CLI tools on this machine."""
    tools: dict[str, str | None] = {}

    # Check Claude Code CLI
    claude_path = shutil.which("claude")
    if claude_path:
        tools["claude-code-cli"] = claude_path

    # Check Codex CLI
    codex_path = shutil.which("codex")
    if codex_path:
        tools["codex-cli"] = codex_path

    return tools


def _detect_capabilities(tools: dict[str, str | None]) -> list[str]:
    """Build capability list from available tools and environment."""
    caps: list[str] = []

    if "claude-code-cli" in tools:
        caps.extend([
            "typescript", "python", "review",
            "complex-implementation", "bug-fix",
            "architect-review", "code-review",
        ])
    if "codex-cli" in tools:
        caps.extend([
            "typescript", "python",
            "docs", "refactor", "test", "scaffold",
        ])

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for c in caps:
        if c not in seen:
            seen.add(c)
            unique.append(c)

    return unique


class RuntimeDaemon:
    """Self-registering agent runtime daemon.

    Lifecycle:
        start() → register → [heartbeat loop + claim loop + execution] → stop() → unregister
    """

    def __init__(
        self,
        archon_url: str = "http://localhost:8100",
        max_concurrent_tasks: int = 1,
        agent_id: str | None = None,
    ) -> None:
        self.archon_url = archon_url.rstrip("/")
        self.max_concurrent_tasks = max_concurrent_tasks
        self.agent_id = agent_id

        self.runtime_id: str | None = None
        self._running = False
        self._shutting_down = False
        self._in_flight_tasks: dict[str, asyncio.Task[Any]] = {}
        self._cancel_watchers: dict[str, asyncio.Task[Any]] = {}
        self._client: httpx.AsyncClient | None = None

        # Auto-detect tools
        self.detected_tools = _detect_cli_tools()
        self.supported_runners = list(self.detected_tools.keys())
        self.capabilities = _detect_capabilities(self.detected_tools)

    @property
    def device_name(self) -> str:
        return f"{platform.node()}-{os.getpid()}"

    @property
    def current_task_count(self) -> int:
        return len(self._in_flight_tasks)

    # ── HTTP Client ───────────────────────────────────────────────────────

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def _api(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Make an API call to Archon."""
        client = await self._get_client()
        url = f"{self.archon_url}{path}"
        try:
            resp = await client.request(method, url, json=json)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            logger.warning(f"API error {e.response.status_code}: {path} — {e}")
            return None
        except Exception as e:
            logger.warning(f"API request failed: {path} — {e}")
            return None

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the daemon: register, then run heartbeat + claim loops."""
        if not self.supported_runners:
            logger.error("No CLI tools detected. Cannot start daemon.")
            return

        logger.info(f"Starting runtime daemon: device={self.device_name} runners={self.supported_runners} capabilities={self.capabilities[:5]} max_concurrent={self.max_concurrent_tasks}")

        self._running = True
        self._shutting_down = False

        # Register with Archon
        registered = await self._register()
        if not registered:
            logger.error("Failed to register runtime. Aborting.")
            self._running = False
            return

        # Install signal handlers
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(self._signal_handler(s)))

        # Start background loops
        heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        claim_task = asyncio.create_task(self._claim_loop())

        try:
            await asyncio.gather(heartbeat_task, claim_task)
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Graceful shutdown: wait for in-flight tasks, then unregister."""
        if self._shutting_down:
            return

        self._shutting_down = True
        self._running = False

        logger.info(f"Shutting down daemon: in_flight={len(self._in_flight_tasks)} grace_period={SHUTDOWN_GRACE_PERIOD}")

        # Wait for in-flight tasks with grace period
        if self._in_flight_tasks:
            logger.info(f"Waiting up to {SHUTDOWN_GRACE_PERIOD}s for {len(self._in_flight_tasks)} in-flight tasks...")
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._in_flight_tasks.values(), return_exceptions=True),
                    timeout=SHUTDOWN_GRACE_PERIOD,
                )
            except asyncio.TimeoutError:
                logger.warning("Grace period expired. Cancelling remaining tasks.")
                for task in self._in_flight_tasks.values():
                    task.cancel()

        # Cancel all cancel watchers
        for watcher in self._cancel_watchers.values():
            watcher.cancel()

        # Unregister runtime
        if self.runtime_id:
            await self._unregister()

        # Close HTTP client
        if self._client and not self._client.is_closed:
            await self._client.aclose()

        logger.info("Daemon stopped cleanly")

    async def _signal_handler(self, sig: signal.Signals) -> None:
        """Handle SIGINT/SIGTERM for graceful shutdown."""
        logger.info(f"Received signal {sig.name}. Initiating graceful shutdown...")
        await self.stop()

    # ── Registration ──────────────────────────────────────────────────────

    async def _register(self) -> bool:
        """Register this runtime with Archon."""
        result = await self._api("POST", "/api/runtimes/register", json={
            "device_name": self.device_name,
            "capabilities": self.capabilities,
            "supported_runners": self.supported_runners,
            "max_concurrent_tasks": self.max_concurrent_tasks,
            "agent_id": self.agent_id,
            "version": "1.0.0",
            "metadata": {
                "platform": platform.system(),
                "python_version": platform.python_version(),
                "detected_tools": {k: str(v) for k, v in self.detected_tools.items()},
            },
        })

        if result and "id" in result:
            self.runtime_id = result["id"]
            logger.info(f"Runtime registered: runtime_id={self.runtime_id}")
            return True

        return False

    async def _unregister(self) -> None:
        """Unregister this runtime from Archon."""
        if not self.runtime_id:
            return

        await self._api("POST", f"/api/runtimes/{self.runtime_id}/unregister")
        logger.info(f"Runtime unregistered: runtime_id={self.runtime_id}")

    # ── Heartbeat ─────────────────────────────────────────────────────────

    async def _heartbeat_loop(self) -> None:
        """Send heartbeats every HEARTBEAT_INTERVAL seconds."""
        while self._running:
            try:
                await self._api("POST", f"/api/runtimes/{self.runtime_id}/heartbeat", json={
                    "current_task_count": self.current_task_count,
                })
            except Exception as e:
                logger.warning(f"Heartbeat failed: {e}")

            await asyncio.sleep(HEARTBEAT_INTERVAL)

    # ── Task Claiming ─────────────────────────────────────────────────────

    async def _claim_loop(self) -> None:
        """Poll for available tasks and claim them."""
        while self._running:
            if self.current_task_count < self.max_concurrent_tasks:
                try:
                    result = await self._api("POST", f"/api/runtimes/{self.runtime_id}/claim")
                    if result and result.get("task"):
                        task = result["task"]
                        task_id = task["id"]
                        logger.info(f"Task claimed: task_id={task_id}")

                        # Start execution in background
                        exec_task = asyncio.create_task(self._execute_task(task))
                        self._in_flight_tasks[task_id] = exec_task

                        # Start cancellation watcher
                        cancel_task = asyncio.create_task(self._cancellation_watcher(task_id))
                        self._cancel_watchers[task_id] = cancel_task

                except Exception as e:
                    logger.warning(f"Claim poll failed: {e}")

            await asyncio.sleep(CLAIM_POLL_INTERVAL)

    # ── Task Execution ────────────────────────────────────────────────────

    async def _execute_task(self, task: dict[str, Any]) -> None:
        """Execute a claimed task in an isolated environment."""
        task_id = task["id"]
        try:
            logger.info(f"Starting task execution: task_id={task_id}")

            # Report initial progress
            await self._report_progress(task_id, step_count=0, current_action="initializing")

            # TODO: Integrate with actual runner adapters (CCSpawner, CodexRunner)
            # For now, this is the framework — actual execution hooks into
            # the existing TaskEngine middleware chain via _stage_runner_execute

            await self._report_progress(task_id, step_count=1, current_action="executing")

            # Placeholder for actual execution — will be wired to TaskEngine
            logger.info(f"Task execution framework ready: task_id={task_id}")

        except asyncio.CancelledError:
            logger.info(f"Task execution cancelled: task_id={task_id}")
        except Exception as e:
            logger.error(f"Task execution failed: task_id={task_id} error={e}", exc_info=True)
        finally:
            # Release task from runtime
            await self._release_task(task_id)
            self._in_flight_tasks.pop(task_id, None)
            self._cancel_watchers.pop(task_id, None)

    async def _release_task(self, task_id: str) -> None:
        """Release a task after completion or failure."""
        if self.runtime_id:
            await self._api("POST", f"/api/runtimes/{self.runtime_id}/release/{task_id}")

    async def _report_progress(
        self,
        task_id: str,
        step_count: int | None = None,
        percentage: float | None = None,
        current_action: str | None = None,
    ) -> None:
        """Report task execution progress to Archon."""
        await self._api("POST", f"/api/runtimes/tasks/{task_id}/progress", json={
            "runtime_id": self.runtime_id,
            "step_count": step_count,
            "percentage": percentage,
            "current_action": current_action,
        })

    # ── Cancellation Detection (M-P2-04) ──────────────────────────────────

    async def _cancellation_watcher(self, task_id: str) -> None:
        """Poll task status every CANCEL_POLL_INTERVAL seconds.

        If the task has been cancelled on the server, cancel the local execution.
        """
        while self._running and task_id in self._in_flight_tasks:
            try:
                result = await self._api("GET", f"/api/runtimes/tasks/{task_id}/status")
                if not result:
                    await asyncio.sleep(CANCEL_POLL_INTERVAL)
                    continue

                status = result.get("status")
                if status == "cancelled":
                    logger.info(f"Cancellation detected: task_id={task_id}")

                    # Cancel the execution task
                    exec_task = self._in_flight_tasks.get(task_id)
                    if exec_task and not exec_task.done():
                        exec_task.cancel()

                    break

            except Exception:
                pass  # Silently retry on poll failures

            await asyncio.sleep(CANCEL_POLL_INTERVAL)


class ConfigWatcher:
    """Watch a configuration file for changes and reload.

    Hot-reload pattern adopted from Multica (M-P1-04).
    """

    def __init__(
        self,
        config_path: str,
        on_change: Any = None,
        poll_interval: float = 5.0,
    ) -> None:
        self.config_path = config_path
        self.on_change = on_change
        self.poll_interval = poll_interval
        self._last_mtime: float | None = None
        self._running = False

    async def start(self) -> None:
        """Start watching the config file for changes."""
        self._running = True
        while self._running:
            try:
                mtime = os.path.getmtime(self.config_path)
                if self._last_mtime is not None and mtime != self._last_mtime:
                    logger.info(f"Config file changed, reloading: path={self.config_path}")
                    if self.on_change:
                        if asyncio.iscoroutinefunction(self.on_change):
                            await self.on_change()
                        else:
                            self.on_change()
                self._last_mtime = mtime
            except FileNotFoundError:
                pass
            except Exception as e:
                logger.warning(f"Config watch error: {e}")

            await asyncio.sleep(self.poll_interval)

    def stop(self) -> None:
        """Stop watching."""
        self._running = False
