---
name: xcode-test
description: Run unit and integration tests for SpuriousTime-macOS
allowed-tools: [Bash, Read]
---

# Xcode Test

Run the test suite for SpuriousTime-macOS with detailed test result reporting.

## When to Use

- Before committing code changes
- After implementing new features
- When validating bug fixes
- As part of CI/CD workflow
- Part of workflow (via xcode-workflow skill)

## Process

### 1. Navigate to Project Directory
```bash
cd /Users/ryders/Documents/GitHub/SpuriousTime/SpuriousTime-macOS
```

### 2. Run Test Command
```bash
xcodebuild test \
           -project SpuriousTime-macOS.xcodeproj \
           -scheme SpuriousTime-macOS \
           -configuration Debug \
           -destination 'platform=macOS' 2>&1 | tee /tmp/xcode-test.log
```

**Timeout**: 180 seconds (3 minutes)

### 3. Parse Test Results

Extract test summary:
```bash
# Total tests run
grep "Test Suite.*passed" /tmp/xcode-test.log | tail -1

# Failed tests
grep "Test Case.*failed" /tmp/xcode-test.log

# Test execution time
grep "Test Suite.*seconds" /tmp/xcode-test.log | tail -1
```

### 4. Provide Structured Summary

**Success format**:
```
✅ ALL TESTS PASSED

Tests Run: 47
Duration: 12.3s
Coverage: Not measured (add --enable-code-coverage to measure)

Next: Safe to commit changes
```

**Failure format**:
```
❌ TESTS FAILED

Tests Run: 47
Passed: 44
Failed: 3

Failed Tests:
1. TaskManagerTests.testTaskPersistence
   Location: TaskManagerTests.swift:145
   Error: Expected 5 tasks, found 3

2. DayPlannerTests.testSchedulingConflict
   Location: DayPlannerTests.swift:89
   Error: Assertion failed: Expected conflict detection

3. ThemeManagerTests.testColorDerivation
   Location: ThemeManagerTests.swift:67
   Error: Color mismatch - expected #FF0000, got #FE0001

Duration: 15.8s
Full log: /tmp/xcode-test.log

Next Steps:
1. Fix failing tests one by one
2. Re-run tests after each fix
3. Investigate root cause (test issue vs code issue)
```

**No tests format**:
```
⚠️ NO TESTS FOUND

The project has no test target or tests are not configured.

Suggestion: Create test target with:
1. File → New → Target → macOS Unit Testing Bundle
2. Add test files to SpuriousTime-macOSTests/
```

## Error Handling

**Common test failures**:

| Failure Pattern | Likely Cause | Suggested Fix |
|-----------------|--------------|---------------|
| `Expected X, found Y` | Business logic changed | Update test expectations |
| `Assertion failed` | Code behavior changed | Verify intentional change |
| `Timeout` | Async test not completing | Increase XCTestExpectation timeout |
| `Fatal error` | Code crash during test | Debug with breakpoint |
| `Test target not found` | Test scheme misconfigured | Check scheme settings |

**Test timeout**:
If tests exceed 180s:
```
⏱️ TEST TIMEOUT (>3 minutes)

Tests taking too long. Possible causes:
- Infinite loop in test
- Deadlock in async code
- Performance regression

Suggestion: Run tests individually to isolate slow test
```

## Examples

**Example 1: All tests pass**
```
User: "Run tests"
Assistant: *Invokes xcode-test skill*

✅ ALL TESTS PASSED
Tests: 47 passed in 12.3s
Safe to commit.
```

**Example 2: Some tests fail**
```
User: "Test the changes"
Assistant: *Invokes xcode-test skill*

❌ TESTS FAILED
3 of 47 tests failed

TaskManagerTests.testTaskPersistence failed:
  Expected 5 tasks after save, found 3

This suggests task persistence is broken by recent changes.
Check TaskRepository implementation.
```

## Test-Driven Development Integration

When working with TDD workflow:
1. Write failing test first
2. Run `/xcode-test` - should fail with expected reason
3. Implement minimal code to pass
4. Run `/xcode-test` again - should pass
5. Refactor if needed
6. Run `/xcode-test` - should still pass

## Success Criteria

- ✅ Test command executed successfully
- ✅ Test results parsed and categorized
- ✅ Pass/fail count reported
- ✅ Failed tests listed with file locations
- ✅ Next steps provided based on results

## Notes

- Tests run on macOS destination (not iOS simulator)
- Test logs saved to `/tmp/xcode-test.log`
- Use `--enable-code-coverage` flag to measure coverage (not default)
- Individual test can be run with: `xcodebuild test -only-testing:SpuriousTime-macOSTests/TestClassName/testMethodName`
