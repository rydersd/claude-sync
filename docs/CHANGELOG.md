# Changelog

## v1.3.0 (2026-03-22)

### Added
- **MCP Server** (`claude-sync-mcp.py`): Bridges agent/skill ecosystem to Claude Desktop
  - 7 tools: `search_agents`, `get_agent`, `get_skill`, `list_worksets`, `activate_workset`, `suggest_workset`, `consult`
  - `consult` auto-routes questions to relevant agent sections with cross-agent compositing
  - `workset://system-prompt` resource for adaptive expertise injection
  - User-invocable skills exposed as MCP prompts
  - Consultation logger for data-driven agent curation

## v1.2.0 (2026-03-22)

### Added
- **Worksets**: Activate subsets of agents/skills per session
  - Vault + hardlink mechanism (zero disk overhead, ~50ms activation)
  - Named workset definitions with tags, explicit lists, extends composition
  - Dependency resolution (auto-includes required agents)
  - Push/pull bracket (sync always sees full set)
  - `workset suggest` with Project Affinity Engine
  - Commands: `init`, `create`, `activate`, `deactivate`, `list`, `show`, `delete`, `status`, `suggest`
- **Skill enrichment script** (`scripts/enrich-skills.py`): Evolves stub skills into operational skills with workflows, gotchas, quality checklists, and bloodline tracking

## v1.1.0 (2026-02-15)

### Added
- **Skill Genome**: Dependency management for skills
  - `requires:` frontmatter block for declaring skill/agent/mcp/rule dependencies
  - Atomized triggers (per-skill `triggers.json` replacing monolithic `skill-rules.json`)
  - Commands: `scan`, `health`, `graph`, `install`, `extract-triggers`, `assemble-triggers`, `package`
- **Ecosystem analysis**: `duplicates`, `related`, `catalog`, `stats`, `stale`, `timeline`, `prune`, `archive`
- **Three-way merge** with manifest as merge base for conflict detection
- **Resolve command** for per-file conflict resolution
- **Watch command** with watchdog support and polling fallback
- **Git hooks** for auto-sync on pull/push
- **Drift detection** across machines

## v1.0.0 (2026-02-07)

### Added
- Initial release of claude-sync
- Commands: init, status, push, pull, diff, doctor, backup, restore
- Secret scanning with 9 regex patterns
- Timestamped backups with retention pruning
- Settings.json portable/machine-specific key separation
- 9 health checks in doctor command
- Colored unified diff output
- JSON output mode (--json)
- Dry-run mode for push/pull/restore
