# Worksets

> Brief: Vault + hardlink mechanism for activating subsets of agents/skills per session. Zero disk overhead, ~50ms.
> Tags: worksets, performance, agents, skills
> Created: 2026-04-03
> Updated: 2026-04-03

## Motivation
A typical Claude Code setup has 179+ agents and skills. Loading all of them adds latency and noise. Worksets let you activate only the 45 you need for a given project type (e.g., Xcode dev, web dev, 3D art).

## Overview

### Mechanism

1. **Vault** (`~/.claude/.workset-vault/`) -- One-time `workset init` copies all agents/skills here as the canonical full set
2. **Activation** -- `workset activate <name>` clears `~/.claude/agents/` and `~/.claude/skills/`, then creates hardlinks from the vault for only the workset's files
3. **Deactivation** -- `workset deactivate` restores hardlinks for the full set

Hardlinks mean zero additional disk usage and near-instant activation.

### Workset Definition

```json
{
  "name": "dev-xcode",
  "description": "Apple platform development",
  "tags": ["Dev"],
  "agents": ["apple-dev-expert", "swift-test-writer"],
  "skills": ["visual-explainer"],
  "exclude_agents": [],
  "exclude_skills": [],
  "extends": ["dev-core"]
}
```

**Resolution order**: extends -> tags -> explicit agents/skills -> dependency resolution (genome) -> excludes

### Affinity Engine

Every `workset activate` records: git remote URL, detected languages, workset name. Over time, `workset suggest` learns per-project preferences:

```
cd ~/projects/my-ios-app
claude-sync workset suggest
# -> Suggested: dev-xcode (100% confidence, used 12/12 times)
```

`--auto` flag auto-activates if confidence > 80%.

### Sync Safety

When a workset is active during `push`/`pull`:
1. Auto-deactivate (restore full set)
2. Perform sync on the full set
3. Re-activate the workset

This ensures sync always sees all files. Workset definitions (`~/.claude/worksets/*.json`) are synced. Vault and activation state are machine-local.

## Key Decisions

- **Hardlinks over symlinks** -- Hardlinks are invisible to tools that check `os.path.isfile()`. Symlinks would require special handling.
- **Vault as separate directory** -- Keeps the full set intact even when a subset is active
- **Machine-local state** -- `_state.json` and `_affinity.json` never sync (machine-specific paths, different project layouts)

## See Also
- [[Skill Genome]]
- [[Architecture]]
