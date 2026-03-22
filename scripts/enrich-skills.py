#!/usr/bin/env python3
"""
enrich-skills.py — Evolve stub skills into functional operational skills.

Reads each agent file, extracts structured sections (workflow, quality,
gotchas, collaboration), and generates enriched SKILL.md files with
pipeline position, workflows, quality checklists, and gotchas.

Only touches 26-27 line stubs. Never overwrites manually enriched skills.

Usage:
    python enrich-skills.py --dry-run           # Preview all batches
    python enrich-skills.py --batch 1           # Run batch 1 (dev-core + dev-xcode)
    python enrich-skills.py --batch 2           # Run batch 2 (dev-web + dev-figma)
    python enrich-skills.py --batch 3           # Run batch 3 (design-ux + design-visual)
    python enrich-skills.py --batch 4           # Run batch 4 (everything else)
    python enrich-skills.py --all               # Run all batches
    python enrich-skills.py --skill <name>      # Enrich a single skill
    python enrich-skills.py --report            # Show stub vs enriched counts
"""

import argparse
import datetime
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


# =============================================================================
# Configuration
# =============================================================================

HOME_CLAUDE = Path.home() / ".claude"
AGENTS_DIR = HOME_CLAUDE / "agents"
SKILLS_DIR = HOME_CLAUDE / "skills"
VAULT_DIR = HOME_CLAUDE / ".workset-vault"
WORKSETS_DIR = HOME_CLAUDE / "worksets"

# Repo location (for git-tracked copy)
REPO_SKILLS = Path.home() / "Documents" / "GitHub" / "rydersClaude" / "claude" / "skills"

# Stub detection: skills at exactly these line counts are stubs
STUB_LINE_COUNTS = {26, 27}

# Batch definitions: workset names per batch
BATCHES = {
    1: ["dev-core", "dev-xcode"],
    2: ["dev-web", "dev-figma"],
    3: ["design-ux", "design-visual"],
    4: ["finance", "product", "writing"],  # + everything else
}


# =============================================================================
# Agent Parser
# =============================================================================

class AgentData:
    """Parsed agent content."""

    def __init__(self, name: str):
        self.name = name
        self.description = ""
        self.tags: List[str] = []
        self.version = "1.0.0"
        self.workflow_steps: List[str] = []
        self.quality_criteria: List[str] = []
        self.gotchas: List[str] = []
        self.delegates_to: List[Tuple[str, str]] = []  # (agent_name, reason)
        self.invoked_by: List[str] = []
        self.deliverables: List[str] = []
        self.safety_boundaries: List[str] = []
        self.core_capabilities: List[str] = []
        self.has_enough_content = False


def parse_agent(path: Path) -> Optional[AgentData]:
    """Parse an agent .md file into structured data."""
    if not path.exists():
        return None

    content = path.read_text(encoding="utf-8", errors="replace")
    name = path.stem
    data = AgentData(name)

    # Parse frontmatter
    if content.startswith("---"):
        end = content.find("---", 3)
        if end > 0:
            fm = content[3:end]
            for line in fm.splitlines():
                stripped = line.strip()
                if stripped.startswith("description:"):
                    val = stripped[12:].strip().strip('"').strip("'")
                    # Truncate long descriptions
                    if len(val) > 200:
                        val = val[:200].rsplit(" ", 1)[0] + "..."
                    data.description = val
                elif stripped.startswith("tags:"):
                    val = stripped[5:].strip()
                    if val.startswith("[") and val.endswith("]"):
                        data.tags = [t.strip().strip('"').strip("'")
                                     for t in val[1:-1].split(",") if t.strip()]
                elif stripped.startswith("version:"):
                    data.version = stripped[8:].strip().strip('"').strip("'")

    # Parse sections
    sections = _split_sections(content)

    # Extract workflow steps
    for key in ["Standard Workflow", "Audit Methodology", "Workflow",
                 "Core Process", "Methodology", "Operating Procedure",
                 "Process", "How It Works", "Approach"]:
        if key in sections:
            data.workflow_steps = _extract_list_items(sections[key])
            break

    # Extract quality criteria
    for key in ["Quality Standards", "Evaluation Criteria", "Success Metrics",
                 "Quality Checklist", "KPIs", "Outputs", "Output Format"]:
        if key in sections:
            data.quality_criteria = _extract_list_items(sections[key])
            break

    # Extract gotchas from safety boundaries
    for key in ["Safety Boundaries", "Special Considerations", "Best Practices",
                 "Common Pitfalls", "Guardrails", "Escalation Triggers",
                 "Decision Heuristics", "When to Use This Skill"]:
        if key in sections:
            data.safety_boundaries = _extract_list_items(sections[key])
            break

    # Extract deliverables
    for key in ["Deliverables", "Deliverable Example", "Commands (actions)"]:
        if key in sections:
            data.deliverables = _extract_list_items(sections[key])
            break

    # Extract core capabilities
    for key in ["Core Expertise", "Core Capabilities", "Core Competencies",
                 "Core Responsibilities", "Purpose and Scope", "Scope",
                 "Inputs", "When to Use This Skill", "Practical Applications"]:
        if key in sections:
            data.core_capabilities = _extract_list_items(sections[key])[:5]
            break

    # Extract collaboration
    if "Agent Collaboration" in sections:
        collab = sections["Agent Collaboration"]
        data.delegates_to = _extract_delegates(collab)
        data.invoked_by = _extract_invoked_by(collab)

    # Determine if we have enough content to generate a useful skill
    # All 179 agents have Agent Collaboration, so delegates_to is always available.
    # Even agents without workflow sections can generate useful skills from
    # collaboration data + core capabilities + generic workflow.
    content_score = 0
    if data.workflow_steps:
        content_score += 3
    if data.quality_criteria:
        content_score += 2
    if data.safety_boundaries:
        content_score += 2
    if data.delegates_to:
        content_score += 2
    if data.core_capabilities:
        content_score += 1
    data.has_enough_content = content_score >= 2

    return data


