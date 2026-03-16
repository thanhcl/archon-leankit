---
description: "Generate a Product Requirements Prompt (PRP) for a feature — with Q&A, research, and confidence scoring"
argument-hint: "[feature description or brain dump]"
---

# /generate-prp — Create Implementation Plan

## Mission
Transform a feature request into a comprehensive PRP that enables one-pass implementation.
Must pass the "No Prior Knowledge Test": someone unfamiliar can implement from PRP alone.

## Input
$ARGUMENTS

## Phase 1: Understand Request
- Parse the feature description
- If vague → ask Director for clarification BEFORE proceeding
- Assess complexity: Low / Medium / High

## Phase 2: Research
- Search codebase for similar implementations and conventions
- Query Archon KB if available: `perform_rag_query()`
- Read design docs, CLAUDE.md rules
- Map integration points: which files need updates, which are new

## Phase 3: Clarify Ambiguities (Q&A)
CRITICAL: Present 10-20 multiple-choice questions to Director.
Categories: Architecture, Scope, Integration, Security, Testing.
One bad assumption = a thousand bad lines of code.
Wait for Director's answers before Phase 4.

## Phase 4: Write PRP
Create file: `PRPs/PRP-{kebab-case-name}.md` using template from `PRPs/templates/prp_base.md`
Include: Context References (file:line), Step-by-step Tasks, 5-level Validation Strategy.

## Phase 5: Self-Review
- [ ] All file references include paths (and line numbers where possible)
- [ ] "No Prior Knowledge Test" passed
- [ ] Every task has a VALIDATE command
- [ ] Security addressed (if applicable)
- [ ] **XSS review**: Any component rendering external data (WebSocket events, API responses, user input) through Markdown or dangerouslySetInnerHTML must specify allowedElements or sanitization — flag in Cross-Cutting Concerns
- [ ] **2D/3D sync impact**: If the feature changes room layouts, furniture, or agent positions, verify that BOTH 2D and 3D renderers are updated from the same data source (room-furniture.ts, calculateDeskSlots, project config). List affected rendering files in Cross-Cutting Concerns
- [ ] **Movement system impact**: If the feature changes room positions or layout presets, verify that pathfinding (movement-animator.ts), zone migration (office-timers.ts), and agent repositioning (repositionAgentsForLayout) all receive dynamic room bounds — not hardcoded ZONES constants

## Phase 6: Confidence Score
Rate #/10 probability of one-pass success.
- 9-10: All context present, simple, well-established patterns
- 7-8: Good context, moderate complexity
- 5-6: Missing context, high complexity
- <5: Recommend more research or scope reduction

## Rules
- NEVER write implementation code
- Q&A phase is NOT optional
- If Confidence < 6, recommend more research before execution
