---
description: Sync Claude Code configuration between machines via claude-sync CLI
allowed-tools: [Bash, Read]
---

# Claude Sync

Sync your `~/.claude/` configuration directory between machines using the `claude-sync` CLI tool. Wraps `claude-sync.py` subcommands with structured output and user confirmation for destructive operations.

## When to Use

- Check sync status between local and remote configs
- Push local configuration changes to the sync repo
- Pull remote configuration changes to this machine
- Back up current configuration before making changes
- Restore configuration from a backup
- Diagnose sync issues with the doctor command
- View diffs between local and remote state
- Initialize sync on a new machine

## Script Location

```
~/Documents/GitHub/claudeTools/claude-sync.py
```

If the script is not found at this path, check:
1. Has the repo been cloned? `ls ~/Documents/GitHub/claudeTools/`
2. Is the script executable? `chmod +x ~/Documents/GitHub/claudeTools/claude-sync.py`
3. Are Python dependencies installed? `pip install -r ~/Documents/GitHub/claudeTools/requirements.txt`

## Subcommands

### status -- Check Sync State

Show the current sync status: what's changed locally, what's changed remotely, and what's in conflict.

```bash
python3 ~/Documents/GitHub/claudeTools/claude-sync.py status
```

**Output format to present**:
```
SYNC STATUS

Local changes (not pushed):
  modified: rules/my-rule.md
  added:    skills/new-skill/SKILL.md

Remote changes (not pulled):
  modified: settings.json

Conflicts: none

Last sync: 2026-02-07 14:32:15
```

### push -- Push Local Changes

Push local configuration changes to the sync repository. **Requires user confirmation** unless `--yes` is passed.

```bash
# Show what will be pushed (dry run)
python3 ~/Documents/GitHub/claudeTools/claude-sync.py push --dry-run

# Push with confirmation prompt
python3 ~/Documents/GitHub/claudeTools/claude-sync.py push

# Push without confirmation (use when user has already approved)
python3 ~/Documents/GitHub/claudeTools/claude-sync.py push --yes
```

**Confirmation flow**:
1. Run `push --dry-run` first
2. Present the list of changes to the user
3. Ask: "These changes will be pushed to the sync repo. Proceed? (y/n)"
4. On confirmation, run `push --yes`
5. On rejection, report "Push cancelled."

### pull -- Pull Remote Changes

Pull remote configuration changes to the local machine. **Requires user confirmation** unless `--yes` is passed.

```bash
# Show what will be pulled (dry run)
python3 ~/Documents/GitHub/claudeTools/claude-sync.py pull --dry-run

# Pull with confirmation prompt
python3 ~/Documents/GitHub/claudeTools/claude-sync.py pull

# Pull without confirmation (use when user has already approved)
python3 ~/Documents/GitHub/claudeTools/claude-sync.py pull --yes
```

**Confirmation flow**:
1. Run `pull --dry-run` first
2. Present the list of incoming changes to the user
3. Ask: "These remote changes will be applied locally. Proceed? (y/n)"
4. On confirmation, run `pull --yes`
5. On rejection, report "Pull cancelled."

### backup -- Create Configuration Backup

Create a timestamped backup of the current `~/.claude/` configuration.

```bash
# Create backup with auto-generated name
python3 ~/Documents/GitHub/claudeTools/claude-sync.py backup

# Create backup with custom label
python3 ~/Documents/GitHub/claudeTools/claude-sync.py backup --label "before-refactor"
```

**Output format**:
```
BACKUP CREATED

Location: ~/.claude-backups/2026-02-07T14-32-15_before-refactor/
Files: 147
Size: 2.3 MB

Restore with: /sync restore --from 2026-02-07T14-32-15_before-refactor
```

### restore -- Restore Configuration from Backup

Restore configuration from a previous backup. **Requires user confirmation**.

```bash
# List available backups
python3 ~/Documents/GitHub/claudeTools/claude-sync.py restore --list

# Restore specific backup (dry run)
python3 ~/Documents/GitHub/claudeTools/claude-sync.py restore --from <backup-name> --dry-run

# Restore specific backup
python3 ~/Documents/GitHub/claudeTools/claude-sync.py restore --from <backup-name> --yes
```

**Confirmation flow**:
1. If no `--from` specified, run `restore --list` and present options
2. Run `restore --from <name> --dry-run` to show what will change
3. Ask: "This will overwrite your current configuration with the backup. Proceed? (y/n)"
4. On confirmation, run `restore --from <name> --yes`
5. On rejection, report "Restore cancelled."

### doctor -- Diagnose Sync Issues

