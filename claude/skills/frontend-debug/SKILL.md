---
name: frontend-debug
description: Systematic frontend debugging for SwiftUI/React/web apps using visual inspection and differential diagnosis
allowed-tools: [Read, Grep, Glob, Bash, Edit, Write]
---

# Frontend Debugging Skill

Systematic approach to debugging UI issues using screenshot analysis, differential diagnosis, and targeted fixes.

## When to Use This Skill

Invoke when user reports:
- UI not rendering as expected
- Layout issues (overlap, spacing, alignment)
- Interactive elements not responding
- Visual glitches or flickering
- Performance issues (jank, lag)
- "It looks wrong" or "It's broken visually"

## Debugging Protocol

### Phase 1: Visual Inspection (REQUIRED)

**STOP if no screenshot provided:**

```
I need to see what you're seeing to debug this properly.

Please share a screenshot showing:
- The entire window/view with the issue
- Any error states or visual glitches
- What you expected vs what you're seeing

Share path to screenshot or drag it into the chat.
```

**Once screenshot received:**

1. Read the screenshot file completely
2. Identify specific visual problems:
   - Overlapping elements → z-index/layer issues
   - Spacing issues → padding/margin problems
   - Alignment issues → flexbox/grid/HStack/VStack
   - Truncation/overflow → frame constraints missing
   - Color/contrast → theme application issues
   - Icon/text overlap → layout calculation bugs

3. Describe what you observe in technical terms:
   ```
   I see [specific issue]:
   - Element X is overlapping element Y
   - Text is truncated at [position]
   - Spacing between A and B is [measurement]
   - Colors don't match theme
   ```

### Phase 2: Code Investigation

**Read relevant files COMPLETELY (no limit/offset):**

```bash
# Find the view file
find . -name "*[ViewName]*" -type f

# Read it completely
# Use Read tool with no limit parameter
```

**Check for common anti-patterns:**

#### SwiftUI Issues
- Missing `.frame(maxWidth: .infinity)` for text truncation
- No `.contentShape(Rectangle())` before `.onDrag` or `.onTapGesture`
- `.allowsHitTesting(true)` on overlays blocking interactions
- Hardcoded sizes instead of using density manager
- Using `Color.blue` instead of `themeManager.accent`
- DatePicker default formatting instead of custom overlays
- Missing `@ViewBuilder` on conditional views
- State updates not triggering re-render

#### React Issues
- Missing key props on list items
- Stale closures capturing old state
- Unnecessary re-renders (no `useMemo`/`useCallback`)
- Z-index conflicts in modals/dropdowns
- CSS-in-JS specificity issues

#### Web Issues
- `overflow: hidden` hiding content
- Flexbox/Grid property conflicts
- Position: absolute without relative parent
- Viewport units breaking on mobile
- Large DOM causing layout thrashing

### Phase 3: Differential Diagnosis (MANDATORY)

**NEVER theorize without comparing first.**

If some UI works correctly and some doesn't:

1. **Find working example** in codebase:
   ```bash
   # Search for similar working component
   grep -r "similar pattern" --include="*.swift"
   ```

2. **Read working code completely**

3. **Read broken code completely**

4. **Compare side-by-side** and list SPECIFIC differences:
   ```
   Working code has:
   - Line 45: .contentShape(Rectangle())
   - Line 67: .frame(maxWidth: .infinity)

   Broken code missing:
   - No contentShape modifier
   - No frame constraint
   ```

5. **Verify difference explains behavior:**
   ```
   This explains why [issue occurs]:
   - Missing contentShape → gestures don't trigger
   - Missing frame → text doesn't truncate
   ```

### Phase 4: Targeted Fix

**Apply working pattern to broken code:**

```swift
// BEFORE (broken)
Text(title)
    .lineLimit(2)
    .truncationMode(.tail)

// AFTER (working pattern applied)
Text(title)
    .lineLimit(2)
    .truncationMode(.tail)
    .frame(maxWidth: .infinity, alignment: .leading)  // ADDED
```

**Build and verify:**

