# claude-sync Architecture

## Overview

`claude-sync` is a single-file Python CLI tool (stdlib only, no dependencies) that synchronizes portable Claude Code configuration between machines via a git repository. It copies syncable files from `~/.claude/` to a repo's `claude/` directory, applying secret scanning, hash-based diffing, and settings key separation along the way.

## Directory Structure

```
claudeTools/                    # Git repository root
  claude-sync.py                # Single-file CLI tool (all logic here)
  manifest.json                 # File hash manifest (auto-generated on push)
  .gitignore                    # Excludes ephemeral/sensitive paths
  templates/                    # Setup templates for new machines
    env.template                # ~/.claude/.env skeleton
    mcp_config.template.json    # MCP server config skeleton
    settings.portable.json      # Portable settings keys reference
    plugins.txt                 # Plugin inventory
  docs/                         # Documentation
    ARCHITECTURE.md             # This file
    BOOTSTRAP.md                # New machine setup guide
    PLUGIN-COMPAT.md            # Plugin format compatibility notes
    CHANGELOG.md                # Release history
  claude/                       # Mirror of ~/.claude portable config (auto-populated)
    CLAUDE.md                   # Global instructions
    settings.json               # Portable keys only (hooks, statusLine, attribution)
    agents/                     # Agent definitions
    skills/                     # Skill definitions
    rules/                      # Rule files
    hooks/                      # Hook scripts
    scripts/                    # MCP and utility scripts
```

### What Syncs vs What Stays Local

| Syncs (portable)            | Never Syncs (machine-specific)       |
|-----------------------------|--------------------------------------|
| `CLAUDE.md`                 | `.env`                               |
| `agents/`                   | `mcp_config.json`                    |
| `skills/`                   | `session-env/`                       |
| `rules/`                    | `todos/`, `plans/`, `projects/`      |
| `hooks/`                    | `history.jsonl`, `stats-cache.json`  |
| `scripts/`                  | `cache/`, `state/`, `telemetry/`     |
| `settings.json` (portable)  | `downloads/`, `plugins/`, `debug/`   |

## Class Architecture

### Core (Phase 1)

- **PathResolver** -- Resolves `~/.claude` and `repo/claude` paths. Auto-detects git root by walking up from cwd. Provides bidirectional path conversion (home-to-relative, repo-to-relative, and back).

- **FileHasher** -- SHA-256 file hashing with 64KB chunks. Walks directory trees, applying exclusion rules (`EXCLUDE_PATHS`, `WALK_EXCLUDE_PATTERNS`) and syncability checks (`SYNC_PATHS`). Returns `{relative_path: hash}` dictionaries.

- **DiffEngine** -- Set-based comparison of two hash dictionaries. Produces a `DiffResult` containing lists of `FileChange` objects categorized as added, modified, or deleted. Supports both push and pull directions.

- **Manifest** -- Schema-versioned JSON file tracking the last-known state of `repo/claude`. Stores file hashes and push provenance (machine ID, hostname, platform, timestamp).

- **SyncEngine** -- Executes file copy/delete operations based on a `DiffResult`. Handles push (home to repo) and pull (repo to home) directions. Sets executable permissions on `.sh`/`.py` files during pull. Cleans up empty directories after deletions.

- **Output** -- TTY-aware colored console output with ANSI codes. Supports JSON mode (`--json`), verbose mode (`--verbose`), and quiet mode (`--quiet`). Provides semantic methods: `success()`, `warning()`, `error()`, `diff_line()`, `confirm()`.

### Safety (Phase 2)

- **SecretScanner** -- Pre-push scanning with 9 regex patterns: API keys (`sk-*`), Anthropic keys, Bearer tokens, private keys, password assignments, connection strings, AWS keys, GitHub tokens, and generic secrets. Masks matched text in output for safe display.

- **BackupManager** -- Creates timestamped backups of syncable files to `~/.claude-sync-backups/`. Supports listing, partial-name lookup, and retention-based pruning (default: keep 5).

- **SettingsMerger** -- Separates `settings.json` into portable keys (`hooks`, `statusLine`, `attribution`) and machine-specific keys (`env`, `permissions`). Push extracts portable only; pull deep-merges portable into existing local settings without overwriting machine-specific keys.

### Diagnostics (Phase 3)

- **Doctor** -- Runs 9 health checks: git repo exists, `~/.claude` exists, `repo/claude` exists, manifest is valid, file hashes match manifest, script permissions are correct, git working tree is clean, settings keys are portable-only, and no excluded paths leaked into repo.

### CLI (Phase 4)

- **ClaudeSync** -- Main application class. Builds argparse parser, dispatches to command handlers. Commands: `init`, `status`, `push`, `pull`, `diff`, `doctor`, `backup`, `restore`.

## Sync Flow

### Push (`~/.claude` -> `repo/claude`)

```
1. SecretScanner scans ~/.claude for secrets
   - If found and --force not set: exit 3
2. FileHasher walks both ~/.claude and repo/claude
3. DiffEngine compares hashes (direction=push)
4. User confirms changes (or --yes skips)
5. BackupManager creates pre-push backup of repo/claude
6. SyncEngine copies added/modified files, deletes removed files
7. SettingsMerger extracts portable keys from settings.json
8. Manifest updates file hashes and push provenance
9. Manifest saved to manifest.json
```

### Pull (`repo/claude` -> `~/.claude`)

```
1. FileHasher walks both ~/.claude and repo/claude
2. DiffEngine compares hashes (direction=pull)
3. User confirms changes (or --yes skips)
4. BackupManager creates pre-pull backup of ~/.claude
5. SyncEngine copies files, sets +x on .sh/.py files
6. SettingsMerger deep-merges portable keys into local settings
7. BackupManager prunes old backups (retention=5)
```

## Security Model

### Secret Scanning

Every push runs 9 regex patterns against all syncable files. Findings show masked text (e.g., `sk-abc...xyz`). Push is blocked (exit code 3) unless `--force` is specified.

### What Never Syncs

The following are excluded by design and never copied to the repo:

- `.env` -- Contains API keys and secrets
- `mcp_config.json` -- Contains local paths and API keys
- `session-env/`, `todos/`, `projects/` -- Session-specific state
- `history.jsonl`, `stats-cache.json` -- Usage data
- `cache/`, `state/`, `telemetry/`, `debug/`, `statsig/` -- Runtime data
- `downloads/`, `plugins/`, `shell-snapshots/`, `paste-cache/`, `file-history/` -- Ephemeral data

### .gitignore

The `.gitignore` file prevents ephemeral and sensitive data from being committed, both in the `claude/` sync directory and the `.claude/` project config directory.

### Settings Key Separation

`settings.json` is split on push:
- **Portable** (synced): `hooks`, `statusLine`, `attribution`
- **Machine-specific** (kept local): `env`, `permissions`

On pull, portable keys are deep-merged into local settings without overwriting machine-specific keys.

## Exit Codes

| Code | Constant         | Meaning                            |
|------|------------------|------------------------------------|
| 0    | `OK`             | Success                            |
| 1    | `ERROR`          | General error                      |
| 2    | `DIRTY`          | Changes exist (status/diff)        |
| 3    | `SECRETS`        | Secrets detected, push blocked     |
| 4    | `NOT_INITIALIZED`| Sync not initialized               |
