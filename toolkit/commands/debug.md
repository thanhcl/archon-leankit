---
description: "Diagnose and fix bugs"
argument-hint: "[error message, symptoms, or file reference]"
---

# /debug — Diagnose & Fix

## Input
$ARGUMENTS

## Process
1. **Reproduce**: Understand the error/symptom
2. **Hypothesize**: Form 2-3 theories about root cause
3. **Investigate**: Read relevant code, check logs, run targeted tests
4. **Fix**: Apply minimal fix for root cause (not symptoms)
5. **Verify**: Run tests, confirm fix, check for regressions
6. **Report**: What was wrong, why, how fixed, prevention

## Rules
- Fix ROOT CAUSE, not symptoms
- Minimal change — don't refactor while debugging
- Always write a regression test for the bug
- If fix requires architecture change → STOP, report to Director
