#!/usr/bin/env python3
"""
claude-sync-mcp: MCP server bridging the agent/skill ecosystem to Claude Desktop.

Exposes agents, skills, and worksets via stdio MCP transport so Claude Desktop
can access the same specialist knowledge as Claude Code. Includes the `consult`
tool for automatic expertise routing and an adaptive system prompt composer.

Usage:
    Add to ~/Library/Application Support/Claude/claude_desktop_config.json:
    {
      "mcpServers": {
        "claude-sync": {
          "command": "python3",
          "args": ["/path/to/claude-sync-mcp.py"]
        }
      }
    }
"""

import datetime
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


# =============================================================================
# Configuration
# =============================================================================

HOME_CLAUDE = Path.home() / ".claude"
AGENTS_DIR = HOME_CLAUDE / "agents"
SKILLS_DIR = HOME_CLAUDE / "skills"
VAULT_DIR = HOME_CLAUDE / ".workset-vault"
WORKSETS_DIR = HOME_CLAUDE / "worksets"
STATE_PATH = WORKSETS_DIR / "_state.json"
AFFINITY_PATH = WORKSETS_DIR / "_affinity.json"
CONSULT_LOG = HOME_CLAUDE / "mcp-consult.log"

PROTOCOL_VERSION = "2024-11-05"

# Common words to skip in search scoring
STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "to", "of", "in", "for", "on", "with",
    "at", "by", "from", "as", "into", "through", "and", "but", "or", "not",
    "so", "if", "when", "this", "that", "it", "its", "use", "using", "agent",
    "skill", "you", "your", "how", "what", "which", "need", "want",
}


# =============================================================================
# Data Layer
# =============================================================================

class AgentInfo:
    __slots__ = ("name", "description", "tags", "content", "sections", "mtime")

    def __init__(self, name, description, tags, content, sections, mtime):
        self.name = name
        self.description = description
        self.tags = tags
        self.content = content
        self.sections = sections  # Dict[str, str] — section_title -> section_body
        self.mtime = mtime


class SkillInfo:
    __slots__ = ("name", "description", "content", "user_invocable", "mtime")

    def __init__(self, name, description, content, user_invocable, mtime):
        self.name = name
        self.description = description
        self.content = content
        self.user_invocable = user_invocable
        self.mtime = mtime