```bash
cd [project-dir]
xcodebuild -project [project].xcodeproj -scheme [scheme] build
```

**Document the fix:**

```
Fixed: [Issue description]

Root Cause: [Specific line causing problem]
- Line X in [file]: [problematic code]

Fix Applied:
- Added [modifier/property] at line Y
- Reason: [why this fixes it]

Verification:
- Build: ✅ SUCCESS
- Manual test needed: [what to check]
```

## Framework-Specific Checklists

### SwiftUI Layout Debugging

When debugging layout issues, check these in order:

**1. Frame Constraints**
```swift
// Text truncation
.frame(maxWidth: .infinity, alignment: .leading)

// Fixed size preventing expansion
.fixedSize() // Remove if causing issues

// Minimum heights
.frame(minHeight: 60)
```

**2. Gesture Recognition**
```swift
// MUST have contentShape before gestures
.contentShape(Rectangle())
.onDrag { ... }

// Hit testing
.allowsHitTesting(false) // On overlays to pass through
```

**3. Conditional Rendering**
```swift
// MUST use @ViewBuilder
@ViewBuilder
var conditionalView: some View {
    if condition {
        ViewA()
    } else {
        ViewB()
    }
}
```

**4. State Updates**
```swift
// Trigger re-render with .id()
.id(taskId)

// ObservedObject for external state
@ObservedObject var manager: ThemeManager
```

**5. Theme Application**
```swift
// DON'T hardcode
.foregroundColor(.blue) // ❌

// DO use theme manager
.foregroundStyle(themeManager.accent) // ✅
```

### React Debugging

**1. Re-render Issues**
```javascript
// Check with React DevTools
// Highlight updates to see unnecessary renders

// Fix with memoization
const memoizedValue = useMemo(() => computeExpensive(), [dep])
const memoizedCallback = useCallback(() => { ... }, [dep])
```

**2. State Closure Issues**
```javascript
// Stale closure
useEffect(() => {
  setTimeout(() => console.log(count), 1000) // ❌ Stale
}, [])

// Fixed
useEffect(() => {
  setTimeout(() => console.log(count), 1000)
}, [count]) // ✅ Updated dependency
```

**3. List Rendering**
```javascript
// MUST have unique keys
{items.map(item => (
  <Component key={item.id} {...item} /> // ✅
))}
```

### Web CSS Debugging

**1. Layout Issues**
```css
/* Flexbox alignment */
.container {
  display: flex;
  align-items: center; /* Vertical */
  justify-content: space-between; /* Horizontal */
}

/* Grid layout */
.grid {
  display: grid;
  grid-template-columns: 1fr 2fr;
  gap: 16px;
}
```

**2. Overflow Issues**
```css
/* Hidden content */
overflow: hidden; /* Remove if hiding content */

/* Scrollable */
overflow-y: auto;
max-height: 400px;
```

**3. Z-index Stacking**
```css
/* Create stacking context */
.modal {
  position: fixed;
  z-index: 1000;
}

/* Parent must be positioned */
.parent {
  position: relative; /* Required for absolute children */
}
```

## Diagnostic Logging

Add strategic logging to understand runtime behavior:

### SwiftUI
```swift
import os.log

extension Log {
    static let ui = Logger(subsystem: "com.app", category: "UI")
}

// In view code
.onAppear {
    Log.ui.debug("Frame size: \(geometry.size.width)×\(geometry.size.height)")
}
```

### React
```javascript
// Component lifecycle
useEffect(() => {
  console.log('[Component] mounted', props)
  return () => console.log('[Component] unmounted')
}, [])

// State changes
useEffect(() => {
  console.log('[Component] state changed', state)
}, [state])
```

### Web
```javascript
// Layout metrics
const rect = element.getBoundingClientRect()
console.log('Position:', rect.x, rect.y)
console.log('Size:', rect.width, rect.height)

// Scroll position
console.log('Scroll:', window.scrollY)
```

## Common Issue Patterns

### Pattern: Text Truncation Not Working

**Symptoms**: Text extends beyond container, ignores lineLimit

