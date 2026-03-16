---
description: "Commit changes and prepare for deployment"
argument-hint: "[commit message]"
---

# /ship — Commit & Deploy

## Input
$ARGUMENTS

## Process
1. **Pre-flight**: Run full test suite, verify all pass
2. **Stage**: `git add` only relevant files (no build artifacts)
3. **Commit**: Conventional commit format
   - `feat: add key rotation API`
   - `fix: handle null algorithm in key generation`
   - `refactor: simplify key state machine`
4. **Post-commit**: Update PRPs status if applicable
5. **Meta-Reasoning**: Was anything surprising? Should CLAUDE.md or commands be updated?

## Meta-Reasoning Questions
After shipping, consider:
- Did the AI make a recurring mistake? → Add rule to CLAUDE.md
- Was a pattern unclear? → Update relevant skill
- Was the plan missing something? → Improve /generate-prp template
- Present options A-E to Director. Director decides AI layer changes.

## Rules
- NEVER commit failing tests
- NEVER commit sensitive data
- One logical change per commit
- Git message = future agent's memory
