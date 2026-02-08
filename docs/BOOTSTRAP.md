# New Machine Bootstrap Guide

Set up Claude Code configuration on a new machine in 5 minutes.

## Prerequisites

- **git** -- For cloning the repo and version control
- **Python 3.8+** -- claude-sync uses stdlib only, no pip install needed
- **Claude Code** -- Must be installed and run at least once (creates `~/.claude/`)
- **Node.js** (optional) -- Required if your hooks use `npx tsx`

## Step 1: Clone the Repository

```bash
git clone <your-repo-url> ~/Documents/GitHub/claudeTools
cd ~/Documents/GitHub/claudeTools
```

## Step 2: Initialize claude-sync

```bash
python3 claude-sync.py init
```

This creates:
- `claude/` directory in the repo (if not present)
- `manifest.json` tracking file hashes
- A backup of your current `~/.claude/` config

## Step 3: Pull Configuration

If the repo already has config from another machine:

```bash
python3 claude-sync.py pull --yes
```

This copies all portable config (agents, skills, rules, hooks, scripts, CLAUDE.md) from `repo/claude/` into `~/.claude/`, and merges portable settings keys into your local `settings.json`.

## Step 4: Configure Machine-Specific Files

These files are intentionally NOT synced -- you need to set them up per machine.

### Environment Variables

```bash
cp templates/env.template ~/.claude/.env
# Edit ~/.claude/.env and add your API keys
```

### MCP Server Configuration

```bash
cp templates/mcp_config.template.json ~/.claude/mcp_config.json
# Edit ~/.claude/mcp_config.json with local paths and keys
```

### Permissions (settings.json)

The `env` and `permissions` keys in `~/.claude/settings.json` are machine-specific. After a pull, review and adjust:

```bash
# View current settings
cat ~/.claude/settings.json | python3 -m json.tool

# Add machine-specific keys as needed
# (env, permissions stay local and won't be overwritten by future pulls)
```

## Step 5: Run Health Checks

```bash
python3 claude-sync.py doctor
```

All 9 checks should pass:
- `git_repo` -- Git repository found
- `home_claude` -- `~/.claude` exists
- `repo_claude` -- `repo/claude` exists
- `manifest` -- `manifest.json` is valid
- `file_hashes` -- All file hashes match manifest
- `script_permissions` -- `.sh` and `.py` files are executable
- `git_clean` -- No uncommitted changes
- `settings_keys` -- Only portable keys in repo settings
- `no_excluded` -- No excluded paths leaked into repo

## Step 6: Optional Alias Setup

Add to your shell profile (`~/.zshrc` or `~/.bashrc`):

```bash
# claude-sync alias
alias csync="python3 ~/Documents/GitHub/claudeTools/claude-sync.py"

# Common workflows
alias csync-push="csync push --yes"
alias csync-pull="csync pull --yes"
alias csync-status="csync status"
```

Then reload:

```bash
source ~/.zshrc  # or source ~/.bashrc
```

## Day-to-Day Usage

### Check Status

```bash
python3 claude-sync.py status
# Shows changes in both directions (push and pull)
```

### Push Changes (after editing config locally)

```bash
python3 claude-sync.py push
# Scans for secrets, shows diff, asks confirmation
# Use --yes to skip confirmation, --force if secret scanner flags false positives
```

### Pull Changes (after pulling repo from another machine)

```bash
python3 claude-sync.py pull
# Shows diff, asks confirmation, creates backup before pulling
```

### View Differences

```bash
python3 claude-sync.py diff                  # Push direction (default)
python3 claude-sync.py diff --direction pull  # Pull direction
python3 claude-sync.py diff rules/hooks.md   # Specific file
```

### Backups

```bash
python3 claude-sync.py backup list           # List backups
python3 claude-sync.py backup create         # Manual backup
python3 claude-sync.py backup prune --keep 3 # Keep only 3 newest
python3 claude-sync.py restore               # Restore latest backup
python3 claude-sync.py restore 20260207      # Restore by name/partial match
```

## Troubleshooting

### "Not in a git repository"

Navigate to the claudeTools repo directory, or run `git init` if starting fresh.

### "~/.claude not found"

Run Claude Code at least once. It creates the `~/.claude/` directory on first launch.

### Secret scanner blocks push

The scanner may flag documentation that mentions API keys (not actual secrets). Review the findings, then use `--force` if they are false positives:

```bash
python3 claude-sync.py push --yes --force
```

### Script permissions wrong after pull

The pull command auto-sets +x on `.sh` and `.py` files. If permissions are still wrong:

```bash
find ~/.claude/hooks -name "*.sh" -exec chmod +x {} \;
find ~/.claude/scripts -name "*.py" -exec chmod +x {} \;
```
