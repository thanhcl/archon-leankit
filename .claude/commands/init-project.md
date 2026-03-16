---
description: "One-time project initialization. Creates PRD.md, configures CLAUDE.md, seeds Archon KB."
---

# /init-project — Initialize Project

## Process

### Phase 1: Create PRD.md
Ask Director for:
1. Project name, description, and purpose
2. Target users and key features
3. Tech stack decisions
4. Non-functional requirements (security, performance)
5. Milestones / phases

Write `PRD.md` in project root with structured sections.

### Phase 2: Generate Global Rules
Run the equivalent of `/create-rules`:
1. Analyze codebase (if brownfield) or tech stack (if greenfield)
2. Detect conventions, patterns, structure
3. Update `CLAUDE.md` Project Context section with specifics
4. Update Build & Test Commands section

### Phase 3: Seed Knowledge (if Archon available)
1. Upload PRD.md to Archon KB
2. Upload architecture docs, design docs
3. Crawl relevant external docs (framework docs, specs)

### Phase 4: Configure Hooks
1. Replace `$PROJECT_NAME` in `.claude/settings.json` with actual project name
2. Verify hooks are working: run a simple command and check Observability dashboard

### Phase 5: Report
```
## Project Initialized

**Project**: {name}
**PRD**: PRD.md created ({sections} sections)
**CLAUDE.md**: Updated with project context
**Archon KB**: {count} docs uploaded
**Hooks**: Configured for {project_name}

### Ready to start
Run `/prime` at the beginning of each session.
Run `/generate-prp [feature]` to plan your first feature.
```

## Rules
- Run ONCE per project, not per session
- Never overwrite existing PRD.md without Director approval
- Always ask before making irreversible changes
