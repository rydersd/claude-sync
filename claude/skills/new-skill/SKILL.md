---
name: new-skill
description: Scaffold a new skill definition from template
allowed-tools: [Write, Read, AskUserQuestion]
---

# /new-skill

Create a new skill definition from a template.

## Usage

```
/new-skill <name>
```

## Flow

1. Ask for skill details:
   - **Name**: From argument or prompt
   - **Description**: What does this skill do?
   - **Allowed Tools**: Which tools can this skill use?
   - **Trigger phrases**: What should activate this skill?

2. Create directory: `~/.claude/skills/<name>/`

3. Generate `SKILL.md` at `~/.claude/skills/<name>/SKILL.md`

4. Add trigger entry to `~/.claude/skills/skill-rules.json`

5. Suggest running `/sync push` to capture in repo

## Template

```markdown
---
name: {name}
description: {description}
allowed-tools: [{tools}]
---

# /{name}

{description}

## Usage

\`\`\`
/{name} [arguments]
\`\`\`

## Steps

1. [Step 1]
2. [Step 2]
3. [Step 3]
```

## skill-rules.json Entry

```json
{
  "name": "{name}",
  "triggers": ["{trigger1}", "{trigger2}"],
  "skill": "/{name}"
}
```
