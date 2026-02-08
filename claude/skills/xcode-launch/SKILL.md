---
name: xcode-launch
description: Launch SpuriousTime-macOS app from DerivedData
allowed-tools: [Bash, Read]
---

# Xcode Launch

Launch the most recently built SpuriousTime-macOS app.

## When to Use

- After successful build
- To test UI changes
- To verify bug fixes
- Before capturing logs
- Part of workflow (via xcode-workflow skill)

## Process

### 1. Kill Existing Instance
```bash
killall "SpuriousTime-macOS" 2>/dev/null || true
sleep 1
```

**Why**: Ensure fresh app launch without state from previous session

### 2. Locate App Bundle
```bash
APP_PATH=$(find ~/Library/Developer/Xcode/DerivedData -name "SpuriousTime-macOS.app" -path "*/Debug/*" 2>/dev/null | head -1)
```

### 3. Verify App Exists
```bash
if [ -z "$APP_PATH" ]; then
  echo "❌ APP NOT FOUND"
  echo "App not built yet. Run /xcode-build first."
  exit 1
fi
```

### 4. Launch App
```bash
open "$APP_PATH"
```

**Timeout**: 10 seconds (app should launch or fail fast)

### 5. Verify Launch
```bash
sleep 2
if pgrep -x "SpuriousTime-macOS" > /dev/null; then
  echo "✅ APP LAUNCHED"
else
  echo "❌ APP FAILED TO START"
  echo "Check Console.app for crash logs"
fi
```

## Output Format

**Success**:
```
✅ APP LAUNCHED

App: SpuriousTime-macOS.app
Path: ~/Library/Developer/Xcode/DerivedData/SpuriousTime-macOS-abc123/Build/Products/Debug/
Process ID: 12345

Next: Use /xcode-logs to monitor runtime logs
```

**App not found**:
```
❌ APP NOT FOUND

The app hasn't been built yet.

Next: Run /xcode-build first, then launch
```

**Launch failed**:
```
❌ APP FAILED TO START

App bundle exists but failed to launch.
Possible causes:
- Code signing issue
- Missing entitlements
- Runtime crash on startup

Check crash logs:
1. Open Console.app
2. Filter: "SpuriousTime-macOS"
3. Look for crash reports
```

## Error Handling

**Common launch failures**:

| Issue | Symptom | Fix |
|-------|---------|-----|
| App not built | "APP NOT FOUND" | Run `/xcode-build` |
| Code signing | Launch fails silently | Check code signing settings in Xcode |
| Missing frameworks | Crash on launch | Verify SpuriousCore is embedded |
| Entitlements | Sandbox violation | Check entitlements in Xcode |
| Already running | "Address already in use" | Kill existing instance first (skill does this) |

## Examples

**Example 1: Successful launch**
```
User: "Launch the app"
Assistant: *Invokes xcode-launch skill*

✅ APP LAUNCHED
Process ID: 12345
Ready to test changes.
```

**Example 2: App not built**
```
User: "Run it"
Assistant: *Invokes xcode-launch skill*

❌ APP NOT FOUND
Need to build first.

*Suggests: Run /xcode-workflow build-launch*
```

**Example 3: Launch as part of workflow**
```
User: "Build and run"
Assistant: *Invokes xcode-workflow skill with build-launch*

✅ BUILD SUCCEEDED (45.2s)
✅ APP LAUNCHED

Monitoring logs... (Ctrl+C to stop)
```

## Success Criteria

- ✅ Existing app instance killed cleanly
- ✅ App bundle located in DerivedData
- ✅ App launched successfully
- ✅ Process verified running
- ✅ Helpful error messages if failed

## Notes

- Always kills existing instance before launching (fresh state)
- Finds most recent Debug build automatically
- Does NOT rebuild - launches existing build
- For fresh build + launch, use `/xcode-workflow build-launch`
- App launch is asynchronous - skill waits 2s to verify start
