# Platform Features

> Brief: Five commands (search, update, compose, audit, hub) that transform claude-sync from a sync tool into a platform.
> Tags: platform, marketplace, search, audit
> Created: 2026-04-03
> Updated: 2026-04-03

## Motivation
With 500+ agents/skills across installations, users need discovery, quality assurance, and community sharing. These commands were added in PR #12 as a single batch (874 new lines, 0 existing lines modified).

## Overview

### search -- Full-Text TF-IDF Search

```bash
claude-sync search "swift concurrency"
```

Searches across all agents, skills, and rules using TF-IDF scoring. Returns ranked results with relevance scores. Implemented entirely in stdlib (no external search library).

### update -- Version Checking

```bash
claude-sync update --check
```

Compares installed agents/skills against source repos to find newer versions. Uses frontmatter `sync_hash` and `synced_at` stamps added during push (PR #9).

### compose -- Agent Composition

```bash
claude-sync compose agent1 agent2 --name combined-agent
```

Merges multiple agent definitions into a composite with deduplication. Useful for creating specialized agents that combine expertise from multiple sources.

### audit -- Security/Quality Scoring

```bash
claude-sync audit
```

Scans agents and skills for:
- Wildcard tool permissions (`allowed-tools: [*]`)
- Suspicious patterns (shell injection vectors, hardcoded paths)
- Quality metrics (description completeness, tag coverage)

Returns a per-file score and overall health grade.

### hub -- Community Discovery

```bash
claude-sync hub browse
```

Discovers and browses agent repos from a community index. Enables finding and installing agents/skills from other developers.

### install / uninstall -- Marketplace Operations (PR #10)

```bash
claude-sync install --from github.com/user/repo
claude-sync uninstall agent-name
```

Pull agents/skills/rules from any public GitHub repo with:
- Overlap detection (warns before replacing existing files)
- Provenance tracking (records where each file came from)
- Path traversal protection (security guard against `../` attacks)
- Safe filename validation

## Key Decisions

- **All stdlib** -- TF-IDF search implemented without numpy, scikit-learn, or whoosh
- **Non-destructive addition** -- 874 new lines, zero changes to existing code
- **Provenance tracking** -- Every installed file records its source for later uninstall

## See Also
- [[Skill Genome]]
- [[Architecture]]