**Diagnosis**:
```swift
// BROKEN
Text(longTitle)
    .lineLimit(2)
    .truncationMode(.tail)
```

**Root Cause**: Missing frame constraint

**Fix**:
```swift
// FIXED
Text(longTitle)
    .lineLimit(2)
    .truncationMode(.tail)
    .frame(maxWidth: .infinity, alignment: .leading)
```

### Pattern: Drag/Drop Not Working

**Symptoms**: .onDrag never fires, drag gesture doesn't trigger

**Diagnosis**:
```swift
// BROKEN
VStack {
    Text("Item")
}
.onDrag { ... }
```

**Root Cause**: No hit area defined

**Fix**:
```swift
// FIXED
VStack {
    Text("Item")
}
.contentShape(Rectangle())  // Define hit area
.onDrag { ... }
```

### Pattern: Overlay Blocking Interactions

**Symptoms**: Can't click elements underneath overlay

**Diagnosis**:
```swift
// BROKEN
.overlay {
    DebugView()
        .allowsHitTesting(true)  // Blocks touches
}
```

**Root Cause**: Overlay capturing all touches

**Fix**:
```swift
// FIXED
.background {  // Use background instead
    DebugView()
        .allowsHitTesting(false)  // Pass through
}
```

### Pattern: DatePicker Showing Wrong Format

**Symptoms**: Shows "1/1/2026" instead of "Jan 1"

**Diagnosis**:
```swift
// BROKEN
DatePicker("", selection: $date, displayedComponents: [.date])
    .datePickerStyle(.compact)
```

**Root Cause**: Compact style uses default formatting

**Fix**:
```swift
// FIXED
DatePicker("", selection: $date, displayedComponents: [.date])
    .datePickerStyle(.compact)
    .fixedSize()
    .overlay {
        Text(formatDateWithoutYear(date))
            .allowsHitTesting(false)
    }

private func formatDateWithoutYear(_ date: Date) -> String {
    let formatter = DateFormatter()
    formatter.dateFormat = "MMM d"
    return formatter.string(from: date)
}
```

## Output Template

Always provide structured output:

```markdown
## Debugging Summary

### Issue Identified
[Describe the visual problem from screenshot]

### Root Cause
File: `path/to/file.swift:line`
Problem: [Specific code causing issue]

### Fix Applied
[Code change with before/after]

### Why This Works
[Technical explanation of why fix resolves issue]

### Verification
- Build status: ✅ SUCCESS / ❌ FAILED
- Manual testing: [Checklist of what to verify]

### Pattern to Remember
[Document the pattern for future reference]
```

## Prevention Strategies

After fixing, document the pattern:

1. **Add to project conventions** if pattern is common
2. **Create reusable component** if repeated across views
3. **Update design system** if theme/styling issue
4. **Add linting rule** if preventable by automation

## When to Use Different Tools

**Read**: Always read files completely first (no limit/offset)
**Grep**: Find similar patterns in codebase for comparison
**Glob**: Locate view files when name is uncertain
**Edit**: Apply targeted fixes after diagnosis
**Bash**: Build verification and log capture

## Example Session

```
User: "The task titles are getting cut off"
[shares screenshot showing truncated text]

Agent:
1. ✅ Screenshot received - I see text truncated without "..."
2. 📖 Reading TaskRowView.swift completely
3. 🔍 Found: lineLimit(2) without frame constraint
4. 🔎 Searching for working example: DayPlannerTaskBox.swift
5. ⚖️ Comparison: Working code has .frame(maxWidth: .infinity)
6. 🔧 Applied fix: Added frame constraint
7. ✅ Build succeeded
8. 📋 Test checklist: Create long title, verify truncates with "..."
```

## Success Criteria

A successful frontend debug includes:

- ✅ Screenshot analyzed with specific observations
- ✅ Root cause identified to exact file:line
- ✅ Working example found and compared (if applicable)
- ✅ Minimal targeted fix applied
- ✅ Build verification passed
- ✅ Manual test checklist provided
- ✅ Pattern documented for prevention
