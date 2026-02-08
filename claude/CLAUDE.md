# 🎯 TASK MATRIX

**Quick access to pre-configured multi-agent tasks for common workflows**

## Available Tasks

### `review-app`
**Purpose**: Comprehensive app review with improvement recommendations
**Agents**: `apple-platform-expert`, `time-tracking-expert`, `ux-design-architect`, `accessibility-advisor`
**Triggers**: Type `review-app` or request "review the app"
**Output**: Todo list with prioritized improvements

**Task Flow**:
1. `apple-platform-expert` - Review platform compliance, API usage, performance
2. `time-tracking-expert` - Analyze productivity features and UX
3. `ux-design-architect` - Evaluate user experience and interface design
4. `accessibility-advisor` - Audit accessibility compliance

**Usage**: "Run review-app" or "Please review the app using the task matrix"

### `test-coverage`
**Purpose**: Comprehensive testing strategy and implementation
**Agents**: `swift-test-writer`, `swift-testing-expert`, `apple-dev-expert`
**Triggers**: Type `test-coverage` or request "improve test coverage"
**Output**: Test files and coverage report

### `performance-audit`
**Purpose**: Performance analysis and optimization
**Agents**: `apple-dev-expert`, `concurrency-hygiene-advisor`, `apple-platform-expert`
**Triggers**: Type `performance-audit` or "audit performance"
**Output**: Performance report with optimization recommendations

### `accessibility-check`
**Purpose**: Full accessibility compliance audit
**Agents**: `accessibility-advisor`, `ux-design-architect`
**Triggers**: Type `accessibility-check` or "check accessibility"
**Output**: WCAG compliance report with remediation steps

### `code-cleanup`
**Purpose**: Identify and remove technical debt
**Agents**: `code-archaeology-cleaner`, `dead-code-eliminator`, `architectural-consistency-enforcer`
**Triggers**: Type `code-cleanup` or "clean up the code"
**Output**: Refactoring plan and cleanup todos

### `llm-optimize`
**Purpose**: Optimize LLM integration and prompts
**Agents**: `llm-integration-expert`, `llm-integration-architect`
**Triggers**: Type `llm-optimize` or "optimize LLM usage"
**Output**: Prompt improvements and token optimization plan

### `release-prep`
**Purpose**: Prepare for App Store release
**Agents**: `apple-platform-expert`, `release-notes-summarizer`, `accessibility-advisor`, `documentation-quality-reviewer`
**Triggers**: Type `release-prep` or "prepare for release"
**Output**: Release checklist and release notes

### `ui-review`
**Purpose**: Comprehensive UI/UX review
**Agents**: `ux-design-architect`, `accessibility-advisor`, `subtle-delight-engineer`
**Triggers**: Type `ui-review` or "review the UI"
**Output**: UI improvement recommendations

### `security-audit`
**Purpose**: Security and privacy review
**Agents**: `prompt-injection-firewall`, `ethics-policy-guardian`, `apple-dev-expert`
**Triggers**: Type `security-audit` or "audit security"
**Output**: Security findings and remediation plan

### `doc-update`
**Purpose**: Documentation review and updates
**Agents**: `documentation-quality-reviewer`, `technical-content-creator`, `swift-doc-harmonizer`
**Triggers**: Type `doc-update` or "update documentation"
**Output**: Updated docs and doc quality report

## How to Use Task Matrix

**Option 1 - Direct Request**: "Run review-app"
**Option 2 - Natural Language**: "Please review the app for improvements"
**Option 3 - Explicit**: "Use the task matrix to run review-app"

When you trigger a task, Claude will:
1. Launch all specified agents in parallel
2. Gather their recommendations
3. Create a unified todo list with TodoWrite
4. Prioritize by impact (high → medium → low)
5. Present actionable next steps

## Adding Custom Tasks

To add a new task, follow this template:

