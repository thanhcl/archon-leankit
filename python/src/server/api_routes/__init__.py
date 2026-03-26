"""
API package for Archon - modular FastAPI endpoints

This package organizes the API into logical modules:
- settings_api: Settings and credentials management
- mcp_api: MCP server management and tool execution
- mcp_client_api: Multi-client MCP management system
- knowledge_api: Knowledge base, crawling, and RAG operations
- projects_api: Project and task management with streaming
"""

from .agent_chat_api import router as agent_chat_router
from .approval_requests_api import router as approval_requests_router
from .bootstrap_plans_api import router as bootstrap_plans_router
from .channel_health_api import router as channel_health_router
from .execution_runs_api import router as execution_runs_router
from .external_requests_api import router as external_requests_router
from .internal_api import router as internal_router
from .knowledge_api import router as knowledge_router
from .mcp_api import router as mcp_router
from .openclaw_api import router as openclaw_router
from .projects_api import router as projects_router
from .providers_api import router as providers_router
from .service_health_api import router as service_health_router
from .settings_api import router as settings_router
from .telegram_api import router as telegram_router

__all__ = [
    "settings_router",
    "mcp_router",
    "knowledge_router",
    "projects_router",
    "openclaw_router",
    "agent_chat_router",
    "approval_requests_router",
    "bootstrap_plans_router",
    "channel_health_router",
    "execution_runs_router",
    "external_requests_router",
    "internal_router",
    "providers_router",
    "service_health_router",
    "telegram_router",
]
