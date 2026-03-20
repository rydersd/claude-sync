# claude-sync

Sync your Claude Code configuration between machines using git.

Claude Code stores its configuration in `~/.claude/` -- rules, agents, hooks, skills, scripts, and `CLAUDE.md`. That directory is local to each machine. **claude-sync** copies the portable parts into a git repo's `claude/` directory so you can push, pull, and share your setup across laptops, desktops, and CI environments.

## Why

- Your `~/.claude/` config is machine-local. When you switch machines, you start from scratch.
- Manually copying files is error-prone and forgets edge cases (permissions, settings merging, secrets).
- claude-sync handles the diff, the merge, the secret scanning, and the backup -- so you just `push` and `pull`.

## Quick Start

```bash
# 1. Navigate to your git repo
cd ~/projects/my-repo

# 2. Initialize (creates repo/claude/ directory + manifest)
python claude-sync.py init

# 3. Push your local config into the repo
python claude-sync.py push

# 4. Commit and share via git
git add claude/ manifest.json
git commit -m "Sync claude config"
git push

# 5. On another machine, pull config from the repo
git pull
python claude-sync.py pull
```

## Installation

### Homebrew (recommended)

```bash
brew tap rydersd/tools
brew install claude-sync
```

Updates:

```bash
brew update && brew upgrade claude-sync
```

### Direct (no install needed)

```bash
python3 claude-sync.py <command>
```

The script is a single file with zero external dependencies. Python 3.9+ and git are the only requirements.

### As a package (pip)

```bash
pip install -e .
claude-sync <command>
```

This installs the `claude-sync` command globally via the `pyproject.toml` entry point.

## Commands

| Command | Description |
|---------|-------------|
| `init` | Initialize sync in the current git repo. Creates `claude/` and `manifest.json`. |
| `status` | Show sync status: what's changed, what's in sync, what's excluded. |
| `push` | Copy `~/.claude/` portable files into `repo/claude/`. Scans for secrets first. |
| `pull` | Copy `repo/claude/` files back into `~/.claude/`. Creates a safety backup first. |
| `diff` | Show file-level differences between local and repo. |
| `resolve` | Show and resolve sync conflicts (three-way merge). |
| `history` | Show per-file sync history across machines. |
| `doctor` | Run health checks: paths, permissions, manifest integrity, git state. |
| `backup` | Manage timestamped backups. Subcommands: `create`, `list`, `prune`. |
| `restore` | Restore `~/.claude/` from a previous backup. |
| `watch` | Poll for changes and auto-sync (configurable interval). |
| `hooks` | Install/uninstall git hooks for auto-sync on pull/push. |
| `ecosystem` | Analyze agents/skills: `duplicates`, `related`, `catalog`, `stats`, `stale`, `timeline`. |
| `genome` | Skill dependency management: `scan`, `health`, `graph`, `install`, `extract-triggers`, `assemble-triggers`, `package`. |
| `drift` | Compare local state against known machine versions. |

### Global Flags

| Flag | Description |
|------|-------------|
| `--json` | Machine-readable JSON output |
| `--verbose`, `-v` | Verbose output |
| `--quiet`, `-q` | Quiet output |

### Command-Specific Flags

```
init      --force              Force re-initialization
push      --dry-run            Show what would change without writing
          --yes, -y            Skip confirmation prompt
          --force              Push even if secrets or conflicts detected
          --ours               Resolve conflicts with local (home) version
          --theirs             Resolve conflicts with remote (repo) version
pull      --dry-run            Show what would change without writing
          --yes, -y            Skip confirmation prompt
          --force              Pull even if conflicts detected
          --ours               Resolve conflicts with local (home) version
          --theirs             Resolve conflicts with remote (repo) version
resolve   --ours               Resolve all conflicts with local version
          --theirs             Resolve all conflicts with remote version
history   [file]               Show history for a specific file
diff      --direction push|pull  Direction to diff (default: push)
          [file]               Diff a specific file
watch     --interval N         Poll interval in seconds (default: 30)
hooks     install              Install post-merge and pre-push hooks
          uninstall            Remove claude-sync hooks
ecosystem duplicates [--threshold N]  Find similar agents/skills (default: 0.6)
          related <file>       Find files related to a given file
          catalog              Categorized listing of all agents/skills
          stats                Ecosystem size and category breakdown
          stale [--days N]     Find files not synced in N days (default: 90)
          timeline [--since DATE]  Evolution from git history
          prune [--dry-run]    Remove stale files
          archive <file>       Move a file to repo archive
backup    prune --keep N       Number of backups to retain (default: 5)
restore   [name]               Backup name (latest if omitted)
          --dry-run            Show what would change without writing
          --yes, -y            Skip confirmation prompt
genome    scan                 Show all skills with dependency declarations
          health               Check for missing deps, broken refs, cycles
          graph [--skill NAME] Visualize dependency tree (tree/flat/dot)
          install <skill>      Install skill with full dependency resolution
          extract-triggers     Split skill-rules.json into per-skill files
          assemble-triggers    Rebuild skill-rules.json from per-skill triggers
          package <skill>      Export skill + all deps as tar.gz
```