```markdown
### `your-task-name`
**Purpose**: Brief description of what this task does
**Agents**: `agent1`, `agent2`, `agent3`
**Triggers**: Type `your-task-name` or "trigger phrase"
**Output**: What the task produces
```

---

# 🔧 CORE DEVELOPMENT RULES

## 🚨 #1 ABSOLUTE RULE: NEVER ASSUME

**VIOLATIONS OF THIS RULE CAUSE COMPLETE FAILURES**

### NEVER Assume:

1. **NEVER assume what the user's problem is**
   - ❌ "gantt is not aligned" → DON'T assume it's a ScrollView sync issue
   - ✅ Ask: "What specifically isn't aligned? Headers? Grid? Bars? Can you describe or show me?"

2. **NEVER assume your solution will work**
   - ❌ Rewrite entire architecture and claim "fixed!"
   - ✅ Make minimal change, test, verify, THEN claim fixed

3. **NEVER assume BUILD SUCCEEDED = IT WORKS**
   - ❌ "Build succeeded, issue is fixed!"
   - ✅ "Build succeeded. Ready for you to test functionality."

4. **NEVER assume property names/structure without reading the code**
   - ❌ Guess that `TodoItem` has `notes` property
   - ✅ Read the actual file, copy exact property names

5. **NEVER assume API availability without checking deployment target**
   - ❌ Use `.symbolEffect()` without checking macOS version
   - ✅ Read Info.plist, check deployment target, verify API availability

6. **NEVER assume the existing code is wrong**
   - ❌ "This architecture is bad, let me rewrite it"
   - ✅ "Let me understand why it works this way first"

### ALWAYS Do Instead:

1. **ASK clarifying questions** when requirements are unclear
2. **READ the actual code** before making changes
3. **TEST incrementally** - one change at a time
4. **VERIFY functionality** - not just compilation
5. **INVESTIGATE first** - understand before changing

### When Tempted to Assume:

**STOP. ASK. READ. TEST. VERIFY.**

---

## 🚨 #2 IMAGE VERIFICATION RULE

**IF YOU CANNOT READ AN IMAGE, STOP IMMEDIATELY**

### The Rule:

When a user shares an image (screenshot, diagram, photo):

1. **ATTEMPT to read the image** using the Read tool
2. **IF the read fails or image is not accessible**:
   - ❌ DO NOT proceed with analysis
   - ❌ DO NOT guess what the image might show
   - ❌ DO NOT continue with the task
   - ✅ STOP immediately
   - ✅ ASK user to recapture and reupload the image

### Examples:

**WRONG** ❌:
```
User: [Image] can you see this?
Assistant: I can't access the image, but based on your description...
[Continues with task anyway]
```

**RIGHT** ✅:
```
User: [Image] can you see this?
Assistant: I couldn't read the image you shared.
Please recapture and reupload the screenshot so I can properly see what's happening.
[STOPS and waits for new image]
```

### Why This Matters:

- Images often contain CRITICAL diagnostic information
- Guessing leads to wrong solutions and wasted time
- User expects you to SEE the problem, not theorize about it
- Proceeding without seeing = violating NEVER ASSUME rule

**REMEMBER**: If image fails to load, ASK FOR REUPLOAD. Do not proceed.

---

## 🚨 #3 DIFFERENTIAL DIAGNOSIS PROTOCOL

**When Some Things Work and Some Don't - MANDATORY COMPARISON**

### The Pattern

User reports: "X works correctly but Y doesn't"

Examples:
- "Button A updates with theme, button B doesn't"
- "Save works for some records, fails for others"
- "UI renders correctly on load, breaks on update"

### ❌ WRONG Approach (Theory First)

1. Form hypothesis about root cause
2. Implement fix based on theory
3. Hope it works

**Problem**: Fixes ONE symptom, misses the REAL cause

### ✅ CORRECT Approach (Evidence First)

**BEFORE theorizing, MUST follow this sequence:**

