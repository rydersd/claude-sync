# Plugin Compatibility Notes

Analysis of current skill/agent format vs Anthropic's plugin format, with migration path.

## Current Format vs Plugin Format

### Skills (`.claude/skills/`)

**Current format**: Markdown files with optional YAML frontmatter.

```yaml
---
name: skill-name
description: Brief description
allowed-tools: [Bash, Read]
---
# Instructions here...
```

**Anthropic plugin format**: Similar markdown with standardized frontmatter fields including `name`, `description`, `version`, `author`, and `allowed-tools`.

**Gap**: 22 skills are missing the `name` field in their frontmatter. The content body is compatible, but metadata needs standardization.

### Agents (`.claude/agents/`)

**Current format**: Markdown files, most without frontmatter.

**Anthropic plugin format**: Expects YAML frontmatter with `name`, `description`, and optionally `model`, `tools`, `system-prompt`.

**Gap**: 4 agents have no frontmatter at all. Their content needs to be restructured with proper metadata headers.

## Audit Findings

### Skills Needing `name` Field (22)

Skills that have a SKILL.md or markdown definition but are missing the `name` field in frontmatter. These function correctly in claude-sync but would not be installable as plugins.

**Fix**: Add `name: <skill-name>` to the frontmatter of each skill file, matching the directory name.

### Agents Needing Frontmatter (4)

Agent files that are plain markdown without any YAML frontmatter block. They work as local agents but cannot be packaged as plugins.

**Fix**: Add a frontmatter block at the top:
```yaml
---
name: agent-name
description: What this agent does
---
```

## Natural Plugin Bundles (6)

Groups of related skills/agents that could be packaged together as a single plugin:

1. **Xcode Workflow** -- `xcode-build`, `xcode-test`, `xcode-launch`, `xcode-logs`, `xcode-clean`, `xcode-screenshot`, `xcode-workflow`
2. **Research & Search** -- `morph-search`, `github-search`, `perplexity-search`, `nia-docs`, `research-agent`
3. **Development Lifecycle** -- `test-driven-development`, `debug`, `frontend-debug`, `implement_task`, `implement_plan`
4. **Planning & Continuity** -- `create_plan`, `plan-agent`, `continuity_ledger`, `create_handoff`, `resume_handoff`
5. **Code Quality** -- `qlty-check`, `ast-grep-find`, `morph-apply`
6. **Session Management** -- `commit`, `describe_pr`, `compound-learnings`, `recall-reasoning`

## skill-rules.json

**Current**: `skill-rules.json` maps trigger patterns to skill invocations. This drives automatic skill selection based on user input.

**Plugin equivalent**: No direct equivalent in the plugin format. Plugins are discovered by name/description and invoked explicitly or via Claude's built-in matching.

**Migration path**: Trigger rules could be converted to enhanced `description` fields in plugin frontmatter, giving Claude enough context to select the right plugin. Alternatively, a custom hook could replicate the trigger-matching behavior.

## Migration Path

### Phase 1: Metadata Standardization (non-breaking)

1. Add `name` field to all 22 skills missing it
2. Add frontmatter blocks to all 4 agents missing them
3. Verify all files still work with claude-sync after changes

### Phase 2: Bundle Packaging (when plugin format stabilizes)

1. Group related skills into plugin bundles
2. Add `version`, `author` fields
3. Create plugin manifest files
4. Test installation via `claude plugins install`

### Phase 3: Trigger Migration

1. Convert `skill-rules.json` triggers to enriched descriptions
2. Or implement a `UserPromptSubmit` hook that replicates trigger matching
3. Test that automatic skill selection still works

## Compatibility Notes

- claude-sync syncs skills and agents as raw files, which is format-agnostic
- Adding plugin-compatible frontmatter does not break existing functionality
- The sync tool does not need changes to support either format
- Plugin bundles are a packaging concern, not a sync concern