class DataLayer:
    """Filesystem-backed data layer with mtime caching."""

    def __init__(self):
        self.agents: Dict[str, AgentInfo] = {}
        self.skills: Dict[str, SkillInfo] = {}
        self.worksets: Dict[str, dict] = {}
        self._agent_mtimes: Dict[str, float] = {}
        self._skill_mtimes: Dict[str, float] = {}
        self._loaded = False

    def ensure_loaded(self):
        if not self._loaded:
            self._load_all()
            self._loaded = True

    def reload_if_changed(self):
        """Check mtimes and reload changed files."""
        self._load_agents()
        self._load_skills()
        self._load_worksets()

    def _load_all(self):
        self._load_agents()
        self._load_skills()
        self._load_worksets()

    def _load_agents(self):
        source = VAULT_DIR / "agents" if VAULT_DIR.exists() else AGENTS_DIR
        if not source.exists():
            return
        for f in source.glob("*.md"):
            mtime = f.stat().st_mtime
            if f.name in self._agent_mtimes and self._agent_mtimes[f.name] == mtime:
                continue
            self._agent_mtimes[f.name] = mtime
            name = f.stem
            try:
                content = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            desc, tags = _parse_frontmatter(content)
            sections = _parse_sections(content)
            self.agents[name] = AgentInfo(name, desc, tags, content, sections, mtime)

    def _load_skills(self):
        source = VAULT_DIR / "skills" if VAULT_DIR.exists() else SKILLS_DIR
        if not source.exists():
            return
        for d in source.iterdir():
            if not d.is_dir() or d.name.startswith("_"):
                continue
            skill_md = d / "SKILL.md"
            if not skill_md.exists():
                continue
            mtime = skill_md.stat().st_mtime
            key = d.name
            if key in self._skill_mtimes and self._skill_mtimes[key] == mtime:
                continue
            self._skill_mtimes[key] = mtime
            try:
                content = skill_md.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            desc, _ = _parse_frontmatter(content)
            user_invocable = _is_user_invocable(content)
            self.skills[key] = SkillInfo(key, desc, content, user_invocable, mtime)

    def _load_worksets(self):
        if not WORKSETS_DIR.exists():
            return
        self.worksets = {}
        for f in WORKSETS_DIR.glob("*.json"):
            if f.name.startswith("_"):
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                self.worksets[data["name"]] = data
            except (json.JSONDecodeError, KeyError):
                continue

    def get_active_workset(self) -> Optional[str]:
        if STATE_PATH.exists():
            try:
                data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
                return data.get("active_workset")
            except (json.JSONDecodeError, TypeError):
                pass
        return None

    def get_workset_agents(self, name: str) -> Set[str]:
        """Resolve a workset to its agent names (with extends)."""
        return self._resolve_workset(name, set())[0]

    def get_workset_skills(self, name: str) -> Set[str]:
        """Resolve a workset to its skill names (with extends)."""
        return self._resolve_workset(name, set())[1]

    def _resolve_workset(self, name: str, visited: Set[str]) -> Tuple[Set[str], Set[str]]:
        if name in visited or name not in self.worksets:
            return set(), set()
        visited.add(name)
        ws = self.worksets[name]
        agents = set(ws.get("agents", []))
        skills = set(ws.get("skills", []))
        for parent in ws.get("extends", []):
            pa, ps = self._resolve_workset(parent, visited)
            agents |= pa
            skills |= ps
        # Tag expansion
        for tag in ws.get("tags", []):
            tag_lower = tag.lower()
            for aname, ainfo in self.agents.items():
                if any(t.lower() == tag_lower for t in ainfo.tags):
                    agents.add(aname)
        agents -= set(ws.get("exclude_agents", []))
        skills -= set(ws.get("exclude_skills", []))
        return agents, skills

    def get_active_agent_names(self) -> Set[str]:
        """Agent names filtered by active workset, or all if none active."""
        active = self.get_active_workset()
        if active and active in self.worksets:
            return self.get_workset_agents(active)
        return set(self.agents.keys())

    def load_affinity(self) -> dict:
        if AFFINITY_PATH.exists():
            try:
                return json.loads(AFFINITY_PATH.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, TypeError):
                pass
        return {"projects": {}, "language_affinity": {}}


# =============================================================================
# Parsing Helpers
# =============================================================================

def _parse_frontmatter(content: str) -> Tuple[str, List[str]]:
    """Extract description and tags from YAML frontmatter."""
    desc = ""
    tags = []
    if not content.startswith("---"):
        return desc, tags
    end = content.find("---", 3)
    if end <= 0:
        return desc, tags
    fm = content[3:end]
    for line in fm.splitlines():
        stripped = line.strip()
        if stripped.startswith("description:"):
            val = stripped[12:].strip().strip('"').strip("'")
            if len(val) > 200:
                val = val[:200].rsplit(" ", 1)[0] + "..."
            desc = val
        elif stripped.startswith("tags:"):
            val = stripped[5:].strip()
            if val.startswith("[") and val.endswith("]"):
                tags = [t.strip().strip('"').strip("'")
                        for t in val[1:-1].split(",") if t.strip()]
    return desc, tags


def _parse_sections(content: str) -> Dict[str, str]:
    """Split markdown into {section_title: section_body}."""
    sections = {}
    current = None
    lines: List[str] = []
    for line in content.splitlines():
        if line.startswith("## "):
            if current:
                sections[current] = "\n".join(lines)
            current = line[3:].strip()
            lines = []
        elif current is not None:
            lines.append(line)
    if current:
        sections[current] = "\n".join(lines)
    return sections


