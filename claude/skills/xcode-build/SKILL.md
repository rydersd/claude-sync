---
name: xcode-build
description: Build Xcode project for SpuriousTime-macOS
allowed-tools: [Bash, Read]
---

# Xcode Build

Build the SpuriousTime-macOS Xcode project with structured error reporting.

## When to Use

- After making code changes
- Before running tests
- Before launching app
- When build errors need diagnosis
- Part of workflow (via xcode-workflow skill)

## Process

### 1. Navigate to Project Directory
```bash
cd /Users/ryders/Documents/GitHub/SpuriousTime/SpuriousTime-macOS
```

### 2. Run Build Command
```bash
xcodebuild -project SpuriousTime-macOS.xcodeproj \
           -scheme SpuriousTime-macOS \
           -configuration Debug \
           build 2>&1 | tee /tmp/xcode-build.log
```

**Timeout**: 300 seconds (5 minutes)

### 3. Parse Build Output

Check for build result:
```bash
tail -20 /tmp/xcode-build.log | grep -E "BUILD (SUCCEEDED|FAILED)"
```

If failed, categorize errors:
```bash
# Import errors
grep "error:.*No such module" /tmp/xcode-build.log

# Type errors
grep "error:.*Type .* does not conform" /tmp/xcode-build.log

# Missing symbols
grep "error:.*Use of unresolved identifier" /tmp/xcode-build.log

# Duplicate symbols
grep "error:.*Duplicate interface definition" /tmp/xcode-build.log
```

### 4. Provide Structured Summary

**Success format**:
```
✅ BUILD SUCCEEDED

Duration: 45.2s
Target: SpuriousTime-macOS (Debug)
Next: Use /xcode-launch to run the app
```

**Failure format**:
```
❌ BUILD FAILED

Duration: 23.8s
Errors: 12

Error Categories:
- Import failures: 3 errors
  • No such module 'SpuriousCore' (3 files)

- Type mismatches: 5 errors
  • TaskItem.swift:145 - Type 'String' does not conform to 'Date'

- Missing symbols: 4 errors
  • DayPlannerView.swift:89 - Use of unresolved identifier 'formatDate'

Full log: /tmp/xcode-build.log

Next Steps:
1. Fix import errors first (usually dependency issues)
2. Then fix type errors
3. Finally fix missing symbols
```

## Error Handling

**Common errors and fixes**:

| Error Pattern | Likely Cause | Suggested Fix |
|---------------|--------------|---------------|
| `No such module 'SpuriousCore'` | Package dependency missing | Check Xcode project has SpuriousCore added |
| `Duplicate interface definition` | File added twice to project | Check project.pbxproj for duplicate entries |
| `Use of unresolved identifier` | Missing import or typo | Add import statement or fix identifier |
| `Type does not conform` | Protocol conformance issue | Implement required methods |
| `Build input file cannot be found` | File moved/deleted but still in project | Remove reference from Xcode project |

**Build timeout**:
If build exceeds 300s:
```
⏱️ BUILD TIMEOUT (>5 minutes)

This is unusual. Possible causes:
- Network issues (downloading dependencies)
- Indexing large codebase
- Background tasks blocking

Suggestion: Try /xcode-clean then rebuild
```

## Examples

**Example 1: Successful build**
```
User: "Build the project"
Assistant: *Invokes xcode-build skill*

✅ BUILD SUCCEEDED
Duration: 38.4s
Ready to launch or test.
```

**Example 2: Build with errors**
```
User: "Build"
Assistant: *Invokes xcode-build skill*

❌ BUILD FAILED
Duration: 15.2s
Errors: 3

Import failures:
- ThemeManager.swift:12 - No such module 'SpuriousCore'
- DensityManager.swift:8 - No such module 'SpuriousCore'
- ColorTokens.swift:5 - No such module 'SpuriousCore'

Root Cause: SpuriousCore package not linked to project

Fix: Add SpuriousCore as local package dependency in Xcode
```

## Success Criteria

- ✅ Build command executed with proper configuration
- ✅ Build output captured to log file
- ✅ Errors categorized by type
- ✅ Structured summary provided
- ✅ Next steps suggested based on result

## Notes

- Always builds Debug configuration (faster iteration)
- Logs saved to `/tmp/xcode-build.log` for inspection
- Build artifacts in `~/Library/Developer/Xcode/DerivedData/SpuriousTime-macOS-*/Build/Products/Debug/`
- Use `/xcode-clean` if build artifacts become corrupted