## Skill Genome

Skill Genome adds dependency management to the skill ecosystem -- think npm for Claude Code skills.

### The Problem

Skills have invisible dependencies. `figma-to-code` needs `design-sync`, which needs `design-tokens`, plus specific agents and MCP servers. Installing a skill means knowing this invisible tree. And the monolithic `skill-rules.json` (65KB+) causes merge conflicts whenever two machines edit different skills.

### How It Works

Skills declare dependencies in their SKILL.md frontmatter:

```yaml
---
name: figma-to-code
description: Deterministic Figma to SwiftUI generation
version: 1.0.0
requires:
  skills: [design-sync, design-tokens]
  agents: [figma-dev, apple-dev-expert]
  mcp-servers: [claude_ai_Figma, ClaudeToFigma]
  rules: [mainactor-safety]
---
```

Skills without `requires:` work exactly as before -- fully backward-compatible.

### Atomized Triggers

Instead of one monolithic `skill-rules.json`, each skill owns its own `triggers.json`:

```
skills/figma-to-code/
  SKILL.md          <- definition
  triggers.json     <- just THIS skill's trigger config
```

`skill-rules.json` becomes a derived artifact, auto-assembled on push/pull. Two machines editing different skills = no merge conflict.

**One-time migration:**

```bash
claude-sync genome extract-triggers    # splits monolith into per-skill files
claude-sync genome assemble-triggers   # verify: rebuilds skill-rules.json
```

### Commands

```bash
# See what you have
claude-sync genome scan                        # list all skills + deps
claude-sync genome health                      # check for missing deps

# Visualize
claude-sync genome graph --skill figma-to-code # dependency tree
claude-sync genome graph --skill figma-to-code --format dot  # graphviz

# Install with dependency resolution
claude-sync genome install figma-to-code       # installs skill + all deps

# Share
claude-sync genome package figma-to-code       # export as tar.gz
```

### Install Flow

```
claude-sync genome install figma-to-code

  Dependency tree:
    figma-to-code v1.0.0
      design-sync v1.0.0
        design-tokens v1.0.0
      figma-dev (agent)
      apple-dev-expert (agent)
      mainactor-safety (rule)
      claude_ai_Figma (mcp)
      ClaudeToFigma (mcp)

  Will install 3 skill(s): design-tokens, design-sync, figma-to-code
  Agents: figma-dev, apple-dev-expert
  Rules: mainactor-safety
  MCP servers: claude_ai_Figma, ClaudeToFigma

  Proceed with install? [y/N]
```

## Sync Flow

```
  ~/.claude/                        repo/claude/
  (your machine)                    (git-tracked)

  CLAUDE.md         ──push──>       CLAUDE.md
  agents/           ──push──>       agents/
  skills/           ──push──>       skills/
  rules/            ──push──>       rules/
  hooks/            ──push──>       hooks/
  scripts/          ──push──>       scripts/
  settings.json*    ──push──>       settings.json*

                    <──pull──
                    (same paths, reverse direction)

  * settings.json is partially synced (see below)
```

## What Syncs vs What Doesn't

### Synced (SYNC_PATHS)

| Path | Contents |
|------|----------|
| `CLAUDE.md` | Your project/global instructions |
| `agents/` | Agent definitions |
| `skills/` | Skill definitions |
| `rules/` | Rule files |
| `hooks/` | Hook scripts (shell + TypeScript) |
| `scripts/` | MCP and utility scripts |