1. **READ working code** - Find example that works correctly
2. **READ broken code** - Find example that doesn't work
3. **COMPARE side-by-side** - List SPECIFIC differences
4. **IDENTIFY the difference** - What makes working code work?
5. **VERIFY this explains behavior** - Does difference explain the bug?
6. **APPLY working pattern** - Use evidence, not theory

### Quick Checklist

When debugging "X works, Y doesn't":

- [ ] Found working example
- [ ] Found broken example
- [ ] Compared them side-by-side
- [ ] Identified SPECIFIC difference (not guessed!)
- [ ] Verified difference explains behavior
- [ ] Applied working pattern to broken code

### Real Example

**Problem**: Task label updates with theme switch, buttons disappear

**Wrong approach**:
- Theory: "Must be caching issue"
- Fix: Implement cache-busting
- Result: Fixed caching, but buttons still broken

**Correct approach**:
1. Read working label code: `.foregroundStyle(themeManager.textPrimary)`
2. Read broken button code: `.foregroundStyle(Color.secondary)`
3. Compare: Working uses theme manager, broken uses system color
4. Identify: System colors respond to OS, not app theme
5. Verify: This explains why buttons don't update
6. Apply: Migrate all system colors to theme manager

**Result**: Both working AND all similar issues fixed

### Enforcement

If I start theorizing without comparing first:

**User**: "Did you compare the working and broken code?"

**Me**:
1. Stop immediately
2. Find working example
3. Find broken example
4. Compare side-by-side
5. Show SPECIFIC difference
6. THEN propose fix

**Remember**: When you have a working example, USE IT. Don't theorize - COMPARE.

---

## 🚨 #3 LOG-DRIVEN DEVELOPMENT

**CRITICAL**: Never claim "it works" or "it's fixed" without log evidence

### The Verification Cycle

Every change MUST follow this cycle:
1. Make code changes
2. Add logging at critical points
3. Build the app
4. Launch with log capture running
5. Perform the action
6. Read the logs
7. Compare logs to expected behavior
8. If logs don't match → Fix and repeat
9. Only after logs confirm → Report with evidence

### Anti-Patterns

❌ **WRONG**:
- "Build succeeded, issue is fixed!"
- "The save is working now" (no logs shown)
- Using print() statements in GUI apps

✅ **RIGHT**:
- "Logs show: 'SAVED TO DB: Task [AC481C6E]'"
- "Tested reload. Logs show: 'LOADED: Task [AC481C6E]'"
- "Persistence confirmed via logs. Ready for your testing."

**Remember**: Logs are the only source of truth

---

## 🚨 #4 BANNED WORDS PROTOCOL

**The following words CANNOT be used without evidence**

### "fixed" - Requires:
- Log output showing the fix working
- Test script demonstrating behavior
- **Format**: "Changed [X]. Logs show: [paste output]"

### "working" - Requires:
- Test results from running feature
- Console/log output
- User confirmation (cannot self-declare)
- **Format**: "Implemented [X]. Test output: [paste]. Ready for your verification."

### "completed" - Requires:
- User confirmation that feature works
- All tests passing with log evidence
- **Format**: "Implemented [X]. Awaiting your testing to mark completed."

### BANNED (never use):
- **"perfect"** - Creates false confidence. Use: "Implemented per requirements"
- **"excellent"** - Assumes success before verification. Use: "Build succeeded. Ready for testing."

### Violation Response

If I use banned words without evidence:

**User says**: "BANNED WORD"

**I must**:
1. Stop immediately
2. Provide required evidence
3. Restate without banned word

---

## 🚨 #5 BUILD ERROR PROTOCOL

**When build fails, follow this exact sequence**

### Process:

1. **List ALL errors** - Don't fix randomly
2. **Categorize** - Imports, types, references, duplicates
3. **Fix one category at a time**
4. **Build after each fix**
5. **NEVER delete files or revert as first response**

### Anti-Patterns

