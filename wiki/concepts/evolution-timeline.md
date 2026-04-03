# Evolution Timeline

> Brief: Full development history from initial commit to current state, tracking architectural decisions across 14 PRs.
> Tags: history, releases, decisions
> Created: 2026-04-03
> Updated: 2026-04-03

## Motivation
Understanding how claude-sync evolved helps inform future decisions and avoids re-litigating settled choices.

## Timeline

### Phase 1: Foundation (Initial Commit)
**Commit**: `f5f3d93` -- "Initial commit: claude-sync toolkit"

Core sync engine: init, push, pull, status, diff. Single-file design established from day one.

### Phase 2: Hardening
**Commit**: `f960364` -- "Harden CLI with three-way merge, automation, and ecosystem intelligence"

- Three-way merge using manifest as merge base
- Secret scanner (blocks push on detected credentials)
- `watch` command (poll + optional watchdog)
- `ecosystem` analysis (duplicates, stats, catalog)
- Git hook integration (`hooks install/uninstall`)

### Phase 3: Testing & Packaging
**Commit**: `e0e5e4f` -- "Add test suite, CI, packaging, and documentation"

- 204 unittest tests across 10 files
- CI matrix: Ubuntu + macOS, Python 3.9 + 3.12
- `pyproject.toml` for pip install
- README documentation

### Phase 4: Skill Genome
**Commit**: `2bb9427` -- "Add Skill Genome: dependency management"

- `requires:` declarations in SKILL.md frontmatter
- Atomized triggers (per-skill `triggers.json`)
- Dependency resolution, health checks, packaging

### Phase 5: Worksets (PR #2)
**Commit**: `a0c44a1` -- "Add worksets: activate agent/skill subsets per session"

- Vault mechanism with hardlinks
- Workset definitions (JSON, tag-based, extends)
- Affinity engine (learns per-project preferences)
- Auto-deactivate during sync

### Phase 6: MCP Server (PR #4)
**Commit**: `e885cad` -- "Add claude-sync-mcp"

- 821-line stdio MCP server
- 7 tools including `consult` (auto-route questions to relevant agents)
- Resources and prompts for Claude Desktop

### Phase 7: Bug Fix -- Three-Way Merge (PR #1)
**Commit**: `a2359ec` -- "Fix three-way merge skipping missing target files on pull"

New files from other machines weren't arriving because missing-in-target was treated as "skip" instead of "target unchanged."

### Phase 8: v1.3.0 Release (PRs #7, #8)
- Cowork support
- Capability manifest (`.claude-sync-capabilities.json`)
- Plugin sync
- Merged worksets + MCP + enrichment into release branch

### Phase 9: Versioning & Release (PR #9)
**Commit**: `761dbe4`

- Replace 8x `datetime.utcnow()` deprecation warnings
- Sync fingerprint for fast version comparison
- Frontmatter stamping (`sync_hash` + semver + `synced_at`)
- `version` command
- `release` command (tag, sha256, Homebrew formula update)

### Phase 10: Homebrew & Marketplace (PRs #10, #11)
**Commits**: `1784584`, `e398b31`

- Self-hosted Homebrew tap in repo
- `install` / `uninstall` commands for pulling from public GitHub repos
- Provenance tracking, path traversal protection
- `install.sh` one-liner setup script

### Phase 11: Platform Features (PR #12)
**Commit**: `cb3db89` -- "Add platform features: search, update, compose, audit, hub"

874 new lines, 0 existing code modified. Five commands transforming claude-sync into a platform.

### Phase 12: Section-Level CLAUDE.md Merge (PR #13)
**Commit**: `edf1a88`

- Parse CLAUDE.md into H1 sections, diff individually
- Health scoring (flags orphaned agent/skill references)
- Atomic writes (tempfile + os.replace)
- Code fence awareness

### Phase 13: Cleanup
**Commit**: `9a6843a` -- Secret scanner false-positive reduction (47 -> 0)
**Commit**: `ab62f70` -- Remove accidental claude-sync artifacts from source repo

## PR Summary

| PR | Title | Status |
|----|-------|--------|
| #1 | Fix three-way merge skipping missing files on pull | Merged |
| #2 | Add worksets | Merged |
| #3 | Add enrich-skills.py | Merged |
| #4 | Add claude-sync-mcp | Merged |
| #7 | v1.3.0: Worksets, MCP, Enrichment | Merged |
| #8 | v1.3.0: Cowork, capability manifest, plugin sync | Merged |
| #9 | Versioning, fingerprint, release command | Merged |
| #10 | Homebrew tap + skill marketplace | Merged |
| #11 | Fix Homebrew tap + install script | Merged |
| #12 | Platform features: search, update, compose, audit, hub | Merged |
| #13 | Section-level CLAUDE.md merge + portable settings sync | Merged |
| #14 | Bump version to 1.3.0 | Open |

## See Also
- [[Architecture]]
- [[Three-Way Merge]]
