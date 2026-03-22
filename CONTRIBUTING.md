# Contributing to claude-sync

Thanks for considering contributing! claude-sync is a single-file Python CLI tool with zero external dependencies, and we'd like to keep it that way.

## Development Setup

```bash
git clone https://github.com/rydersd/claudeTools.git
cd claudeTools
python3 claude-sync.py status    # verify it runs
```

No virtual environment or package install needed. Python 3.9+ and git are the only requirements.

## Architecture

The project follows a strict single-file-per-feature pattern:

| File | Purpose |
|------|---------|
| `claude-sync.py` | Core CLI: sync, worksets, genome, ecosystem |
| `claude-sync-mcp.py` | MCP server for Claude Desktop integration |
| `scripts/enrich-skills.py` | Skill evolution automation |

Each file is self-contained with zero external dependencies (Python stdlib only).

## Guidelines

### Code Style

- Pure Python stdlib — no `pip install` dependencies
- Type hints on function signatures
- Dataclasses for structured data
- `Path` objects (not string paths)
- Exit codes via `ExitCode` enum

### Adding a New Command

1. Add the argparse subcommand in `_build_parser()`
2. Add the handler to the `handlers` dict in `run()`
3. Implement `_cmd_<name>()` method on `ClaudeSync`
4. Add to the commands table in README.md

### Testing

```bash
cd tests/
python3 -m pytest
```

Tests use pytest with no external fixtures. Each test module covers one component (hasher, manifest, diff engine, etc.).

### Pull Requests

- One feature per PR
- Include test coverage for new functionality
- Update README.md if adding user-facing commands
- Update docs/CHANGELOG.md

## What We're Looking For

- Bug fixes with reproduction steps
- New ecosystem analysis capabilities
- MCP server enhancements
- Performance improvements for large agent/skill collections
- Cross-platform (Windows/Linux) compatibility fixes

## What We're Not Looking For

- External dependencies (PyYAML, click, rich, etc.)
- Breaking changes to the sync format or manifest schema
- Features that require a running service or daemon

## Questions?

Open an issue on GitHub.
