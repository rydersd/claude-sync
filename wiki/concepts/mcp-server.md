# MCP Server

> Brief: Stdio-based MCP server (821 lines) bridging agents/skills to Claude Desktop with auto-routing consult.
> Tags: mcp, desktop, claude-desktop
> Created: 2026-04-03
> Updated: 2026-04-03

## Motivation
Claude Code CLI has access to 179+ agents and skills. Claude Desktop (the GUI app) does not. The MCP server bridges this gap, making the same ecosystem accessible from either interface.

## Overview

`claude-sync-mcp.py` is a standalone stdio MCP server that reads from `~/.claude/agents/` and `~/.claude/skills/` and exposes them as tools, resources, and prompts.

### Tools (7)

| Tool | Purpose |
|------|---------|
| `search_agents(query)` | Fuzzy search agents by name/description/tags |
| `get_agent(name)` | Full agent definition content |
| `get_skill(name)` | Full skill definition content |
| `list_worksets()` | All worksets with file counts |
| `activate_workset(name)` | Switch active workset |
| `suggest_workset(project_path)` | Auto-suggest from affinity data |
| `consult(question, context)` | Auto-route to 1-3 most relevant agents |

### consult -- Expertise Routing

The `consult` tool is the key differentiator. Instead of manually searching for the right agent:

1. Takes a natural language question
2. Fuzzy-matches against all agent descriptions
3. Selects the 1-3 most relevant agents
4. Extracts only the pertinent sections from each
5. Returns a composite response

Consultations are logged to `~/.claude/mcp-consult.log` for data-driven agent curation.

### Resources

- `workset://system-prompt` -- Compressed system prompt from active workset's top agents
- `workset://active` -- Current workset state
- `agent://{name}` -- Full agent content
- `skill://{name}` -- Full skill content

### Setup

Add to Claude Desktop's `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "claude-sync": {
      "command": "python3",
      "args": ["/path/to/claude-sync-mcp.py"]
    }
  }
}
```

## Key Decisions

- **Stdio transport** -- Simplest MCP transport; no HTTP server, no port management
- **Standalone file** -- Not part of claude-sync.py to keep concerns separate
- **Reads live filesystem** -- Always reflects current workset state, no caching

## See Also
- [[Worksets]]
- [[Architecture]]
