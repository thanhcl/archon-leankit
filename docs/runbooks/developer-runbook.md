# Developer Runbook

Daily workflow for local development, validation, and debugging.

Use this sequence at the start of a development session or before handoff so local setup, validation, and debugging evidence stay reproducible.

## Session objective

- Sync toolchains and dependencies before reproducing an issue.
- Start the stack the same way other maintainers do.
- Leave behind validation commands, failing evidence, and logs that another developer can replay.

## Related references

- `docs/api-contracts/observability.md`
- `docs/api-contracts/external-requests.md`

## Scope

- Run commands from the repository root unless a step says otherwise.
- Use this runbook when starting a fresh dev session, validating changes, or debugging regressions.
- Prefer the repository commands already used by maintainers so local behavior matches CI and normal operations.

## Recommended terminal layout

1. Terminal 1: Docker-backed backend services.
2. Terminal 2: local frontend dev server.
3. Terminal 3: validation commands, curls, and log tailing.

## 1. Check the local toolchain

```bash
node -v
uv --version
docker compose version
```

Expected outcome:

- Node, `uv`, and Docker Compose are installed and respond without errors.
- You can catch local machine setup issues before spending time on application debugging.

Troubleshooting:

- If `uv` is missing, install it before syncing Python dependencies.
- If `docker compose version` fails, fix Docker before continuing because later steps depend on containerized services.

## 2. Sync dependencies

```bash
cd python && uv sync --group all --group dev
cd ../archon-ui-main && npm install
cd ..
```

Expected outcome:

- Python dependencies resolve through `uv`.
- Frontend dependencies install without lockfile drift or missing package errors.

Troubleshooting:

- If `uv` is not installed, install it first and rerun the command.
- If `npm install` fails, confirm you are using Node 18 or newer.

## 3. Start backend services for development

```bash
docker compose up archon-server archon-mcp -d --build
docker compose ps
```

Expected outcome:

- `archon-server` and `archon-mcp` start in Docker.
- The platform API becomes reachable on `http://localhost:8181` unless overridden in `.env`.

Troubleshooting:

- If the build fails, inspect the failed layer output and rerun after fixing the underlying dependency or syntax error.
- If the containers start but the API is unavailable, check `docker compose logs --tail=200 archon-server`.

## 4. Start the frontend locally

```bash
cd archon-ui-main
npm run dev
```

Expected outcome:

- Vite starts the UI development server, normally on port `3737`.
- API calls proxy to the local backend using the configured Vite environment.

Troubleshooting:

- If port `3737` is already in use, stop the conflicting process or use the configured alternative port.
- If the UI cannot reach the backend, confirm `archon-server` is running and the local Vite variables point to the correct host and port.

## 5. Verify the platform before changing code

In a second terminal from the repository root:

```bash
curl -sS http://localhost:8181/health | python -m json.tool
curl -sS http://localhost:8181/api/services/heartbeat | python -m json.tool
```

Expected outcome:

- `/health` returns a basic server health payload.
- `/api/services/heartbeat` returns a structured dependency summary.

Troubleshooting:

- If `/health` fails, the API is not ready yet; wait for Docker health checks to pass.
- If the heartbeat is degraded before you make changes, document that baseline so you do not misattribute the failure later.

## 6. Run backend validation

```bash
cd python
uv run pytest tests/ -x --timeout=30
uv run ruff check
uv run mypy src/
```

Expected outcome:

- Tests stop on the first failure because of `-x`.
- Ruff returns no lint errors.
- Mypy reports no type errors in `src/`.

Troubleshooting:

- If pytest fails, rerun the single failing test file with `uv run pytest path/to/test.py -v`.
- If Ruff or MyPy fails on unrelated pre-existing issues, isolate whether your change touched the failing module before deciding on follow-up work.

## 7. Run frontend validation

```bash
cd archon-ui-main
npx tsc --noEmit
npm run lint
npm run biome
```

Expected outcome:

- TypeScript completes without type errors.
- ESLint finishes successfully for legacy code.
- Biome completes successfully for feature-slice code.

Troubleshooting:

- If `npm run lint` reports issues outside your change set, verify whether the failing file is legacy code or an existing repository issue.
- If Biome reports formatting problems in feature files, run `npm run biome:fix` and review the result before committing.

## 8. Inspect logs while debugging

```bash
docker compose logs -f archon-server
```

Expected outcome:

- You see live API logs while reproducing the issue.

Troubleshooting:

- If the relevant failure is in MCP behavior, switch to `docker compose logs -f archon-mcp`.
- If log output is too broad, rerun without `-f` and use `--tail=200` for a smaller slice.

## 9. Stop the session cleanly

```bash
docker compose stop archon-server archon-mcp
docker compose ps
```

Expected outcome:

- `archon-server` and `archon-mcp` stop cleanly.
- `docker compose ps` no longer shows those services as running.

Troubleshooting:

- If containers remain, run `docker compose ps` to identify which services are still active.
- If you need to remove volumes as part of a reset, use the repo clean workflow intentionally rather than doing it by default.

## Developer handoff checklist

- Record the exact commands used for validation.
- Record whether failures reproduce from a clean `docker compose up archon-server archon-mcp -d --build` session.
- Record any failing tests, logs, or endpoints needed for the next person to continue.

## Escalate when

- Move to the admin runbook when the issue depends on secrets, migrations, or container configuration instead of code changes.
- Return work to the operator runbook when the fix is deployed locally and only daily verification remains.
- Open a follow-up bug when validation passes locally but the underlying regression is not fully explained.
