# Skill Genome

> Brief: npm-style dependency management for Claude Code skills -- declarations, resolution, health checks, packaging.
> Tags: genome, dependencies, skills
> Created: 2026-04-03
> Updated: 2026-04-03

## Motivation
Skills have invisible dependency trees. `figma-to-code` needs `design-sync` which needs `design-tokens`, plus specific agents and MCP servers. Installing a skill without its deps silently breaks. The monolithic `skill-rules.json` (65KB+) causes merge conflicts when two machines edit different skills.

## Overview

### Dependency Declarations

Skills declare dependencies in SKILL.md frontmatter:

```yaml
---
name: figma-to-code
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

Each skill owns its own `triggers.json` instead of sharing a monolithic `skill-rules.json`:

```
skills/figma-to-code/
  SKILL.md          # Definition
  triggers.json     # THIS skill's trigger config only
```

`skill-rules.json` becomes a derived artifact, auto-assembled on push/pull. Two machines editing different skills = no merge conflict.

### Commands

| Command | Purpose |
|---------|---------|
| `genome scan` | List all skills with their dependency declarations |
| `genome health` | Check for missing deps, broken refs, cycles |
| `genome graph --skill X` | Visualize dependency tree (tree/flat/dot format) |
| `genome install X` | Install skill + all transitive dependencies |
| `genome extract-triggers` | Split monolithic skill-rules.json into per-skill files |
| `genome assemble-triggers` | Rebuild skill-rules.json from per-skill triggers |
| `genome package X` | Export skill + all deps as tar.gz for sharing |

### Implementation

The `SkillGenomeEngine` class (~530 lines) handles:
- YAML frontmatter parsing for `requires:` blocks
- Transitive dependency resolution with cycle detection
- Health checks (missing deps, broken refs)
- DOT format graph generation for Graphviz
- Tar.gz packaging with dependency bundling

## Key Decisions

- **Frontmatter-based declarations** -- No separate manifest file per skill; deps live in the SKILL.md itself
- **Backward-compatible** -- Skills without `requires:` still work; genome is opt-in
- **Atomized triggers** -- Solves the merge conflict problem at the source

## See Also
- [[Worksets]]
- [[Platform Features]]