def _is_user_invocable(content: str) -> bool:
    """Check if a skill should be exposed as an MCP prompt."""
    if not content.startswith("---"):
        return False
    end = content.find("---", 3)
    if end <= 0:
        return False
    fm = content[3:end]
    for line in fm.splitlines():
        stripped = line.strip()
        if stripped.startswith("user_invocable:"):
            val = stripped[15:].strip().lower()
            return val in ("true", "yes", "1")
    # Heuristic: only skills with explicit usage patterns or step-by-step processes
    # Pipeline Position is too broad (all enriched skills have it now)
    if "## Process" in content or "## Input" in content:
        return True
    if "Usage:" in content[:300]:
        return True
    return False


# =============================================================================
# Search Engine
# =============================================================================

def search_agents(data: DataLayer, query: str, limit: int = 10) -> List[dict]:
    """Fuzzy search agents by name, description, tags."""
    tokens = _tokenize(query)
    if not tokens:
        return []

    active_names = data.get_active_agent_names()
    scored = []

    for name, agent in data.agents.items():
        if name not in active_names:
            continue
        score = 0
        name_lower = name.lower().replace("-", " ")
        desc_lower = agent.description.lower()
        tags_lower = " ".join(t.lower() for t in agent.tags)

        for token in tokens:
            if token in name_lower:
                score += 3
            if token in tags_lower:
                score += 2
            if token in desc_lower:
                score += 1

        if score > 0:
            scored.append({"name": name, "description": agent.description,
                           "tags": agent.tags, "score": score})

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:limit]


def _tokenize(text: str) -> List[str]:
    """Tokenize and filter stop words."""
    words = re.findall(r'[a-z]+', text.lower())
    return [w for w in words if w not in STOP_WORDS and len(w) > 2]


# =============================================================================
# Consult Engine
# =============================================================================

def consult(data: DataLayer, question: str, context: str = "") -> str:
    """Auto-route question to relevant agent sections."""
    tokens = _tokenize(question + " " + context)
    if not tokens:
        return "No question provided."

    # Score all agents
    agent_scores: List[Tuple[str, int]] = []
    for name, agent in data.agents.items():
        score = 0
        name_lower = name.lower().replace("-", " ")
        desc_lower = agent.description.lower()
        tags_lower = " ".join(t.lower() for t in agent.tags)
        for token in tokens:
            if token in name_lower:
                score += 3
            if token in tags_lower:
                score += 2
            if token in desc_lower:
                score += 1
        if score > 0:
            agent_scores.append((name, score))

    agent_scores.sort(key=lambda x: x[1], reverse=True)
    top_agents = agent_scores[:3]

    if not top_agents:
        return "No relevant agents found for this question."

    # Extract relevant sections from top agents
    result_parts = []
    agents_used = []
    sections_used = []

    for agent_name, _ in top_agents:
        agent = data.agents[agent_name]
        relevant = _score_sections(agent.sections, tokens)
        if relevant:
            result_parts.append(f"[From: {agent_name}]")
            for section_title, section_body, _ in relevant[:3]:
                result_parts.append(f"## {section_title}")
                # Truncate very long sections
                body = section_body.strip()
                if len(body) > 800:
                    body = body[:800].rsplit("\n", 1)[0] + "\n..."
                result_parts.append(body)
                result_parts.append("")
                sections_used.append(f"{agent_name}/{section_title}")
            agents_used.append(agent_name)

    # Log consultation
    _log_consultation(question, agents_used, sections_used)

    return "\n".join(result_parts)


def _score_sections(sections: Dict[str, str], tokens: List[str]) -> List[Tuple[str, str, int]]:
    """Score sections by relevance to query tokens."""
    scored = []
    # Skip metadata sections
    skip = {"Agent Collaboration", "After Task Completion"}
    for title, body in sections.items():
        if title in skip:
            continue
        text = (title + " " + body).lower()
        score = sum(1 for t in tokens if t in text)
        if score > 0:
            scored.append((title, body, score))
    scored.sort(key=lambda x: x[2], reverse=True)
    return scored


