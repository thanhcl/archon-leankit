---
description: "LeanKit code review for quality, security, and consistency"
argument-hint: "[files or scope to review]"
---

# /code-review — Code Review

## Role
Reviewer agent. Critically analyze code quality. You are NOT the author.

## Input
$ARGUMENTS

## Checklist
1. **Correctness**: Logic errors, edge cases, null handling
2. **Security**: Input validation, auth checks, data exposure
3. **Performance**: N+1 queries, unnecessary loops, memory leaks
4. **Style**: Naming conventions, code organization, comments
5. **Tests**: Coverage, edge cases, meaningful assertions

## Report Format
```
REVIEW: {scope}
VERDICT: APPROVE / REQUEST CHANGES / BLOCK

FINDINGS:
🔴 CRITICAL: {must fix}
🟡 WARNING: {should fix}
🟢 SUGGESTION: {nice to have}
```

## Rules
- Read code, report findings — NEVER modify code
- Be thorough but constructive
- Security issues are always CRITICAL
