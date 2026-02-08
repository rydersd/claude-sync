# Frontend Debug Skill

Systematic approach to debugging UI issues using screenshot analysis, differential diagnosis, and targeted fixes.

## Overview

This skill provides a structured methodology for debugging frontend issues across SwiftUI, React, and web applications. It emphasizes:

1. **Screenshot-first analysis** - Always get visual evidence before investigating code
2. **Differential diagnosis** - Compare working vs broken examples
3. **Systematic checklists** - Framework-specific common issues
4. **Targeted fixes** - Minimal changes with maximum impact
5. **Pattern documentation** - Prevent recurrence

## When It Activates

The skill triggers on keywords like:
- "debug UI" / "fix layout"
- "text cut off" / "truncation"
- "not clickable" / "gesture not working"
- "overlap" / "spacing issue"
- "looks wrong" / "broken visually"
- "screenshot shows"

And patterns like:
- "(debug|fix|investigate).*(UI|layout|frontend)"
- "(text|title).*(cut off|truncat)"
- "(gesture|drag).*(not work|broken)"
- "(theme|color).*(not apply|wrong)"

## Usage

### Basic Flow

1. **Request screenshot** if not provided
2. **Read screenshot** and identify specific visual issues
3. **Read relevant files** completely (no limit/offset)
4. **Find working example** for comparison (if applicable)
5. **Compare side-by-side** to identify differences
6. **Apply targeted fix** based on evidence
7. **Build and verify**
8. **Document pattern** for prevention

### Example Session

```
User: "The task titles are getting cut off"
[shares screenshot]

Skill:
✅ Screenshot shows text truncated without "..."
📖 Reading TaskRowView.swift completely
🔍 Found lineLimit(2) without frame constraint
🔎 Comparing with working DayPlannerTaskBox.swift
⚖️ Working code has .frame(maxWidth: .infinity)
🔧 Applied fix to TaskRowView.swift
✅ Build succeeded
📋 Test: Create long title, verify truncates with "..."
```

## Common Patterns Fixed

### SwiftUI
- Text truncation (missing `.frame(maxWidth: .infinity)`)
- Drag/drop (missing `.contentShape(Rectangle())`)
- Overlay blocking (`.allowsHitTesting(true)` on overlay)
- DatePicker format (use custom overlay for "MMM d" format)
- Theme not applying (hardcoded colors instead of `themeManager`)

### React
- Unnecessary re-renders (missing `useMemo`/`useCallback`)
- Stale closures (missing dependencies in hooks)
- List rendering (missing unique `key` props)

### Web/CSS
- Layout issues (flexbox/grid property conflicts)
- Overflow (hidden content, missing scrolling)
- Z-index stacking (positioning context issues)

## Files

- `SKILL.md` - Complete skill implementation
- `README.md` - This file

## Framework Coverage

- ✅ SwiftUI (macOS/iOS)
- ✅ React (web/React Native)
- ✅ Web (HTML/CSS)

## Integration

Registered in `/Users/ryders/.claude/skills/skill-rules.json`:
- Type: `domain`
- Enforcement: `suggest`
- Priority: `high`

## Benefits

- **Faster diagnosis** - Structured approach vs random exploration
- **Better fixes** - Evidence-based vs theoretical
- **Pattern learning** - Documented for future reference
- **Prevention** - Identifies root causes to avoid recurrence

## Created

2026-01-17 - Based on real debugging sessions in SpuriousTime project