Run diagnostic checks on the sync setup and report problems.

```bash
python3 ~/Documents/GitHub/claudeTools/claude-sync.py doctor
```

**Output format**:
```
SYNC DOCTOR

[PASS] Git repository configured
[PASS] Remote origin accessible
[WARN] 3 files not tracked by sync
[FAIL] Encryption key not found

Recommendations:
1. Run 'claude-sync init --encryption' to set up file encryption
2. Review untracked files: .claude/cache/, .claude/tmp/
```

### diff -- View Differences

Show detailed diffs between local and remote state.

```bash
# Diff all changed files
python3 ~/Documents/GitHub/claudeTools/claude-sync.py diff

# Diff specific file
python3 ~/Documents/GitHub/claudeTools/claude-sync.py diff --path rules/my-rule.md
```

Present diff output in a readable format with file paths and change summaries.

### init -- Initialize Sync

Set up sync on a new machine or reinitialize an existing setup.

```bash
# Interactive init (asks questions)
python3 ~/Documents/GitHub/claudeTools/claude-sync.py init

# Init with remote repo URL
python3 ~/Documents/GitHub/claudeTools/claude-sync.py init --remote git@github.com:user/claude-config.git

# Init with encryption enabled
python3 ~/Documents/GitHub/claudeTools/claude-sync.py init --encryption
```

**Init flow**:
1. Check if already initialized
2. If not, guide user through setup questions
3. Present the configuration choices before applying
4. Run init with the chosen options

## Invocation Patterns

When the user types `/sync` with no arguments, show a brief status and available subcommands:

```
CLAUDE SYNC

Status: 2 local changes, 0 remote changes, 0 conflicts
Last sync: 5 minutes ago

Available commands:
  /sync status   -- Detailed sync status
  /sync push     -- Push local changes
  /sync pull     -- Pull remote changes
  /sync backup   -- Create backup
  /sync restore  -- Restore from backup
  /sync doctor   -- Diagnose issues
  /sync diff     -- View differences
  /sync init     -- Initialize sync
```

When invoked with a subcommand (e.g., `/sync push`), run that subcommand directly.

## Error Handling

| Error | Cause | Resolution |
|-------|-------|------------|
| `claude-sync.py not found` | Script not installed | Clone claudeTools repo or check path |
| `Not initialized` | Sync not set up on this machine | Run `/sync init` |
| `Remote unreachable` | Network or auth issue | Check git remote, SSH keys, network |
| `Merge conflict` | Both local and remote changed same file | Show conflict details, suggest manual resolution |
| `Encryption key missing` | Key not configured on this machine | Run `/sync init --encryption` or import key |
| `Python not found` | Python 3 not installed | Install Python 3.8+ |

If the script exits with a non-zero code, capture stderr and present it to the user with suggested next steps.

## Safety Rules

1. **Never auto-push or auto-pull** without user confirmation
2. **Always dry-run first** for push, pull, and restore operations
3. **Suggest backup before restore** if no recent backup exists
4. **Never pass `--yes`** unless the user has explicitly confirmed in the current interaction
5. **Warn before overwriting** local changes during pull

## Examples

**Example 1: Quick status check**
```
User: "/sync"
Assistant: *Runs claude-sync.py status*

CLAUDE SYNC

Status: 1 local change, 0 remote changes
Last sync: 2 hours ago

Modified locally:
  rules/my-custom-rule.md

Run /sync push to push changes, or /sync diff to see details.
```

**Example 2: Push with confirmation**
```
User: "/sync push"
Assistant: *Runs push --dry-run*

Changes to push:
  modified: rules/my-custom-rule.md (12 lines changed)
  added:    skills/sync/SKILL.md (new file)

Push these 2 changes to the sync repo? (y/n)

User: "y"
Assistant: *Runs push --yes*

Pushed 2 changes to sync repo.
Last sync updated: 2026-02-07 15:45:22
```

**Example 3: Doctor diagnosis**
```
User: "/sync doctor"
Assistant: *Runs doctor*

SYNC DOCTOR

[PASS] Python 3.12 found
[PASS] Git repository initialized
[PASS] Remote origin: git@github.com:user/claude-config.git
[PASS] SSH authentication working
[WARN] 5 files in .claude/cache/ not tracked (expected - cache is excluded)
[PASS] No conflicts detected
[PASS] Last sync: 2 hours ago

All checks passed. Sync is healthy.
```

## Success Criteria

- Command executed with correct arguments
- Output parsed and presented in structured format
- Destructive operations confirmed before execution
- Errors caught and presented with remediation steps
- Dry-run shown before actual changes for push/pull/restore
