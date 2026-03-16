---
description: "Execute a PRP plan task by task with self-correction loop"
argument-hint: "[PRP filename or feature name]"
---

# /execute-prp — Implement from PRP

## Input
$ARGUMENTS

## Process

### 1. Load PRP
- Find PRP file in `PRPs/` matching the argument
- Read Mandatory Reading files listed in PRP
- Verify all referenced files exist

### 2. Execute Tasks
For each task in order:
1. Read task instructions completely
2. Implement exactly as specified
3. Run the VALIDATE command for that task
4. If FAIL → self-correct (max 3 retries) → if still FAIL → STOP and report to Director
5. If PASS → mark task done, move to next

### 3. Self-Correction Loop (max 3 retries per task)
```
Attempt → Validate → FAIL?
  → Read error → Analyze root cause → Fix → Validate again
  → Still FAIL after 3 attempts? → STOP → Report to Director
```

### 4. Completion
After all tasks:
1. Run full validation pyramid: compile → unit test → integration test
2. Update PRP status: DRAFT → IMPLEMENTED
3. Report summary: tasks completed, failures, time estimate

## Rules
- Follow PRP tasks IN ORDER — do not skip or reorder
- NEVER add features not in PRP scope
- Stop after 3 failed retries — Director decides next step
- Run `mvn compile` (or equivalent) after each subtask
