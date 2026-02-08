---
name: export-plugin
description: Package agents/skills as an Anthropic plugin for distribution
allowed-tools: [Bash, Read, Write, Glob]
---

# /export-plugin

Package selected agents and skills into Anthropic plugin format.

## Usage

```
/export-plugin <plugin-name> --agents agent1,agent2 --skills skill1,skill2
```

## Arguments

- `<plugin-name>` - Name for the plugin (required)
- `--agents` - Comma-separated list of agent names to include
- `--skills` - Comma-separated list of skill names to include
- `--output` - Output directory (default: ./plugins/<plugin-name>)
- `--author` - Author name for plugin.json
- `--version` - Version string (default: 1.0.0)

## Steps

1. Validate all specified agents/skills exist
2. Create plugin directory structure
3. Copy agent .md files to `agents/`
4. Copy skill directories to `skills/`
5. Generate `.claude-plugin/plugin.json`
6. Validate all frontmatter fields present
7. Generate README.md with usage instructions
8. Report any format issues

## Example

```bash
/export-plugin xcode-dev-tools \
  --agents apple-dev-expert,swiftui-design-system-debugger \
  --skills xcode-build,xcode-test,xcode-clean,xcode-launch \
  --author "Ryder" \
  --version 1.0.0
```
