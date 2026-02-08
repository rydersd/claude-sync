---
description: Orchestrate common Xcode development workflows
allowed-tools: [Bash, Read, Skill]
---

# Xcode Workflow

Composite skill that orchestrates common Xcode development workflows by combining atomic skills.

## When to Use

- Want to run multiple Xcode operations in sequence
- Common dev patterns (build-launch-logs, build-test, etc.)
- Automate repetitive workflows
- Ensure steps run in correct order

## Supported Workflows

### 1. build-launch-logs (Most Common)
**Usage**: "Build and run"
**Steps**:
1. `xcode-build` - Build the project
2. `xcode-launch` - Launch the app
3. `xcode-logs` - Monitor runtime logs

**When**: Making UI changes, testing features, regular development

**Example**:
```
User: "Build and run the app"
Assistant: *Invokes xcode-workflow: build-launch-logs*

✅ BUILD SUCCEEDED (38.4s)
✅ APP LAUNCHED (PID: 12345)
📋 MONITORING LOGS

[14:32:15] [TaskManager] Loaded 42 tasks
[14:32:16] [ThemeManager] Applied theme: Light
...
```

---

### 2. build-test
**Usage**: "Build and test"
**Steps**:
1. `xcode-build` - Build the project
2. `xcode-test` - Run test suite

**When**: Before committing, validating changes, CI/CD

**Example**:
```
User: "Build and run tests"
Assistant: *Invokes xcode-workflow: build-test*

✅ BUILD SUCCEEDED (38.4s)
✅ ALL TESTS PASSED (12.3s)

47 tests passed
Safe to commit.
```

---

### 3. clean-build
**Usage**: "Clean build"
**Steps**:
1. `xcode-clean` (Level 2: Deep) - Remove DerivedData
2. `xcode-build` - Fresh build from scratch

**When**: Build corruption, module import errors, weird build failures

**Example**:
```
User: "Clean and rebuild"
Assistant: *Invokes xcode-workflow: clean-build*

✅ DEEP CLEAN COMPLETED (15.8s)
  Freed 1.2 GB

✅ BUILD SUCCEEDED (145.6s)
  Fresh build complete
```

---

### 4. test-watch
**Usage**: "Full validation"
**Steps**:
1. `xcode-build` - Build the project
2. `xcode-test` - Run test suite
3. `xcode-launch` - Launch the app
4. `xcode-logs` - Monitor logs for verification

**When**: Before major commits, release validation, comprehensive testing

**Example**:
```
User: "Full validation before release"
Assistant: *Invokes xcode-workflow: test-watch*

✅ BUILD SUCCEEDED (38.4s)
✅ ALL TESTS PASSED (12.3s)
✅ APP LAUNCHED
📋 MONITORING LOGS

All checks passed. Ready for release.
```

---

### 5. quick-launch
**Usage**: "Just run it"
**Steps**:
1. `xcode-launch` - Launch existing build
2. `xcode-logs` - Monitor logs

**When**: Iterating quickly, build already succeeded, testing same binary

**Example**:
```
User: "Run it again"
Assistant: *Invokes xcode-workflow: quick-launch*

✅ APP LAUNCHED (PID: 12346)
📋 MONITORING LOGS

Ready to test.
```

---

## Workflow Selection Logic

The skill automatically selects appropriate workflow based on user intent:

| User Says | Workflow | Rationale |
|-----------|----------|-----------|
| "build and run" | build-launch-logs | Most common: build → launch → monitor |
| "build and test" | build-test | Pre-commit validation |
| "clean build" | clean-build | Corruption fix |
| "full validation" | test-watch | Comprehensive check |
| "run it" | quick-launch | Fastest iteration |
| "test everything" | build-test | Thorough testing |

## Error Handling

### Early Termination on Failure

Workflows stop at first failure:

```
❌ WORKFLOW FAILED at step 1/3

Workflow: build-launch-logs
Failed step: xcode-build

Error: 12 build errors

Resolution:
1. Fix build errors
2. Re-run workflow

Not proceeding to launch (build failed)
```

### Continue on Warning

Some failures are non-critical:

```
⚠️ WORKFLOW WARNING at step 2/4

Workflow: test-watch
Warning step: xcode-test - 2 tests failed

Continue to launch? (y/n)

User: "y"

✅ APP LAUNCHED
📋 MONITORING LOGS

Note: Fix failing tests before committing
```

## Custom Workflows

Users can request custom sequences:

```
User: "Clean, build, test, then launch"
Assistant: Creating custom workflow:
1. xcode-clean (Deep)
2. xcode-build
3. xcode-test
4. xcode-launch
5. xcode-logs

Proceeding...

✅ DEEP CLEAN (15.8s)
✅ BUILD SUCCEEDED (145.6s)
✅ ALL TESTS PASSED (12.3s)
✅ APP LAUNCHED
📋 MONITORING LOGS

Custom workflow complete.
```

## Performance Optimization

### Skip Redundant Steps

If build already succeeded and user just wants logs:
```
User: "Show me the logs"
*Skip build if recent (< 5 min) and successful*
*Invoke quick-launch instead of build-launch-logs*

✅ Using existing build (38s old)
✅ APP LAUNCHED
📋 MONITORING LOGS
```

### Parallel Execution (Not Supported)

Build and test cannot run in parallel (sequential dependency).
Clean must complete before build.
Launch must wait for build.

## Output Format

**Workflow start**:
```
🔄 STARTING WORKFLOW: build-launch-logs

Steps:
1. Build project
2. Launch app
3. Monitor logs

Estimated time: ~60 seconds
```

**Workflow progress**:
```
✅ Step 1/3: BUILD SUCCEEDED (38.4s)
✅ Step 2/3: APP LAUNCHED
🔄 Step 3/3: Monitoring logs...
```

**Workflow complete**:
```
✅ WORKFLOW COMPLETE: build-launch-logs

Total time: 42.7s
All steps succeeded

Status:
- Build: ✅ 38.4s
- Launch: ✅ 2.3s
- Logs: Running (Ctrl+C to stop)
```

## Examples

**Example 1: Standard development**
```
User: "Build and run"
Assistant: *Selects build-launch-logs workflow*

🔄 WORKFLOW: build-launch-logs

✅ BUILD (38.4s)
✅ LAUNCH (2.3s)
📋 LOGS...

[14:32:15] App started successfully
```

**Example 2: Pre-commit validation**
```
User: "Make sure tests pass before I commit"
Assistant: *Selects build-test workflow*

🔄 WORKFLOW: build-test

✅ BUILD (38.4s)
✅ TESTS (12.3s) - 47 passed

Safe to commit ✅
```

**Example 3: Corruption recovery**
```
User: "Build is broken with weird errors"
Assistant: Sounds like corruption. Let's clean first.

*Selects clean-build workflow*

🔄 WORKFLOW: clean-build

✅ DEEP CLEAN (15.8s) - Freed 1.2 GB
✅ BUILD (145.6s)

Fresh build succeeded ✅
```

**Example 4: Custom sequence**
```
User: "Clean, build, and test"
Assistant: *Creates custom workflow*

🔄 CUSTOM WORKFLOW

1. ✅ CLEAN (15.8s)
2. ✅ BUILD (145.6s)
3. ✅ TEST (12.3s) - 47 passed

All steps completed ✅
```

## Success Criteria

- ✅ Correct workflow selected based on user intent
- ✅ Steps executed in proper sequence
- ✅ Early termination on critical failures
- ✅ Clear progress indication
- ✅ Summary of all steps at completion

## Notes

- Workflows are optimized for common patterns
- Can skip redundant steps if recent build exists
- Custom workflows supported via natural language
- Logs continue running until user cancels (Ctrl+C)
- Build times vary: incremental (30-60s), clean (2-5min)
