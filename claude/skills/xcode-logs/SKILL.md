---
name: xcode-logs
description: Capture runtime logs from SpuriousTime-macOS using unified logging
allowed-tools: [Bash, Read]
---

# Xcode Logs

Capture and monitor runtime logs from the running SpuriousTime-macOS app using macOS unified logging system.

## When to Use

- After launching app to see runtime behavior
- When debugging UI issues
- To verify feature implementation
- To diagnose crashes or errors
- Part of workflow (via xcode-workflow skill)

## Process

### 1. Verify App is Running
```bash
if ! pgrep -x "SpuriousTime-macOS" > /dev/null; then
  echo "⚠️ APP NOT RUNNING"
  echo "Launch app first with /xcode-launch"
  exit 1
fi
```

### 2. Start Log Streaming
```bash
log stream \
  --predicate 'subsystem == "com.spurioustime.SpuriousTime-macOS"' \
  --level debug \
  --style compact \
  --color auto
```

**No timeout**: Runs until user cancels (Ctrl+C) or workflow completes

### 3. Optional: Save to File
If user requests log capture:
```bash
log stream \
  --predicate 'subsystem == "com.spurioustime.SpuriousTime-macOS"' \
  --level debug \
  --style compact \
  --color auto \
  | tee /tmp/spurioustime-logs-$(date +%Y%m%d-%H%M%S).log
```

## Output Format

**When starting**:
```
📋 MONITORING LOGS

Subsystem: com.spurioustime.SpuriousTime-macOS
Level: Debug
Style: Compact

Press Ctrl+C to stop...

[Timestamp] [Category] Message
[14:32:15] [TaskManager] Loaded 42 tasks from database
[14:32:16] [ThemeManager] Applied theme: Light Mode
[14:32:17] [DayPlanner] Rendered timeline for 2026-01-31
```

**When app not running**:
```
⚠️ APP NOT RUNNING

The app must be running to capture logs.

Next: Run /xcode-workflow build-launch-logs
```

## Log Categories

Common log categories in SpuriousTime-macOS:

| Category | Purpose | Example Messages |
|----------|---------|------------------|
| `TaskManager` | Task CRUD operations | "Created task [ID]", "Updated task [ID]" |
| `DayPlanner` | Day planner view updates | "Rendered timeline", "Task moved to [time]" |
| `GanttView` | Gantt chart operations | "Drag started", "Task resized" |
| `ThemeManager` | Theme changes | "Applied theme: Dark", "Color derived: #FF0000" |
| `Database` | SQLite operations | "Query executed in 3ms", "Migration complete" |
| `LLM` | AI/LLM operations | "Sent prompt to Anthropic", "Received response" |

## Filtering Logs

**By category**:
```bash
log stream \
  --predicate 'subsystem == "com.spurioustime.SpuriousTime-macOS" AND category == "TaskManager"' \
  --level debug
```

**By level** (error, fault, info, debug):
```bash
# Errors only
log stream \
  --predicate 'subsystem == "com.spurioustime.SpuriousTime-macOS"' \
  --level error
```

**By time range** (for saved logs):
```bash
log show \
  --predicate 'subsystem == "com.spurioustime.SpuriousTime-macOS"' \
  --last 5m  # Last 5 minutes
```

## Examples

**Example 1: Monitor live logs**
```
User: "Show me the logs"
Assistant: *Invokes xcode-logs skill*

📋 MONITORING LOGS
Press Ctrl+C to stop...

[14:32:15] [TaskManager] Loaded 42 tasks
[14:32:16] [ThemeManager] Applied Light theme
[14:32:17] [UI] Window resized to 1200×800
```

**Example 2: Capture logs for debugging**
```
User: "Capture logs while I test the drag feature"
Assistant: *Invokes xcode-logs skill with file capture*

📋 CAPTURING LOGS
Saving to: /tmp/spurioustime-logs-20260131-143215.log

Perform your test actions, then let me know when done.

[User tests drag feature]

User: "Done"
Assistant: Stopping log capture...

Captured 157 log entries. Analyzing...

Found drag events:
- [14:33:01] [GanttView] Drag started: Task [ABC123]
- [14:33:02] [GanttView] Drag moved: x=450, y=120
- [14:33:03] [GanttView] Drag ended: Saved new time
- [14:33:03] [TaskManager] Updated task [ABC123] scheduledTime

✅ Drag feature logging looks correct
```

**Example 3: Error detection**
```
User: "Check logs for errors"
Assistant: *Invokes xcode-logs skill with error level*

📋 MONITORING ERRORS ONLY

[14:34:22] [Database] ERROR: Failed to open connection
[14:34:22] [Database] FAULT: SQLite error code 14

🚨 Database connection failing!

Next: Investigate database file corruption
```

## Error Handling

**App crashes while logging**:
```
🚨 APP CRASHED

Last log entries before crash:
[14:35:01] [TaskManager] Processing 1000 tasks
[14:35:02] [Memory] Warning: High memory usage
[14:35:03] Process terminated

Check crash report:
~/Library/Logs/DiagnosticReports/SpuriousTime-macOS*.crash
```

**No logs appearing**:
```
⚠️ NO LOGS DETECTED

Possible causes:
1. App not using unified logging (os.log)
2. Wrong subsystem identifier
3. Log level too low (try --level debug)

Verify subsystem in code:
Logger(subsystem: "com.spurioustime.SpuriousTime-macOS", category: "...")
```

## Success Criteria

- ✅ App verified running before starting logs
- ✅ Correct subsystem predicate used
- ✅ Logs streaming in real-time
- ✅ Optional file capture if requested
- ✅ Helpful error messages if issues occur

## Notes

- Uses macOS unified logging (not print() or NSLog)
- Logs persist in system log database
- Can retrieve historical logs with `log show`
- `--style compact` omits redundant metadata for readability
- Debug level includes all messages (use `--level error` for errors only)
- Logs survive app restarts (persisted by system)
