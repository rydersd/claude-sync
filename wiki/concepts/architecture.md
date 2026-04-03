# Architecture

> Brief: Single-file Python CLI with 30 classes, 7800+ lines, zero external dependencies. All stdlib.
> Tags: architecture, design, python
> Created: 2026-04-03
> Updated: 2026-04-03

## Motivation
Understanding the codebase structure is essential for anyone contributing or extending claude-sync.

## Overview

claude-sync is a **single-file CLI** (`claude-sync.py`, ~7800 lines) that syncs `~/.claude/` configuration between machines via a git repo's `claude/` directory. It uses **only Python stdlib** (no pip dependencies), targeting Python 3.9+.

### File Layout

```
claude-sync.py              # The entire CLI (single file)
claude-sync-mcp.py          # MCP server for Claude Desktop (821 lines)
claude_sync/                # Thin pip-installable wrapper
  __init__.py               # Version string
  cli.py                    # Entry point delegates to claude-sync.py
scripts/enrich-skills.py    # Skill evolution utility
tests/                      # 204 unittest tests (10 test files)
dist/                       # Homebrew distribution package
Formula/claude-sync.rb      # Homebrew formula (self-hosted tap)
.github/workflows/          # CI (ci.yml) + release automation (release.yml)
claude-sync-app/            # Tauri desktop app (WIP)
```

### Class Hierarchy (30 classes)

The file is organized into sections by comment banners:

| Section | Classes | Purpose |
|---------|---------|---------|
| **Core MVP** | `PathResolver`, `FileHasher`, `FileChange`, `DiffResult`, `DiffEngine`, `Manifest`, `Output`, `SyncEngine` | Path resolution, hashing, diffing, manifest tracking, sync operations |
| **Security** | `SecretFinding`, `SecretScanner` | Pattern-based secret detection on push |
| **Backup/Merge** | `BackupManager`, `SettingsMerger`, `MarkdownSectionMerger` | Timestamped backups, settings.json partial sync, CLAUDE.md section merge |
| **Health** | `HealthCheck`, `Doctor` | Diagnostic checks (paths, permissions, manifest integrity) |
| **Automation** | `SyncEventHandler`, `FileWatcher`, `GitHookManager`, `SyncConfig` | Watch mode, git hook install, config |
| **Worksets** | `WorksetDefinition`, `WorksetState`, `WorksetEngine` | Vault, activation, affinity engine |
| **Genome** | `SkillDependencies`, `SkillGenome`, `DependencyNode`, `HealthIssue`, `SkillGenomeEngine` | Dependency declarations, resolution, packaging |
| **Ecosystem** | `SimilarityPair`, `EcosystemAnalyzer` | Duplicate detection, TF-IDF search, catalog |
| **CLI** | `ClaudeSync` | Argument parsing, command dispatch (27 `_cmd_*` methods) |

### Command Count

27 top-level commands via `_cmd_*` methods in the `ClaudeSync` class:
`init`, `status`, `push`, `pull`, `diff`, `resolve`, `history`, `doctor`, `backup`, `restore`, `watch`, `hooks`, `ecosystem`, `drift`, `tracker`, `pair`, `genome`, `workset`, `version`, `release`, `install`, `uninstall`, `search`, `update`, `compose`, `audit`, `hub`

### Key Design Decisions

1. **Single file** -- Simplifies distribution (just copy one file), no build step needed
2. **Zero dependencies** -- Works on any machine with Python 3.9+, no `pip install` required
3. **Manifest as merge base** -- Three-way merge uses `manifest.json` to track last-synced state
4. **Hardlinks for worksets** -- Zero disk overhead, ~50ms activation
5. **Section-level CLAUDE.md merge** -- Parses H1 sections, diffs individually, prevents data loss
6. **Portable settings extraction** -- Only syncs safe keys from settings.json

### Testing

- 204 tests across 10 test files using `unittest` (not pytest)
- CI matrix: Ubuntu + macOS, Python 3.9 + 3.12
- Test files mirror class structure: `test_sync_engine.py`, `test_secret_scanner.py`, etc.

### Distribution

- **Direct**: `python3 claude-sync.py <command>`
- **pip**: `pip install -e .` installs `claude-sync` command
- **Homebrew**: `brew tap rydersd/claude-sync` (self-hosted tap in repo)
- **Install script**: `install.sh` for one-liner setup

## See Also
- [[Three-Way Merge]]
- [[Secret Scanner]]
- [[Evolution Timeline]]
