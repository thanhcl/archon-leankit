---
name: e2e-test
description: "End-to-end testing with parallel sub-agents. Run after implementation."
---
# E2E Testing

## 6 Phases

### Phase 1: Parallel Research (3 sub-agents)
- Sub-agent A: App structure, API endpoints, user journeys
- Sub-agent B: Database schema, data flows, validation queries
- Sub-agent C: Bug hunting, security audit, potential issues

### Phase 2: Start Application
Using Sub-agent A's startup instructions.

### Phase 3: Create Task List
From research findings, create test tasks.

### Phase 4: Execute Tests
- API Testing: curl/httpie with valid + invalid params, auth checks
- DB Validation: SQL queries to verify records after each API call
- Browser Testing: agent-browser skill for UI (optional)
- Security Tests: data exposure, auth enforcement, input validation
- Auto-fix: if issue found -> fix -> re-test -> verify no regression

### Phase 5: Cleanup
Stop app, close browsers, reset test data if needed.

### Phase 6: Report
```
E2E REPORT:
- Endpoints tested: {count}
- Journeys tested: {count}
- Issues found: {count} (fixed: {n}, remaining: {n})
- Security: {PASS/FAIL per category}
```
