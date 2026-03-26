# Required Config Checklist

This repository fails fast on missing startup-blocking configuration and logs warnings for optional settings that only affect partial functionality. Error messages point back to this file so operators can verify the exact checklist for each service.

## archon-backend

Startup command examples: `uv run python -m src.server.main` or `uv run python -m uvicorn src.server.main:app --port 8181 --reload`

| Variable | Required | Notes |
| --- | --- | --- |
| `LEANKIT_CONTROL_PLANE_PORT` or `ARCHON_SERVER_PORT` | Conditional | Required when starting the backend via `uv run python -m src.server.main`. When `uvicorn ... --port` provides the bind port, startup does not require either variable. |
| `SUPABASE_URL` | Yes | Required for credentials, projects, documents, and all database-backed APIs. |
| `SUPABASE_SERVICE_KEY` | Yes | Must be a Supabase `service_role` key, not an anon key. |
| `LEANKIT_CONTROL_PLANE_MCP_PORT` or `ARCHON_MCP_PORT` or `PORT` | Yes | Required during startup validation so backend-managed MCP coordination resolves the MCP listener port. |
| `OPENAI_API_KEY` | No | Startup logs a warning when missing because embedding/provider features may not work. |
| `LOGFIRE_TOKEN` | No | Startup logs a warning when missing because tracing/observability exports stay disabled. |

## archon-agents

Startup command examples: `uv run python -m src.agents.server` or `uv run python -m uvicorn src.agents.server:app --port 8052 --reload`

| Variable | Required | Notes |
| --- | --- | --- |
| `LEANKIT_CONTROL_PLANE_PORT` or `ARCHON_SERVER_PORT` | Yes | Required so the agents service can fetch credentials from `archon-server` during startup. |
| `LEANKIT_AGENT_SERVICE_PORT` or `ARCHON_AGENTS_PORT` | Conditional | Required when starting the agents service via `uv run python -m src.agents.server`. When `uvicorn ... --port` provides the bind port, startup does not require either variable. |
| `OPENAI_API_KEY` | No | Credentials are normally loaded from the backend at startup; a local override is optional. |
| `LOGFIRE_TOKEN` | No | Logging/export integrations stay disabled when unset. |

## archon-mcp-server

Startup command example: `uv run python -m src.mcp_server.mcp_server`

| Variable | Required | Notes |
| --- | --- | --- |
| `LEANKIT_CONTROL_PLANE_MCP_PORT` or `ARCHON_MCP_PORT` | Yes | Required so the MCP HTTP server knows which port to bind. |

## agent-work-orders

Startup command example: `uv run python -m uvicorn src.agent_work_orders.server:app --port 8053 --reload`

| Variable | Required | Notes |
| --- | --- | --- |
| `ENABLE_AGENT_WORK_ORDERS` or `LEANKIT_ENABLE_AGENT_WORK_ORDERS` | No | Feature flag. When false, startup skips storage-specific validation. |
| `STATE_STORAGE_TYPE` | Conditional | Allowed values: `memory`, `file`, `supabase`. |
| `SUPABASE_URL` | Conditional | Required when `STATE_STORAGE_TYPE=supabase` and agent work orders are enabled. |
| `SUPABASE_SERVICE_KEY` | Conditional | Required when `STATE_STORAGE_TYPE=supabase` and agent work orders are enabled. |
| `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN` | No | Startup logs a warning when neither is present because Claude execution will fail later. |
| `GITHUB_PAT_TOKEN` | No | Startup logs a warning when missing because PR creation/authenticated GitHub flows may fail. |

## leankit-engine

Startup command example: `uv run python start_engine.py`

This service currently has no startup-blocking environment variables because it defaults to local control-plane URLs and sensible concurrency values. Optional overrides such as `LEANKIT_CONTROL_PLANE_URL`, `LEANKIT_ENGINE_MAX_PARALLEL`, `LEANKIT_ENGINE_MAX_PARALLEL_GLOBAL`, and `LEANKIT_ENGINE_TASK_TIMEOUT_SECONDS` are resolved at startup and fall back to documented defaults when unset.

## archon-frontend

Startup command example: `cd archon-ui-main && npm run dev`

This service currently has no startup-blocking environment variables. Development startup falls back to local defaults for `VITE_ARCHON_SERVER_PORT`, `ARCHON_SERVER_PORT`, `HOST`, `VITE_ALLOWED_HOSTS`, and `VITE_SHOW_DEVTOOLS` when they are unset.