def _split_sections(content: str) -> Dict[str, str]:
    """Split markdown into {section_title: section_body}."""
    sections: Dict[str, str] = {}
    current_title = None
    current_lines: List[str] = []

    for line in content.splitlines():
        if line.startswith("## "):
            if current_title:
                sections[current_title] = "\n".join(current_lines)
            current_title = line[3:].strip()
            current_lines = []
        elif current_title is not None:
            current_lines.append(line)

    if current_title:
        sections[current_title] = "\n".join(current_lines)

    return sections


def _extract_list_items(text: str) -> List[str]:
    """Extract bullet/numbered list items from markdown text."""
    items = []
    for line in text.splitlines():
        stripped = line.strip()
        # Match "- item", "* item", "1. item", "1) item"
        m = re.match(r'^[-*]\s+(.+)$', stripped)
        if not m:
            m = re.match(r'^\d+[.)]\s+(.+)$', stripped)
        if m:
            item = m.group(1).strip()
            # Clean up bold markers
            item = re.sub(r'\*\*(.+?)\*\*', r'\1', item)
            if len(item) > 10:  # Skip trivially short items
                items.append(item)
    return items


def _extract_delegates(text: str) -> List[Tuple[str, str]]:
    """Extract (agent_name, reason) pairs from Delegates to section."""
    results = []
    in_delegates = False
    for line in text.splitlines():
        if "Delegates to" in line or "delegates to" in line:
            in_delegates = True
            continue
        if "Invoked by" in line or "invoked by" in line:
            in_delegates = False
            continue
        if in_delegates:
            m = re.match(r'^[-*]\s+`([^`]+)`\s*[-–—]\s*(.+)$', line.strip())
            if m:
                results.append((m.group(1), m.group(2).strip()))
    return results


def _extract_invoked_by(text: str) -> List[str]:
    """Extract invoked-by references."""
    results = []
    in_invoked = False
    for line in text.splitlines():
        if "Invoked by" in line or "invoked by" in line:
            in_invoked = True
            continue
        if in_invoked:
            stripped = line.strip()
            if stripped.startswith("- ") or stripped.startswith("* "):
                results.append(stripped[2:].strip())
            elif stripped and not stripped.startswith("#"):
                continue
            else:
                break
    return results


# =============================================================================
# Workset Resolver
# =============================================================================

def load_worksets() -> Dict[str, List[str]]:
    """Load workset definitions and return {agent_name: [workset_names]}."""
    agent_to_worksets: Dict[str, List[str]] = {}
    if not WORKSETS_DIR.exists():
        return agent_to_worksets

    # Load all definitions
    definitions: Dict[str, dict] = {}
    for f in WORKSETS_DIR.glob("*.json"):
        if f.name.startswith("_"):
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            definitions[data["name"]] = data
        except (json.JSONDecodeError, KeyError):
            continue

    # Resolve agents per workset (simplified — no extends resolution here)
    for ws_name, ws_data in definitions.items():
        agents = set(ws_data.get("agents", []))
        for agent_name in agents:
            agent_to_worksets.setdefault(agent_name, []).append(ws_name)

    return agent_to_worksets


