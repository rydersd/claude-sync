# Secret Scanner

> Brief: Regex-based secret detection that blocks push when API keys, tokens, or credentials are found. Includes false-positive mitigation for docs and placeholders.
> Tags: security, scanning, secrets
> Created: 2026-04-03
> Updated: 2026-04-03

## Motivation
Syncing `~/.claude/` to a git repo risks exposing API keys, tokens, and credentials. The scanner runs on every `push` and blocks if secrets are found.

## Overview

The `SecretScanner` class scans all files before push using regex patterns:

### Patterns Detected

- API keys (`sk-*`, `sk-ant-*`)
- Anthropic API keys
- Bearer tokens
- PEM private keys
- Password assignments
- Database connection strings
- AWS access keys (`AKIA*`)
- GitHub tokens (`ghp_*`, `gho_*`, etc.)
- Generic secret/token assignments

### False-Positive Mitigation

This was a major iteration area (PRs #9, commit 9a6843a):

1. **Placeholder detection** -- Strings containing `EXAMPLE`, `PLACEHOLDER`, `YOUR_`, `xxx`, `000` are skipped
2. **Code block awareness** -- In `.md` files, matches inside ``` fences are skipped
3. **Doc context patterns** -- Python `pass` statements, test pass/fail output, password policy discussions are skipped
4. **Shell variable references** -- `${VAR}` treated as placeholders, not secrets
5. **Path exclusions** -- `plugins/marketplaces/` and `/references/` directories are skipped entirely

Result: false positives went from 47 to 0 on a typical push.

### Behavior

- **Default**: Push blocked with exit code 3, masked findings shown
- **`--force`**: Override and push anyway (not recommended)
- **Masking**: Found secrets are shown truncated (e.g., `sk-abc...xyz`)

## Key Decisions

- **Block by default** -- Better to annoy with false positives than to leak a real key
- **Regex-based, not entropy-based** -- Predictable, debuggable, zero dependencies
- **Iterative false-positive reduction** -- Rather than loosening patterns, added context-aware skipping

## See Also
- [[Three-Way Merge]]
- [[Architecture]]
