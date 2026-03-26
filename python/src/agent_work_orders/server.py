"""Standalone Server Entry Point

FastAPI server for independent agent work order service.
"""

import os
import shutil
import subprocess
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import log_buffer, router, telemetry_store
from .config import config, validate_startup_config
from .database.client import check_database_health
from .utils.structured_logger import (
    configure_structured_logging_with_buffer,
    get_logger,
)
from src.server.config.required_config import required_config_reference


def _warn_optional_vars(log: Any) -> None:
    """Log warnings for optional environment variables that are not set.

    When the agent work orders feature is enabled, missing auth credentials will
    cause task execution failures. Surface these gaps at startup instead of at
    runtime.
    """
    optional_vars: list[tuple[str, str]] = [
        ("ANTHROPIC_API_KEY", "Required for Claude API authentication (alternative: CLAUDE_CODE_OAUTH_TOKEN)"),
        ("CLAUDE_CODE_OAUTH_TOKEN", "OAuth token for Claude CLI (alternative: ANTHROPIC_API_KEY)"),
        ("GITHUB_PAT_TOKEN", "Required for gh CLI authentication and PR creation"),
    ]
    # Only warn on auth vars if the feature is enabled — missing vars are expected when disabled
    if config.ENABLED:
        has_claude_auth = bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_CODE_OAUTH_TOKEN"))
        if not has_claude_auth:
            log.warning(
                "Missing Claude authentication",
                extra={
                    "detail": "Neither ANTHROPIC_API_KEY nor CLAUDE_CODE_OAUTH_TOKEN is set. "
                    f"Task execution will fail. See {required_config_reference('agent-work-orders')}."
                },
            )
        if not os.getenv("GITHUB_PAT_TOKEN"):
            log.warning(
                "Optional environment variable not set",
                extra={
                    "var": "GITHUB_PAT_TOKEN",
                    "detail": f"{optional_vars[2][1]}. See {required_config_reference('agent-work-orders')}.",
                },
            )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan context manager for startup and shutdown tasks"""
    # Configure structured logging with buffer for SSE streaming and telemetry persistence
    configure_structured_logging_with_buffer(config.LOG_LEVEL, log_buffer, telemetry_store)

    logger = get_logger(__name__)

    logger.info(
        "Starting Agent Work Orders service",
        extra={
            "port": config.get_service_port(),
            "service_discovery_mode": config.SERVICE_DISCOVERY_MODE,
        },
    )

    validate_startup_config()

    # Warn on missing optional vars that affect runtime behavior
    _warn_optional_vars(logger)

    # Start log buffer cleanup task
    await log_buffer.start_cleanup_task()

    # Validate Claude CLI is available
    try:
        result = subprocess.run(
            [config.CLAUDE_CLI_PATH, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            logger.info(
                "Claude CLI validation successful",
                extra={"version": result.stdout.strip()},
            )
        else:
            logger.error(
                "Claude CLI validation failed",
                extra={"error": result.stderr},
            )
    except FileNotFoundError:
        logger.error(
            "Claude CLI not found",
            extra={"path": config.CLAUDE_CLI_PATH},
        )
    except Exception as e:
        logger.error(
            "Claude CLI validation error",
            extra={"error": str(e)},
        )

    # Validate git is available
    if not shutil.which("git"):
        logger.error("Git not found in PATH")
    else:
        logger.info("Git validation successful")

    # Log service URLs
    archon_server_url = config.get_archon_server_url()
    archon_mcp_url = config.get_archon_mcp_url()

    if archon_server_url:
        logger.info(
            "Service discovery configured",
            extra={
                "archon_server_url": archon_server_url,
                "archon_mcp_url": archon_mcp_url,
            },
        )

    yield

    logger.info("Shutting down Agent Work Orders service")

    # Stop log buffer cleanup task
    await log_buffer.stop_cleanup_task()

# Create FastAPI app with lifespan
app = FastAPI(
    title="Agent Work Orders API",
    description="Independent agent work order service for workflow-based agent execution",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS middleware with permissive settings for development
cors_origins = os.getenv("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routes with /api/agent-work-orders prefix
app.include_router(router, prefix="/api/agent-work-orders")


@app.get("/health")
async def health_check() -> dict[str, Any]:
    """Health check endpoint with dependency validation"""
    health_status: dict[str, Any] = {
        "status": "healthy",
        "service": "agent-work-orders",
        "version": "0.1.0",
        "enabled": config.ENABLED,
        "dependencies": {},
    }

    # If feature is not enabled, return early with healthy status
    # (disabled features are healthy - they're just not active)
    if not config.ENABLED:
        health_status["message"] = "Agent work orders feature is disabled. Set ENABLE_AGENT_WORK_ORDERS=true to enable."
        return health_status

    # Check Claude CLI
    try:
        result = subprocess.run(
            [config.CLAUDE_CLI_PATH, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        health_status["dependencies"]["claude_cli"] = {
            "available": result.returncode == 0,
            "version": result.stdout.strip() if result.returncode == 0 else None,
        }
    except Exception as e:
        health_status["dependencies"]["claude_cli"] = {
            "available": False,
            "error": str(e),
        }

    # Check git
    health_status["dependencies"]["git"] = {
        "available": shutil.which("git") is not None,
    }

    # Check GitHub CLI authentication
    try:
        result = subprocess.run(
            [config.GH_CLI_PATH, "auth", "status"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        # gh auth status returns 0 if authenticated
        health_status["dependencies"]["github_cli"] = {
            "available": shutil.which(config.GH_CLI_PATH) is not None,
            "authenticated": result.returncode == 0,
            "token_configured": os.getenv("GH_TOKEN") is not None or os.getenv("GITHUB_TOKEN") is not None,
        }
    except Exception as e:
        health_status["dependencies"]["github_cli"] = {
            "available": False,
            "authenticated": False,
            "error": str(e),
        }

    # Check Archon server connectivity (if configured)
    archon_server_url = config.get_archon_server_url()
    if archon_server_url:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{archon_server_url}/health")
                health_status["dependencies"]["archon_server"] = {
                    "available": response.status_code == 200,
                    "url": archon_server_url,
                }
        except Exception as e:
            health_status["dependencies"]["archon_server"] = {
                "available": False,
                "url": archon_server_url,
                "error": str(e),
            }

    # Check database health if using Supabase storage
    if config.STATE_STORAGE_TYPE.lower() == "supabase":
        db_health = await check_database_health()
        health_status["storage_type"] = "supabase"
        health_status["database"] = db_health
    else:
        health_status["storage_type"] = config.STATE_STORAGE_TYPE

    # Check MCP server connectivity (if configured)
    archon_mcp_url = config.get_archon_mcp_url()
    if archon_mcp_url:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{archon_mcp_url}/health")
                health_status["dependencies"]["archon_mcp"] = {
                    "available": response.status_code == 200,
                    "url": archon_mcp_url,
                }
        except Exception as e:
            health_status["dependencies"]["archon_mcp"] = {
                "available": False,
                "url": archon_mcp_url,
                "error": str(e),
            }

    # Check Supabase database connectivity (if configured)
    supabase_url = os.getenv("SUPABASE_URL")
    if supabase_url:
        try:
            from .state_manager.repository_config_repository import get_supabase_client

            client = get_supabase_client()
            # Check if archon_configured_repositories table exists
            response = client.table("archon_configured_repositories").select("id").limit(1).execute()
            health_status["dependencies"]["supabase"] = {
                "available": True,
                "table_exists": True,
                "url": supabase_url.split("@")[-1] if "@" in supabase_url else supabase_url.split("//")[-1],
            }
        except Exception as e:
            health_status["dependencies"]["supabase"] = {
                "available": False,
                "table_exists": False,
                "error": str(e),
            }

    # Determine overall status
    critical_deps_ok = (
        health_status["dependencies"].get("claude_cli", {}).get("available", False)
        and health_status["dependencies"].get("git", {}).get("available", False)
    )

    if not critical_deps_ok:
        health_status["status"] = "degraded"

    return health_status


@app.get("/")
async def root() -> dict:
    """Root endpoint with service information"""
    return {
        "service": "agent-work-orders",
        "version": "0.1.0",
        "description": "Independent agent work order service",
        "docs": "/docs",
        "health": "/health",
        "api": "/api/agent-work-orders",
    }


if __name__ == "__main__":
    import uvicorn

    port = int(config.get_service_port())
    uvicorn.run(
        "src.agent_work_orders.server:app",
        host="0.0.0.0",
        port=port,
        reload=True,
    )