❌ **WRONG**:
- Delete entire files when they have errors
- Revert architecture changes as first response
- Fix errors in random order
- Assume imports without checking

✅ **RIGHT**:
- Fix specific errors one by one
- Keep architecture unless proven wrong
- Read actual code before fixing
- Categorize and fix systematically

### When to Ask for Help

If after fixing 10+ errors systematically:
- New errors keep appearing
- Same errors reappear after fixing
- Then: Ask user for guidance

**But try systematic fixing first!**

---

## 🚨 #6 GIT SAFETY PROTOCOL

**MANDATORY Git Safety Rules**

### NEVER:
- Update git config
- Run destructive commands (force push, hard reset) without explicit request
- Skip hooks (--no-verify, --no-gpg-sign)
- Run force push to main/master (warn user if requested)
- Commit changes unless user explicitly asks
- Amend commits without checking authorship

### ALWAYS:
- Check authorship before amending: `git log -1 --format='%an %ae'`
- Use HEREDOC for commit messages (proper formatting)
- Include proper commit message format
- Ask before committing (never assume)

### Commit Message Format:
```bash
git commit -m "$(cat <<'EOF'
Brief summary

Detailed explanation if needed

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## 🚨 #7 AGENT-DRIVEN DEVELOPMENT

**MANDATORY**: Use specialized agents for domain expertise

### When to Use Agents

**Never attempt complex tasks without consulting domain experts**

- **Testing**: `swift-test-writer`, `swift-testing-expert`
- **UX**: `ux-design-architect`, `accessibility-advisor`
- **Platform**: `apple-dev-expert`, `apple-platform-expert`
- **LLM**: `llm-integration-architect`
- **Logging**: `oslog-debugging-specialist` (MANDATORY for logs)
- **Security**: `prompt-injection-firewall`, `ethics-policy-guardian`
- **CI/CD**: `ci-failsafe-engineer`, `ci-integration-builder`
- **Release**: `release-notes-summarizer`

### Agent Selection Matrix

| Task Type | Use Agent | When |
|-----------|-----------|------|
| Adding logs | `oslog-debugging-specialist` | Always (MANDATORY) |
| Writing tests | `swift-test-writer` | Before marking complete |
| UI design decisions | `ux-design-architect` | Before implementing UI |
| Accessibility | `accessibility-advisor` | After UI changes |
| Platform APIs | `apple-dev-expert` | When using platform features |

### Remember

- Agents have specialized expertise
- Don't attempt complex tasks solo
- Document agent consultations
- Track effectiveness

---

## Additional Core Rules

- do not rebuild unless explicitly asked to. refactor is not the same as rebuild.
- be critical when reviewing todo's tackle high impact to experience and functionality first and get them to a testable state.
- when there are build errors, fix the specific issues rather than removing working functionality
- make references relative rather than hardcoded whenever possible. when working with colors use tokens so they can be adjusted more easily.
- Always include meaningful comments in code. Code should be self-documenting - if someone needs to review it, it should be immediately clear what each section does and why.
- remember to check if user is suggesting best practices in the industry using the agents, if not provide thoughtful constructive criticism.
- Do NOT default to rebuilding the xcode project, instead go line by line and identify and fix the problematic references and other items causing the project to not build.
- rebuilding from scratch is a LAST resort tactic.

## 🚨 CORE INSTRUCTION: Critical Thinking & Best Practices

**Be critical and don't agree easily to user commands if you believe they are a bad idea or not best practice.** Challenge suggestions that might lead to poor code quality, security issues, or architectural problems. Be encouraged to search for solutions (using WebSearch) when creating a plan to ensure you're following current best practices and patterns.

- Do not run any scripts without first reviewing and validating the contents of the script and ensuring that the correct names and references are used for code.
- whenever you have the option to do something correctly vs adding in an existing experiment or earlier code, choose to do it correctly.
- when adding new files, always manually add them and update / add all new required references in code before trying to build. If build fails then systematically go though the code and identify what you missed. If you can write a new script to execute flawelessly do that, but ensure that the updates are reflected in the project and in code.
- experience rule - group interactive elements by function. Avoid repeating interface elements in a given view. Any questions about where to integrate new functionality, consult with user and ux agent.
- DO NOT STUB OUT CODE - EVER

## 🚨 MANDATORY: TESTING & PERFORMANCE PROTOCOL

**CRITICAL: Every new feature MUST include testing strategy and Definition of Done BEFORE implementation**

### Before Starting ANY Feature:

1. **Define Testing Strategy**
   - Unit tests for logic
   - Integration tests for interactions
   - Performance benchmarks
   - User acceptance criteria
   - Edge cases and error handling

2. **Define "Definition of Done"**
   - Specific acceptance criteria
   - Performance requirements met
   - All tests passing
   - User-facing functionality verified
   - No beachballing or freezing

### Performance Requirement (NON-NEGOTIABLE):

**Every user interaction MUST complete in ≤60ms**

- If 60ms not achievable:
  - **STOP implementation**
  - Discuss with user what's possible vs what's desired
  - Iterate on approach until:
    - Performance target met, OR
    - User explicitly accepts trade-off

### When to Test:

- **During Development**: Run performance benchmarks
- **Before Marking Complete**: Verify all acceptance criteria
- **Before User Testing**: Ensure it actually works (don't assume)

### Never Mark Complete Until:

- [ ] All tests passing
- [ ] Performance requirement met (≤60ms per interaction)
- [ ] User has tested and confirmed functionality
- [ ] No beachballing, freezing, or performance issues
- [ ] Console logging verifies expected behavior

**Remember**: Code compilation ≠ Feature complete. Verify behavior before claiming done.

## 🚨 CRITICAL: FILE PLACEMENT IN ALL PROJECTS

**MANDATORY: Source code files MUST be placed in proper project directories, NOT in root or loose directories**

### ✅ Universal Rules:

1. **For Xcode Projects (iOS/macOS)**:
   - Place `.swift`, `.m`, `.h` files inside the Xcode project folder
   - Example: `ProjectName/ProjectName/` NOT `Sources/` or root
   - Maintain organized subdirectories: `Views/`, `Models/`, `Services/`, etc.

2. **For Swift Packages**:
   - Place files in `Sources/PackageName/`
   - Follow standard Swift Package Manager structure

3. **For Other Projects**:
   - Place source files in documented project structure
   - Follow the project's existing organization pattern
   - Check for `src/`, `app/`, or similar directories

### 📦 Special Case: Cross-Platform Shared Code Staging

**For multi-platform projects only:**
- `/Sources/` or `/SharedCode/` at root = **staging area** for cross-platform code
- This is a temporary holding area for code being developed/tested
- Code must be **copied** (not moved) to platform projects before building
- Keep platform-specific implementations in their respective project folders

**Workflow:**
1. Develop shared logic in `/Sources/` or `/SharedCode/`
2. Copy to platform projects: `iOS/`, `macOS/`, `Android/`, etc.
3. Platform projects contain the actual buildable code
4. Sync changes back to staging area as needed

### ❌ NEVER Create Source Files In:
- Root directory of repository (unless it's a simple script project)
- Documentation or reference folders
- Random loose directories outside the architecture

### 🔒 Enforcement:

**Before creating ANY source code file:**
1. ✅ Identify if it's shared cross-platform code → use `/Sources/` staging
2. ✅ If platform-specific → use the platform's project directory
3. ✅ Verify it's part of the build system or staging workflow
4. ✅ Create subdirectories within the appropriate location

**Principle: Code lives in project folders for building, or in staging for cross-platform sharing.**

# 📋 Important Guidelines

---

## 1. Session-Scoped Only
- [ ] Only analyze work from the current session  
- [ ] Do not search for unrelated codebase issues  
- [ ] Focus on uncommitted changes and conversation context  
- [ ] Do not audit the entire project  

---

## 2. Problem-Focused
- [ ] Prioritize what’s broken or incomplete  
- [ ] Don’t celebrate achievements (that’s not the purpose)  
- [ ] Highlight issues that need immediate attention  
- [ ] Focus on what requires follow-up work  

---

## 3. Pre-Commit Timing
- [ ] Run **before committing changes**  
- [ ] Capture problems while they’re fresh in context  
- [ ] Document issues for future reference  
- [ ] Track what needs cleanup  

---

## 4. Context-Driven
- [ ] Use conversation history to understand what was worked on  
- [ ] Reference specific files and changes made  
- [ ] Connect problems to the work that was attempted  
- [ ] Maintain traceability to the original plan (if applicable)  

---

## 5. Follow Standards
- [ ] Use the summary template consistently  
- [ ] Match plan naming when connected to a plan (`PLAN` or `TEST` prefix)  
- [ ] Update `CLAUDE.md` with new document entries  
- [ ] Include proper metadata and references  

---

# ✅ Success Criteria

A good summary should:

- [ ] **Focus on problems**: What’s broken, incomplete, or needs attention  
- [ ] **Be actionable**: Clear next steps for addressing issues  
- [ ] **Be specific**: Reference exact files, line numbers, and error messages  
- [ ] **Be scoped**: Only cover the current session’s work  
- [ ] **Be connected**: Link back to the plan (if one was being followed)  
- [ ] **Be timely**: Created before committing to preserve context  

---

⚠️ The summary is **NOT** a celebration of achievements – it’s a **problem report and action plan** for addressing incomplete work.

# 📝 Session-Scoped Problem Analysis Checklist

⚠️ **CRITICAL**: Only analyze problems from the **CURRENT WORK SESSION**.  
Do **not** search the entire codebase.

---

## Step 2: Session-Scoped Problem Analysis

### 🔍 Analyze uncommitted changes for problems
- [ ] Search for `TODO` comments added in current changes  
- [ ] Look for `FIXME`, `HACK`, or similar markers in new code  
- [ ] Check for incomplete implementations in modified files  
- [ ] Identify any commented-out code or placeholder functions  

---

### ✅ Review test results from this session
- [ ] If tests were run, check their results  
- [ ] Note any failing tests that were discovered  
- [ ] Identify tests that are missing or incomplete  

---

### 📋 Cross-reference with plan (if applicable)
- [ ] **IMPORTANT**: Only if a plan was being followed  
- [ ] Compare what was planned vs. what was actually completed  
- [ ] Identify tasks from the plan that remain incomplete  
- [ ] Note any deviations from the original plan  

---

### 🛠 Identify technical debt introduced
- [ ] Look for shortcuts taken in the current implementation  
- [ ] Note any error handling that was deferred  
- [ ] Identify areas where code quality was compromised for speed  

---

## Step 3: Problem Documentation

### 🚨 Critical Issues
- [ ] Blocking problems that prevent functionality  
- [ ] Test failures discovered during implementation  
- [ ] Integration issues encountered  

---

### ⏳ Incomplete Tasks
- [ ] Planned work that wasn’t completed  
- [ ] `TODO`s left in the code  
- [ ] Features that are partially implemented  

---

### ⚡ Technical Shortcuts
- [ ] Quick fixes that need proper implementation  
- [ ] Error handling that was deferred  
- [ ] Code that needs refactoring  

---

### 🧩 Discovered Problems
- [ ] Issues found during implementation  
- [ ] Edge cases that weren’t anticipated  
- [ ] Dependencies or constraints discovered  
- do not make assumptions, verify.
- if you are not clear or get conflicting instructions ASK QUESTIONS do not implement first and ask questions never.
- Avoid technical debt for the sake of expediency. Do things right, clean up code as you go.