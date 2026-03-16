---
description: "Generate and run tests for recent changes"
argument-hint: "[scope or file reference]"
---

# /test — Generate & Run Tests

## Role
Tester agent. Ensure code quality through comprehensive testing.

## Input
$ARGUMENTS

## Process
1. Identify what changed (git diff or plan reference)
2. Determine test types needed: unit, integration, security
3. Write tests following project conventions
4. Run all tests and report results

## Report Format
```
TEST REPORT:
- Tests written: {count}
- Tests passed: {count}
- Tests failed: {count} — {details}
- Coverage: {if available}
- Gaps: {untested scenarios}
```

## Rules
- Test naming: `methodName_scenario_expectedResult`
- Never test private methods directly
- Every test: clear Arrange / Act / Assert
- Security code MUST have tests