def get_batch_skills(batch_num: int) -> Set[str]:
    """Get skill names for a batch based on workset membership."""
    if batch_num not in BATCHES:
        return set()

    workset_names = BATCHES[batch_num]
    skill_names: Set[str] = set()

    for f in WORKSETS_DIR.glob("*.json"):
        if f.name.startswith("_"):
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("name") in workset_names:
                skill_names.update(data.get("agents", []))
                skill_names.update(data.get("skills", []))
        except (json.JSONDecodeError, KeyError):
            continue

    return skill_names


def get_all_stubs() -> Set[str]:
    """Get names of all stub skills."""
    stubs = set()
    for skill_dir in SKILLS_DIR.iterdir():
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if skill_md.exists():
            line_count = len(skill_md.read_text(encoding="utf-8").splitlines())
            if line_count in STUB_LINE_COUNTS:
                stubs.add(skill_dir.name)
    return stubs


# =============================================================================
# Skill Generator
# =============================================================================

def generate_skill(agent_data: AgentData, workset_context: Dict[str, List[str]]) -> str:
    """Generate an enriched SKILL.md from agent data."""
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    name = agent_data.name
    title = name.replace("-", " ").title()

    lines = []

    # Frontmatter
    lines.append("---")
    lines.append(f'name: "{name}"')
    lines.append(f'description: "{agent_data.description}"')
    lines.append("bloodline:")
    lines.append(f'  born: "{now}"')
    lines.append("  generation: 2")
    lines.append('  parent: "stub-v1"')
    lines.append(f'  enriched_from: "agent:{name}@{agent_data.version}"')
    lines.append("  gotchas_added: 0")
    lines.append("  last_gotcha_session: null")
    lines.append("---")
    lines.append("")
    lines.append(f"# {title}")
    lines.append("")

    # Pipeline Position
    lines.append("## Pipeline Position")
    lines.append("")
    worksets = workset_context.get(name, [])
    if worksets:
        lines.append(f"**Worksets**: {', '.join(sorted(worksets))}")
    if agent_data.delegates_to:
        delegates_str = ", ".join(f"`{d[0]}`" for d in agent_data.delegates_to[:4])
        lines.append(f"**Delegates to**: {delegates_str}")
    if agent_data.invoked_by:
        lines.append(f"**Invoked by**: {agent_data.invoked_by[0]}")
    lines.append("")

    # Workflow
    lines.append("## Workflow")
    lines.append("")
    if agent_data.workflow_steps:
        steps = agent_data.workflow_steps[:6]  # Cap at 6 steps
        for i, step in enumerate(steps, 1):
            # Truncate long steps
            if len(step) > 120:
                step = step[:120].rsplit(" ", 1)[0] + "..."
            lines.append(f"{i}. {step}")
    else:
        # Generate generic workflow from capabilities
        lines.append("1. **Understand context** — read relevant code/files, clarify requirements")
        lines.append(f"2. **Execute** — apply `{name}` agent expertise to the task")
        lines.append("3. **Verify** — check output against quality criteria below")
        lines.append("4. **Hand off** — document results, invoke work-logger if significant")
    lines.append("")

    # Quality Checklist
    lines.append("## Quality Checklist")
    lines.append("")
    if agent_data.quality_criteria:
        for criterion in agent_data.quality_criteria[:7]:  # Cap at 7
            if len(criterion) > 100:
                criterion = criterion[:100].rsplit(" ", 1)[0] + "..."
            lines.append(f"- [ ] {criterion}")
    else:
        # Generate from core capabilities
        for cap in agent_data.core_capabilities[:4]:
            if len(cap) > 100:
                cap = cap[:100].rsplit(" ", 1)[0] + "..."
            lines.append(f"- [ ] {cap}")
        lines.append("- [ ] Results verified against requirements")
        lines.append("- [ ] No regressions introduced")
    lines.append("")

    # Gotchas
    lines.append("## Gotchas")
    lines.append("")
    if agent_data.safety_boundaries:
        for gotcha in agent_data.safety_boundaries[:5]:  # Cap at 5
            if len(gotcha) > 120:
                gotcha = gotcha[:120].rsplit(" ", 1)[0] + "..."
            lines.append(f"- {gotcha}")
    else:
        # Generate domain-appropriate gotchas
        lines.append(f"- Don't assume — read the actual code/state before making changes")
        lines.append(f"- Verify the fix works, don't just check that it builds")
        lines.append(f"- Check for side effects in related components")
    lines.append("")

    # Deliverables (if available)
    if agent_data.deliverables:
        lines.append("## Deliverables")
        lines.append("")
        for d in agent_data.deliverables[:5]:
            if len(d) > 100:
                d = d[:100].rsplit(" ", 1)[0] + "..."
            lines.append(f"- {d}")
        lines.append("")

    return "\n".join(lines) + "\n"