### Never Synced (EXCLUDE_PATHS)

These stay local to each machine and are never copied:

| Path | Why excluded |
|------|--------------|
| `.env` | Environment secrets |
| `mcp_config.json` | Machine-specific MCP server paths |
| `session-env/` | Ephemeral session data |
| `todos/` | Session-scoped todo state |
| `projects/` | Project-specific caches |
| `history.jsonl` | Conversation history |
| `stats-cache.json` | Local statistics |
| `telemetry/` | Telemetry data |
| `cache/` | Temporary caches |
| `state/` | Runtime state |
| `plans/` | Session plans |
| `downloads/` | Downloaded files |
| `plugins/` | Local plugins |
| `shell-snapshots/` | Shell state |
| `paste-cache/` | Clipboard cache |
| `file-history/` | File access history |
| `debug/` | Debug logs |
| `statsig/` | Feature flag state |

## Settings.json Handling

`settings.json` gets special treatment. It contains both portable keys (should sync) and machine-specific keys (should not).

**Portable keys** (synced):
- `hooks` -- hook registrations
- `statusLine` -- status line configuration
- `attribution` -- attribution settings

**Machine-specific keys** (never synced):
- `env` -- environment variables (may contain paths or secrets)
- `permissions` -- machine-specific permission grants

During **push**, only portable keys are extracted from `~/.claude/settings.json` and written to `repo/claude/settings.json`.

During **pull**, portable keys from the repo are merged into the local `~/.claude/settings.json` without touching machine-specific keys.

## Security

### Secret Scanning

Every `push` scans all files for potential secrets before copying. If a secret is detected, the push is blocked (exit code 3) unless `--force` is used.

Patterns detected:
- API keys (`sk-*`)
- Anthropic API keys
- Bearer tokens
- Private keys (PEM)
- Password assignments
- Database connection strings
- AWS access keys
- GitHub tokens
- Generic secret/token assignments

Matched text is masked in output (e.g., `sk-abc...xyz`).

### Backup System

- `push` and `pull` both create automatic safety backups before modifying files
- `restore` creates a pre-restore backup before overwriting
- Backups are stored in `~/.claude-sync-backups/` with timestamps
- Default retention: 5 backups (configurable via `backup prune --keep N`)

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Error |
| 2 | Dirty (changes exist) |
| 3 | Secrets detected |
| 4 | Not initialized |

## Requirements

- **Python**: 3.9+
- **git**: any recent version
- **Dependencies**: none (stdlib only)

## Project Structure

```
claude-sync.py          # Single-file CLI (backward-compatible entry point)
claude_sync/            # Package wrapper for pip install
  __init__.py           # Version string
  cli.py                # Entry point that delegates to claude-sync.py
pyproject.toml          # PEP 621 package metadata
```

## FAQ

**Q: Do I need to install anything?**
No. `python claude-sync.py <command>` works out of the box with Python 3.9+. The `pip install` path is optional, for people who want a `claude-sync` command on their PATH.

**Q: What happens if I push and secrets are found?**
The push is blocked with exit code 3. You'll see which files and line numbers triggered the detection. Use `--force` to override (not recommended).

**Q: Does pull overwrite my local settings.json?**
Only the portable keys (`hooks`, `statusLine`, `attribution`). Machine-specific keys like `env` and `permissions` are preserved.

**Q: Where are backups stored?**
`~/.claude-sync-backups/`. Each backup is a timestamped directory containing a full snapshot of your `~/.claude/` syncable files.

**Q: Can I use this with multiple repos?**
Yes. Each repo gets its own `claude/` directory and `manifest.json`. Your `~/.claude/` is the single source; you push to whichever repo you're in.

**Q: What if push and pull conflict?**
claude-sync uses three-way merge with the manifest as the merge base. If both sides changed the same file since the last sync, the operation is blocked with a conflict warning. Use `claude-sync resolve` to inspect conflicts, then resolve with `--ours` (keep local), `--theirs` (keep remote), or `--force` (overwrite). The automatic backup means you can always `restore` if you choose wrong.

**Q: Does this work on Linux/Windows?**
It's tested on macOS and should work on Linux. Windows support is untested but the code uses `pathlib` throughout, so it may work with minor adjustments.
