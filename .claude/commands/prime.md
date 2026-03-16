---
description: "Bootstrap context at session start. Read docs, explore structure, check git log."
---

# /prime — Session Bootstrap

Run at the START of every coding session.

## Process

### 1. Read Core Docs
- `CLAUDE.md` (already loaded automatically)
- `PRD.md` (project requirements)
- Scan any `.md` files in `docs/` or project root

### 2. Explore Structure
- `tree -L 3 -I 'target|node_modules|.git|build|dist'`
- Identify recent changes: `git log --oneline -10`
- Check for open PRPs: `ls PRPs/ 2>/dev/null`

### 3. Check Active Tasks (if Archon available)
- Query Archon: `find_tasks(status='doing')`
- Note any blocked or overdue tasks

### 4. Build Mental Model
Summarize in chat:
```
## Session Context

**Project**: {name}
**Last activity**: {from git log}
**Active tasks**: {from Archon or PRPs}
**Key files changed recently**: {from git diff --stat HEAD~5}

Ready for instructions.
```

## Rules
- NEVER modify any files during /prime
- Keep summary under 20 lines — concise mental model only
- If PRD.md missing, suggest running `/init-project` first
