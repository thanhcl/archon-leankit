---
description: "Auto-generate CLAUDE.md from codebase analysis. 4 phases: Discover → Analyze → Generate → Output."
---

# /create-rules — Generate Global Rules

## Phase 1: DISCOVER
- Detect project type from root files (pom.xml, package.json, pyproject.toml...)
- Read config files: build config, app config, .env.example, docker-compose
- Map structure: `tree -L 3`, scan key source files

## Phase 2: ANALYZE
- Extract tech stack with versions
- Read 3-5 existing files to detect naming, architecture, error handling, logging patterns
- Identify key files and entry points

## Phase 3: GENERATE
- Use `.claude/templates/CLAUDE-template.md` as starting point
- Fill sections with discovered information
- Keep under 150 lines, focus on ACTIONABLE rules only
- Prefer patterns from EXISTING code over generic best practices

## Phase 4: OUTPUT
- Write/merge into `CLAUDE.md`
- Report: Project Type, Tech Stack, Modules detected

## Rules
- NEVER guess — read more files if unsure
- If CLAUDE.md already exists, MERGE — don't overwrite
- Every section must help Claude Code write CORRECT code faster
