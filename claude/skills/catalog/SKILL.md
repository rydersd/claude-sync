---
name: catalog
description: Browse and search your agent/skill collection
allowed-tools: [Read, Glob, Grep]
---

# /catalog

Browse and search all agents and skills in your Claude Code configuration.

## Usage

- `/catalog` - List all agents and skills with categories
- `/catalog agents` - List agents only
- `/catalog skills` - List skills only
- `/catalog search <term>` - Search by keyword

## Implementation

1. Read all agent files: `~/.claude/agents/*.md`
2. Read all skill files: `~/.claude/skills/*/SKILL.md`
3. Parse YAML frontmatter from each file
4. Categorize by function
5. Display as formatted table

## Output Format

```
AGENT/SKILL CATALOG

Agents (16):
  Name                    Category      Description
  apple-dev-expert        Development   Production-grade Apple platform development
  config-sync-agent       Workflow      Complex sync scenario handler
  ...

Skills (41):
  Name                    Category      Description
  /commit                 Workflow      Git commit with reasoning capture
  /sync                   Workflow      Sync config between machines
  ...
```

## Categories

- **Development**: Code writing, testing, debugging, platform-specific
- **Research**: Codebase analysis, web search, documentation lookup
- **DevOps**: CI/CD, deployment, release preparation
- **Quality**: Accessibility, performance, security auditing
- **Workflow**: Session management, handoffs, sync, commits
- **Meta**: Tools for creating more tools (new-agent, new-skill)
