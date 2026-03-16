# PRP: {Feature Name}

Date: {YYYY-MM-DD}
Author: AI (reviewed by Director)
Status: DRAFT
Confidence: {#}/10

## Feature Description
{Detailed description}

## Problem Statement / Solution Statement

## Metadata
- Type: [New Capability / Enhancement / Refactor / Bug Fix]
- Complexity: [Low / Medium / High]
- Systems Affected: {list}

---

## CONTEXT REFERENCES

### Mandatory Reading
- `path/to/file` — Why: {reason}

### New Files to Create
- `path/to/NewFile` — {description}

### Patterns to Follow
{Code snippets from existing project}

---

## STEP-BY-STEP TASKS

### Task 1: {action} {target}
- **IMPLEMENT**: {specific detail}
- **PATTERN**: Mirror `path/to/existing`
- **VALIDATE**: `{executable command}`

---

## CROSS-CUTTING CONCERNS

### Cleanup & Lifecycle
- List all subscriptions, event listeners, timers created — each must have a cleanup path
- PixiJS: `destroy()` must remove ticker handlers, Zustand subscriptions, DOM event listeners
- React hooks: cleanup function must cancel async work (use `destroyed` flag for pending promises)
- Services: `onEvent()` must return unsubscribe function; `disconnect()` must clean up sockets

### Input Validation
- All external data (WebSocket, REST, URL params) must be validated at the service boundary
- Define a validation function for each incoming data shape — reject and log invalid data
- Never cast `unknown` to a type with `as` — use type guards or validation functions

### Animation & Timing
- All animation increments must scale by `delta` time — never use fixed frame counts
- Document target duration in milliseconds, not frames
- Grid/state updates must happen at the correct lifecycle point (e.g., on arrival, not on start)

### Scaffolding Checklist (for greenfield tasks)
- `.gitignore` must include: `*.tsbuildinfo`, `__pycache__/`, `*.log`, `coverage/`, `dist/`
- ESLint: use flat config (`eslint.config.js`) for v9+
- Tailwind v4: use `@tailwindcss/postcss` plugin
- Testing: install `@testing-library/dom` alongside `@testing-library/react`
- Vitest: separate `vitest.config.ts` file (not inside `vite.config.ts`)
- Environment variables: use `import.meta.env.VITE_*` for configurable URLs

---

## VALIDATION STRATEGY
1. Syntax & Type Check
2. Unit Tests
3. Integration Tests
4. Manual Validation
5. E2E Testing (optional)

## ACCEPTANCE CRITERIA
- [ ] All tasks completed
- [ ] All validation levels passed
- [ ] No regressions
- [ ] Cleanup paths verified (no leaked subscriptions/listeners/timers)
- [ ] Input validation at all external boundaries

## NOTES
{Trade-offs, alternatives considered}
