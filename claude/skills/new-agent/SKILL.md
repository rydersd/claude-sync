---
name: new-agent
description: Scaffold a new agent definition from template
allowed-tools: [Write, Read, AskUserQuestion]
---

# /new-agent

Create a new agent definition file from a template.

## Usage

```
/new-agent <name>
```

## Flow

1. Ask for agent details:
   - **Name**: From argument or prompt
   - **Description**: What does this agent do?
   - **Model**: sonnet (default), opus, haiku
   - **Tools**: Which tools should it have access to?
   - **Color**: Optional color for UI display

2. Generate agent file at `~/.claude/agents/<name>.md`

3. Suggest running `/sync push` to capture in repo

## Template

```markdown
---
name: {name}
description: {description}
model: {model}
tools: [{tools}]
---

# {Title} Agent

{description}

## When to Use

Spawn this agent when:
- [Describe use case 1]
- [Describe use case 2]

## Capabilities

[Describe what the agent can do]

## Usage

\`\`\`
Spawn {name} to [do something]
\`\`\`
```