def _log_consultation(question: str, agents: List[str], sections: List[str]):
    """Append consultation to log file."""
    try:
        entry = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "question_keywords": question[:100],
            "agents_selected": agents,
            "sections_used": sections,
        }
        with open(CONSULT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


# =============================================================================
# System Prompt Composer
# =============================================================================

def compose_system_prompt(data: DataLayer) -> str:
    """Compose a compressed system prompt from the active workset's agents."""
    active = data.get_active_workset()
    if active and active in data.worksets:
        agent_names = data.get_workset_agents(active)
        header = f"Active workset: {active} ({len(agent_names)} agents)"
    else:
        agent_names = set(list(data.agents.keys())[:10])
        header = "No workset active — showing top 10 agents"

    parts = [f"# Agent Expertise Context\n\n{header}\n"]

    for name in sorted(agent_names)[:10]:
        agent = data.agents.get(name)
        if not agent:
            continue
        parts.append(f"## {name}")
        # Extract operational essence: gotchas + quality + key workflow
        for section_key in ["Safety Boundaries", "Guardrails", "Quality Standards",
                            "Gotchas", "Standard Workflow", "Operating Procedure"]:
            if section_key in agent.sections:
                body = agent.sections[section_key].strip()
                if len(body) > 300:
                    body = body[:300].rsplit("\n", 1)[0] + "\n..."
                parts.append(f"**{section_key}**: {body}")
                break
        parts.append("")

    return "\n".join(parts)


# =============================================================================
# MCP Protocol Handler
# =============================================================================

class MCPServer:
    """Stdio-based MCP server."""

    def __init__(self):
        self.data = DataLayer()

    def run(self):
        """Main event loop: read JSON-RPC from stdin, write responses to stdout."""
        self.data.ensure_loaded()
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue

            method = msg.get("method", "")
            msg_id = msg.get("id")
            params = msg.get("params", {})

            # Notifications (no id) — just acknowledge
            if msg_id is None:
                if method == "notifications/initialized":
                    pass  # No response needed
                continue

            # Dispatch
            handler = {
                "initialize": self._handle_initialize,
                "tools/list": self._handle_tools_list,
                "tools/call": self._handle_tools_call,
                "resources/list": self._handle_resources_list,
                "resources/read": self._handle_resources_read,
                "prompts/list": self._handle_prompts_list,
                "prompts/get": self._handle_prompts_get,
            }.get(method)

            if handler:
                result = handler(params)
                self._respond(msg_id, result)
            else:
                self._respond_error(msg_id, -32601, f"Method not found: {method}")

    def _respond(self, msg_id: Any, result: Any):
        resp = {"jsonrpc": "2.0", "id": msg_id, "result": result}
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()

    def _respond_error(self, msg_id: Any, code: int, message: str):
        resp = {
            "jsonrpc": "2.0", "id": msg_id,
            "error": {"code": code, "message": message}
        }
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()

    # ---- Lifecycle ----

    def _handle_initialize(self, params: dict) -> dict:
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {
                "tools": {},
                "resources": {},
                "prompts": {},
            },
            "serverInfo": {
                "name": "claude-sync",
                "version": "1.0.0",
            },
        }

    # ---- Tools ----

    def _handle_tools_list(self, params: dict) -> dict:
        return {"tools": [
            {
                "name": "search_agents",
                "description": "Search for agents by name, description, or tags. Returns top matches from the active workset.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query (e.g., 'swift testing', 'accessibility')"}
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "get_agent",
                "description": "Get the full content of a specific agent by name.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Agent name (e.g., 'apple-dev-expert')"}
                    },
                    "required": ["name"],
                },
            },
            {
                "name": "get_skill",
                "description": "Get the full content of a specific skill by name.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Skill name (e.g., 'blog', 'shaping')"}
                    },
                    "required": ["name"],
                },
            },
            {
                "name": "list_worksets",
                "description": "List all available worksets with their descriptions and agent/skill counts.",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "activate_workset",
                "description": "Activate a workset to filter which agents and skills are available.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Workset name (e.g., 'dev-xcode', 'design-ux')"}
                    },
                    "required": ["name"],
                },
            },
            {
                "name": "suggest_workset",
                "description": "Suggest the best workset based on project context and past usage.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "project_path": {"type": "string", "description": "Path to the project directory (optional)"}
                    },
                },
            },
            {
                "name": "consult",
                "description": "Ask a question and get relevant expertise from the best-matching agents. Auto-routes to 1-3 agents and extracts only the relevant sections.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "description": "Your question or topic"},
                        "context": {"type": "string", "description": "Additional context about what you're working on (optional)"}
                    },
                    "required": ["question"],
                },
            },
        ]}

    def _handle_tools_call(self, params: dict) -> dict:
        self.data.reload_if_changed()
        name = params.get("name", "")
        args = params.get("arguments", {})

        if name == "search_agents":
            results = search_agents(self.data, args.get("query", ""))
            return {"content": [{"type": "text", "text": json.dumps(results, indent=2)}]}

        elif name == "get_agent":
            agent = self.data.agents.get(args.get("name", ""))
            if agent:
                return {"content": [{"type": "text", "text": agent.content}]}
            return {"content": [{"type": "text", "text": f"Agent '{args.get('name')}' not found."}], "isError": True}

        elif name == "get_skill":
            skill = self.data.skills.get(args.get("name", ""))
            if skill:
                return {"content": [{"type": "text", "text": skill.content}]}
            return {"content": [{"type": "text", "text": f"Skill '{args.get('name')}' not found."}], "isError": True}

        elif name == "list_worksets":
            result = []
            active = self.data.get_active_workset()
            for ws_name, ws_data in sorted(self.data.worksets.items()):
                agents = self.data.get_workset_agents(ws_name)
                skills = self.data.get_workset_skills(ws_name)
                result.append({
                    "name": ws_name,
                    "description": ws_data.get("description", ""),
                    "agents": len(agents),
                    "skills": len(skills),
                    "active": ws_name == active,
                })
            return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}

        elif name == "activate_workset":
            ws_name = args.get("name", "")
            if ws_name not in self.data.worksets:
                return {"content": [{"type": "text", "text": f"Workset '{ws_name}' not found."}], "isError": True}
            # Write state
            state = {
                "active_workset": ws_name,
                "activated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "vault_initialized": True,
                "resolved_agents": sorted(self.data.get_workset_agents(ws_name)),
                "resolved_skills": sorted(self.data.get_workset_skills(ws_name)),
            }
            WORKSETS_DIR.mkdir(parents=True, exist_ok=True)
            tmp = STATE_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
            tmp.rename(STATE_PATH)
            agents = self.data.get_workset_agents(ws_name)
            return {"content": [{"type": "text",
                                 "text": f"Activated '{ws_name}': {len(agents)} agents available."}]}

        elif name == "suggest_workset":
            affinity = self.data.load_affinity()
            project_path = args.get("project_path", "")
            # Check project affinity
            for proj_key, proj_data in affinity.get("projects", {}).items():
                if project_path and proj_key in project_path:
                    activations = proj_data.get("activations", {})
                    if activations:
                        best = max(activations, key=activations.get)
                        return {"content": [{"type": "text",
                                             "text": json.dumps({"suggestion": best,
                                                                  "reason": f"Most used for {proj_key}",
                                                                  "activations": activations})}]}
            return {"content": [{"type": "text", "text": "No suggestion available. Use more worksets to build affinity."}]}

        elif name == "consult":
            result = consult(self.data, args.get("question", ""), args.get("context", ""))
            return {"content": [{"type": "text", "text": result}]}

        return {"content": [{"type": "text", "text": f"Unknown tool: {name}"}], "isError": True}

    # ---- Resources ----

    def _handle_resources_list(self, params: dict) -> dict:
        self.data.reload_if_changed()
        resources = []

        # System prompt resource
        resources.append({
            "uri": "workset://system-prompt",
            "name": "Adaptive System Prompt",
            "description": "Compressed system prompt from active workset's top agents",
            "mimeType": "text/markdown",
        })

        # Active workset state
        resources.append({
            "uri": "workset://active",
            "name": "Active Workset State",
            "description": "Current workset activation state",
            "mimeType": "application/json",
        })

        # Agents (filtered by active workset)
        active_names = self.data.get_active_agent_names()
        for name in sorted(active_names):
            if name in self.data.agents:
                agent = self.data.agents[name]
                resources.append({
                    "uri": f"agent://{name}",
                    "name": f"Agent: {name}",
                    "description": agent.description[:100],
                    "mimeType": "text/markdown",
                })

        # Skills
        for name in sorted(self.data.skills.keys()):
            skill = self.data.skills[name]
            resources.append({
                "uri": f"skill://{name}",
                "name": f"Skill: {name}",
                "description": skill.description[:100],
                "mimeType": "text/markdown",
            })

        return {"resources": resources}

    def _handle_resources_read(self, params: dict) -> dict:
        uri = params.get("uri", "")

        if uri == "workset://system-prompt":
            content = compose_system_prompt(self.data)
            return {"contents": [{"uri": uri, "mimeType": "text/markdown", "text": content}]}

        if uri == "workset://active":
            active = self.data.get_active_workset()
            state = {
                "active_workset": active,
                "available_worksets": list(self.data.worksets.keys()),
                "total_agents": len(self.data.agents),
                "total_skills": len(self.data.skills),
            }
            if active:
                state["active_agents"] = len(self.data.get_workset_agents(active))
                state["active_skills"] = len(self.data.get_workset_skills(active))
            return {"contents": [{"uri": uri, "mimeType": "application/json",
                                  "text": json.dumps(state, indent=2)}]}

        if uri.startswith("agent://"):
            name = uri[8:]
            agent = self.data.agents.get(name)
            if agent:
                return {"contents": [{"uri": uri, "mimeType": "text/markdown", "text": agent.content}]}
            return {"contents": [{"uri": uri, "mimeType": "text/plain", "text": f"Agent '{name}' not found."}]}

        if uri.startswith("skill://"):
            name = uri[8:]
            skill = self.data.skills.get(name)
            if skill:
                return {"contents": [{"uri": uri, "mimeType": "text/markdown", "text": skill.content}]}
            return {"contents": [{"uri": uri, "mimeType": "text/plain", "text": f"Skill '{name}' not found."}]}

        return {"contents": [{"uri": uri, "mimeType": "text/plain", "text": "Unknown resource."}]}

    # ---- Prompts ----

    def _handle_prompts_list(self, params: dict) -> dict:
        self.data.reload_if_changed()
        prompts = []
        for name, skill in sorted(self.data.skills.items()):
            if skill.user_invocable:
                prompts.append({
                    "name": name,
                    "description": skill.description[:150] if skill.description else name,
                    "arguments": [
                        {
                            "name": "topic",
                            "description": "Topic or input for the skill",
                            "required": False,
                        }
                    ],
                })
        return {"prompts": prompts}

    def _handle_prompts_get(self, params: dict) -> dict:
        name = params.get("name", "")
        skill = self.data.skills.get(name)
        if not skill:
            return {"messages": [{"role": "user", "content": {"type": "text",
                                                               "text": f"Skill '{name}' not found."}}]}

        messages = [
            {"role": "user", "content": {"type": "text", "text": skill.content}},
        ]

        # Add topic argument if provided
        arguments = params.get("arguments", {})
        topic = arguments.get("topic", "")
        if topic:
            messages.append({"role": "user", "content": {"type": "text", "text": topic}})

        return {"messages": messages}


# =============================================================================
# Entry Point
# =============================================================================

def main():
    server = MCPServer()
    server.run()


if __name__ == "__main__":
    main()
