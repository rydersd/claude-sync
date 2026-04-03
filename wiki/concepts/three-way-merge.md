# Three-Way Merge

> Brief: Conflict resolution using manifest.json as the merge base between home (~/.claude) and repo (claude/).
> Tags: sync, merge, conflict-resolution
> Created: 2026-04-03
> Updated: 2026-04-03

## Motivation
The core problem: two copies of config (home and repo) can diverge independently. A naive overwrite loses changes. Three-way merge detects which side(s) changed since the last sync.

## Overview

The `Manifest` class (`manifest.json`) stores the SHA-256 hash of every file at last sync time. On push or pull, the `DiffEngine` compares three states:

```
Home (source)     Manifest (base)     Repo (target)
     \                  |                  /
      \     SHA-256     |     SHA-256     /
       \   comparison   |   comparison   /
        \               |               /
         -------→ Merge Decision ←-------
```

### Resolution Logic

| Home vs Base | Repo vs Base | Action |
|-------------|-------------|--------|
| Same | Same | No change (in sync) |
| Changed | Same | Safe to push/pull (one side changed) |
| Same | Changed | Safe to push/pull (one side changed) |
| Changed | Changed | **Conflict** -- block and prompt user |

### Conflict Resolution

When both sides changed the same file:
- `--ours` -- keep the local (home) version
- `--theirs` -- keep the remote (repo) version
- `--force` -- overwrite regardless
- `claude-sync resolve` -- interactive review with unified diff

### Bug Fix: Missing Files (PR #1)

The original implementation skipped files that existed in the manifest but were missing from the target directory during pull. This meant new files added on another machine would never arrive. Fixed by treating "missing in target" as "target unchanged from base" -- allowing the pull to create the file.

### CLAUDE.md Section Merge (PR #13)

Plain three-way merge on CLAUDE.md was too coarse -- it treated the whole file as one unit. The `MarkdownSectionMerger` parses CLAUDE.md into H1 sections and diffs each independently:

1. Parse both versions into `[(heading, content), ...]`
2. Compare section-by-section with unified diff
3. Interactive review: accept incoming / keep local / skip per section
4. Health scoring: flags orphaned agent/skill references, oversized sections

Code fence awareness prevents headings inside ``` blocks from being treated as section boundaries.

## Key Decisions

- **Manifest is the source of truth for "last sync"** -- not git history, not file timestamps
- **Block on conflict by default** -- force the user to make an explicit choice
- **Backup before every push/pull** -- safety net regardless of merge outcome

## See Also
- [[Architecture]]
- [[Secret Scanner]]