# =============================================================================
# Writer
# =============================================================================

def write_skill(name: str, content: str, dry_run: bool = False) -> List[str]:
    """Write enriched skill to all 3 locations. Returns list of paths written."""
    paths = [
        SKILLS_DIR / name / "SKILL.md",
        VAULT_DIR / "skills" / name / "SKILL.md",
        REPO_SKILLS / name / "SKILL.md",
    ]

    written = []
    for p in paths:
        if p.parent.exists():
            if dry_run:
                written.append(str(p))
            else:
                p.write_text(content, encoding="utf-8")
                written.append(str(p))

    return written


# =============================================================================
# CLI
# =============================================================================

def cmd_report():
    """Show stub vs enriched counts."""
    stubs = get_all_stubs()
    total = len(list(SKILLS_DIR.glob("*/SKILL.md")))
    enriched = total - len(stubs)
    print(f"Total skills:    {total}")
    print(f"Stubs (26-27L):  {len(stubs)}")
    print(f"Enriched (28+L): {enriched}")
    print()

    # Show by workset
    for batch_num, ws_names in BATCHES.items():
        batch_skills = get_batch_skills(batch_num)
        batch_stubs = batch_skills & stubs
        print(f"Batch {batch_num} ({', '.join(ws_names)}): "
              f"{len(batch_stubs)} stubs / {len(batch_skills)} total")

    # Remainder
    all_batch_skills = set()
    for b in BATCHES.values():
        for ws_name in b:
            all_batch_skills |= get_batch_skills(list(BATCHES.keys())[0])
    remaining = stubs - all_batch_skills
    if remaining:
        print(f"Unaffiliated stubs: {len(remaining)}")


def cmd_enrich(targets: Set[str], dry_run: bool = False):
    """Enrich a set of stub skills."""
    stubs = get_all_stubs()
    workset_context = load_worksets()

    # Only process targets that are actually stubs
    to_process = targets & stubs
    skipped_not_stub = targets - stubs
    if skipped_not_stub:
        print(f"Skipping {len(skipped_not_stub)} already-enriched skills")

    enriched = 0
    flagged = 0
    errors = 0

    for name in sorted(to_process):
        agent_path = AGENTS_DIR / f"{name}.md"
        agent_data = parse_agent(agent_path)

        if not agent_data:
            print(f"  SKIP {name} — no matching agent file")
            errors += 1
            continue

        if not agent_data.has_enough_content:
            print(f"  FLAG {name} — agent lacks structured content for auto-enrichment")
            flagged += 1
            continue

        content = generate_skill(agent_data, workset_context)

        if dry_run:
            print(f"  PREVIEW {name} ({len(content.splitlines())} lines)")
            # Show first few lines of generated content after frontmatter
            preview_lines = content.splitlines()
            for line in preview_lines[12:20]:  # Skip frontmatter, show workflow
                print(f"    {line}")
            print()
        else:
            paths = write_skill(name, content, dry_run=False)
            print(f"  OK {name} → {len(paths)} locations ({len(content.splitlines())} lines)")
            enriched += 1

    print()
    print(f"Results: {enriched} enriched, {flagged} flagged for manual, "
          f"{errors} errors, {len(skipped_not_stub)} already enriched")


def main():
    parser = argparse.ArgumentParser(
        description="Evolve stub skills into functional operational skills"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview without writing")
    parser.add_argument("--batch", type=int, choices=[1, 2, 3, 4],
                        help="Run a specific batch")
    parser.add_argument("--all", action="store_true",
                        help="Run all batches")
    parser.add_argument("--skill", help="Enrich a single skill by name")
    parser.add_argument("--report", action="store_true",
                        help="Show stub vs enriched counts")

    args = parser.parse_args()

    if args.report:
        cmd_report()
        return

    if args.skill:
        cmd_enrich({args.skill}, dry_run=args.dry_run)
        return

    if args.batch:
        targets = get_batch_skills(args.batch)
        if args.batch == 4:
            # Batch 4 includes everything not in batches 1-3
            all_stubs = get_all_stubs()
            covered = set()
            for b in [1, 2, 3]:
                covered |= get_batch_skills(b)
            targets = (all_stubs - covered) | get_batch_skills(4)
        print(f"Batch {args.batch}: {len(targets)} skills")
        cmd_enrich(targets, dry_run=args.dry_run)
        return

    if args.all:
        all_stubs = get_all_stubs()
        print(f"All stubs: {len(all_stubs)} skills")
        cmd_enrich(all_stubs, dry_run=args.dry_run)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
