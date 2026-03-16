# LeanKit V3 Toolkit

Single source of truth for onboarding any new project into the LeanKit V3 development workflow. This toolkit contains all Claude Code commands, agents, skills, hooks, and templates needed for a consistent AI-assisted development experience.

## What's Included

### Commands (`commands/`)
| Command | Description |
|---------|-------------|
| `code-review` | LeanKit code review for quality, security, and consistency |
| `cook` | Implement code following an existing plan |
| `create-rules` | Auto-generate CLAUDE.md from codebase analysis |
| `debug` | Diagnose and fix bugs |
| `execute-prp` | Execute a PRP plan task by task with self-correction |
| `generate-prp` | Generate a Product Requirements Prompt (PRP) |
| `init-project` | One-time project initialization |
| `plan` | Explore codebase and create implementation plan |
| `prime` | Bootstrap context at session start |
| `ship` | Commit changes and prepare for deployment |
| `test` | Generate and run tests for recent changes |
| `opsx/*` | OpenSpec workflow commands (explore, propose, apply, archive) |

### Agents (`agents/`)
| Agent | Description |
|-------|-------------|
| `planner` | Research + planning agent |
| `reviewer` | Code quality + security review |
| `tester` | Test design + execution |
| `db-admin` | Database specialist |
| `team/*` | Team agents (coder, reviewer, tester) |
| `reference/*` | Personality definitions (security engineer, backend architect, testing specialists) |

### Skills (`skills/`)
| Skill | Description |
|-------|-------------|
| `agent-browser` | Browser automation for UI testing |
| `e2e-test` | End-to-end testing with parallel sub-agents |
| `openspec-*` | OpenSpec workflow skills (explore, propose, apply, archive) |

### Templates (`templates/`)
| Template | Description |
|----------|-------------|
| `CLAUDE-template.md` | Starter CLAUDE.md for new projects |
| `api-bundle.md` | API documentation bundle template |
| `architect-playbook.md` | Architecture decision template |
| `bug-fix-prompt.md` | Bug fix prompt template |
| `feature-prompt.md` | Feature implementation prompt |
| `refactor-prompt.md` | Refactoring prompt template |

### Hooks (`hooks/`)
Full hook pipeline with 12 event types: PreToolUse, PostToolUse, PostToolUseFailure, SubagentStart, SubagentStop, SessionStart, SessionEnd, Stop, Notification, PermissionRequest, UserPromptSubmit, PreCompact.

Includes utilities for LLM integration, TTS, summarization, and file validation.

### PRP Templates (`prp-templates/`)
Base PRP (Product Requirements Prompt) template for structured feature planning.

## Installation

### 1. Run the install script

```bash
./toolkit/install.sh /path/to/your-project your-app-name
```

This will:
- Copy all toolkit files into `.claude/` and `PRPs/templates/`
- Generate `.claude/settings.json` with your app name in hook events

### 2. Add Archon MCP server

```bash
cd /path/to/your-project
claude mcp add archon --transport http http://localhost:8051/mcp
```

### 3. Generate project CLAUDE.md

```bash
cd /path/to/your-project
claude /create-rules
```

### 4. Register in Archon

Create a project in Archon and add relevant documentation to the knowledge base.

## Updating the Toolkit

If you improve commands/agents/hooks in a project and want to propagate changes back:

```bash
# Copy improved file back to toolkit
cp /path/to/project/.claude/commands/improved-command.md toolkit/commands/

# Then re-install to other projects as needed
./toolkit/install.sh /path/to/other-project other-app-name
```

The toolkit in `archon-leankit/toolkit/` is the canonical source. Always update here first, then distribute.
