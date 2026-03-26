# Deployment Guide

This directory contains deployment templates for three environment tiers.

```
deploy/
├── local/
│   ├── docker-compose.yml   # All services, hot reload
│   └── .env.example
├── staging/
│   ├── docker-compose.yml   # Production-like, no hot reload, resource limits
│   └── .env.example
└── production/
    ├── docker-compose.yml   # Hardened, restart:always, tight resource limits
    └── .env.example
```

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (or Docker Engine + Compose plugin)
- A [Supabase](https://supabase.com/) project (cloud or local)
- AI API keys (Anthropic, OpenAI) — see `.env.example` for which are required
- GitHub Personal Access Token with `repo` and `workflow` scopes

## Services Overview

| Container | Port | Purpose |
|---|---|---|
| `archon-server` | 8181 | FastAPI main API |
| `archon-mcp` | 8051 | MCP server for IDE integration |
| `archon-agents` | 8052 | AI agents (reranking, document processing) |
| `archon-agent-work-orders` | 8053 | Workflow execution engine |
| `archon-ui` | 3737 | React frontend |

## Database Migrations

Before starting services for the first time, apply schema migrations:

```bash
# Run all migrations in order against your Supabase project
# (replace <project-ref> with your Supabase project ID)
for f in migration/0.1.0-leankit/*.sql; do
  echo "Applying $f..."
  psql "$DATABASE_URL" -f "$f"
done
```

Or paste each `migration/0.1.0-leankit/*.sql` file into the Supabase SQL editor in numeric order.

## Local Development

Starts all five services with hot reload enabled.

```bash
# 1. Configure environment
cp deploy/local/.env.example .env
# Edit .env — fill in SUPABASE_URL, SUPABASE_SERVICE_KEY, ANTHROPIC_API_KEY

# 2. Build and start
docker compose -f deploy/local/docker-compose.yml up --build

# 3. Open the UI
open http://localhost:3737
```

**Ports (local defaults):**
- UI: http://localhost:3737
- API: http://localhost:8181
- MCP: http://localhost:8051

**Hot reload:** Source code is volume-mounted so changes to `python/src/` and
`archon-ui-main/src/` take effect without rebuilding.

### Hybrid mode (optional)

Run the backend in Docker and the frontend locally for faster frontend iteration:

```bash
# Terminal 1 — backend services only
docker compose -f deploy/local/docker-compose.yml up archon-server archon-mcp archon-agents archon-agent-work-orders --build

# Terminal 2 — frontend with Vite HMR
cd archon-ui-main && npm run dev
```

## Staging

Production-like environment without hot reload. Intended for integration testing
and pre-release validation.

```bash
# 1. Configure environment
cp deploy/staging/.env.example .env
# Edit .env — set HOST to your staging server hostname/IP

# 2. Build and start
docker compose -f deploy/staging/docker-compose.yml up --build -d

# 3. Check service health
docker compose -f deploy/staging/docker-compose.yml ps
```

**Key differences from local:**
- No source volume mounts — runs built images
- `LOG_LEVEL=INFO`
- `STATE_STORAGE_TYPE=supabase` (durable state)
- Resource limits applied (CPU + memory)
- `restart: unless-stopped`

## Production

Hardened deployment. Run behind a reverse proxy (nginx, Caddy, Traefik) that
terminates TLS and forwards traffic to the container ports.

```bash
# 1. Configure environment
cp deploy/production/.env.example .env
# Edit .env — fill in ALL values; no defaults are safe here

# 2. Build and start
docker compose -f deploy/production/docker-compose.yml up --build -d

# 3. Verify all services are healthy
docker compose -f deploy/production/docker-compose.yml ps
docker compose -f deploy/production/docker-compose.yml logs --tail=50
```

**Key differences from staging:**
- `restart: always` (survives host reboots)
- `LOG_LEVEL=WARNING` (reduced log volume)
- `PROD=true` — UI and API served through a single port via built-in proxy
- Migration volume mounted read-only
- Tighter CPU/memory reservations

### Reverse proxy example (nginx)

```nginx
server {
    listen 443 ssl;
    server_name app.example.com;

    ssl_certificate     /etc/ssl/certs/app.example.com.crt;
    ssl_certificate_key /etc/ssl/private/app.example.com.key;

    location / {
        proxy_pass http://localhost:3737;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## Common Operations

### View logs

```bash
# All services
docker compose -f deploy/local/docker-compose.yml logs -f

# Single service
docker compose -f deploy/local/docker-compose.yml logs -f archon-server
```

### Restart a service after a code change

```bash
# Rebuild and restart one service
docker compose -f deploy/local/docker-compose.yml up --build -d archon-server
```

### Stop all services

```bash
docker compose -f deploy/local/docker-compose.yml down

# Also remove named volumes
docker compose -f deploy/local/docker-compose.yml down -v
```

### Check health endpoints

```bash
curl http://localhost:8181/health   # API
curl http://localhost:3737          # UI
```

## Environment Variable Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `SUPABASE_URL` | Yes | — | Supabase project URL |
| `SUPABASE_SERVICE_KEY` | Yes | — | SERVICE ROLE key (not anon) |
| `ANTHROPIC_API_KEY` | Yes* | — | For agent work orders |
| `OPENAI_API_KEY` | Optional | — | For agents service |
| `GITHUB_PAT_TOKEN` | Yes* | — | For PR creation (repo+workflow scopes) |
| `HOST` | Yes | localhost | Hostname for UI/API |
| `LOG_LEVEL` | No | INFO | DEBUG/INFO/WARNING/ERROR |
| `AGENTS_ENABLED` | No | true | Enable agents service |
| `ENABLE_AGENT_WORK_ORDERS` | No | true | Enable work orders service |
| `PROD` | No | false | Single-port proxy mode |

*Required when `ENABLE_AGENT_WORK_ORDERS=true`

Full variable list: see the `.env.example` for your target environment tier.

## Troubleshooting

**Services not starting / health checks failing:**
```bash
docker compose -f deploy/local/docker-compose.yml logs archon-server
```
Most startup failures are caused by missing or incorrect `SUPABASE_URL` /
`SUPABASE_SERVICE_KEY`. Verify the service role key is used (not the anon key).

**Port conflicts:**
Override any port in `.env` before starting:
```bash
ARCHON_SERVER_PORT=9181 docker compose -f deploy/local/docker-compose.yml up -d
```

**Database migration errors:**
Apply migrations in numeric order. Each file is idempotent but assumes the
previous ones have been applied. Check the Supabase SQL editor for error details.

**`docker compose` vs `docker-compose`:**
This project uses the Compose V2 plugin (`docker compose`). If your system only
has the standalone `docker-compose` binary, commands work the same way with a hyphen.
