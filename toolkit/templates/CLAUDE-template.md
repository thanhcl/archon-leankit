# CLAUDE.md Template
Adapt when running /create-rules. Remove sections that don't apply.

## Project Overview
{One paragraph: what, who, why}

## Tech Stack
| Technology | Version | Purpose |

## Commands
```bash
# Build / Test / Lint / Run
```

## Project Structure
```
{tree output}
```

## Architecture
{Layers and data flow description}

## Code Patterns
### Naming / Error Handling / Logging / Comments

## Testing
- Framework, Location, Naming convention, Run command

## Validation (before commit)
```bash
{compile} && {test} && {lint}
```

## Key Files
| File | Purpose |

## On-Demand Context
| Topic | File path |

## Security Rules (if applicable)

## Lessons Learned (Sprint S3-S4)

1. **DUPLICATE CLASS DEFINITIONS**: Before fixing any model/contract, ALWAYS grep the entire codebase: `grep -rn "class ClassName" --include="*.py"`. Duplicate classes in other files shadow the canonical import.
2. **DUPLICATE FIELD DEFINITIONS**: When editing a Pydantic model, verify there are no duplicate field lines. Claude Code sometimes inserts new code without removing the old version.
3. **DB MIGRATION SYNC**: Adding fields in code MUST have a corresponding migration. Check both migration files AND service code for existing usage of those fields.
4. **DOCKER REBUILD**: `docker compose restart` reuses the old image. Always use `docker compose up --build -d` after code changes.
5. **TASK SIZE**: Tasks touching >5 files or crossing layers (migration + API + service + frontend) are medium/complex, NOT simple.
6. **SINGLE SOURCE OF TRUTH**: Models are defined in `api_contracts.py`. NEVER duplicate local classes in route files — always import from the contracts module.
7. **DEDUP CHECK**: Before creating a task, search existing tasks with the same title to avoid duplicates.
8. **LIBRARY INSTALL TASKS**: Tasks yêu cầu install new npm/pip packages nên làm manual qua CC interactive trước, rồi tạo Engine task cho phần implement. Hoặc pre-install packages trước khi Engine chạy. Engine subprocess có thể bị hang khi install packages do network/permission issues.
9. **BUDGET LIMITS**: Engine CostBudgetService có daily + sprint budget limits. Default: $100/day, $500/sprint. File: cost_budget_service.py. Khi Engine log "Budget exceeded — pausing project", tăng limits hoặc chờ ngày mới reset.
