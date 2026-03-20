"""Tests for SkillGenome: dependency resolution, health checking, trigger management."""

import json
import os
import tempfile
import textwrap
import unittest
from pathlib import Path

from conftest import claude_sync

SkillDependencies = claude_sync.SkillDependencies
SkillGenome = claude_sync.SkillGenome
DependencyNode = claude_sync.DependencyNode
HealthIssue = claude_sync.HealthIssue
SkillGenomeEngine = claude_sync.SkillGenomeEngine


# ---------------------------------------------------------------------------
# Dataclass unit tests
# ---------------------------------------------------------------------------


class TestSkillDependencies(unittest.TestCase):
    """Test SkillDependencies dataclass behavior."""

    def test_empty_has_any_false(self):
        """has_any returns False when all lists are empty."""
        deps = SkillDependencies(skills=[], agents=[], mcp_servers=[], rules=[])
        self.assertFalse(deps.has_any)

    def test_has_any_with_skills(self):
        """has_any returns True when at least one list has entries."""
        deps = SkillDependencies(
            skills=["commit"], agents=[], mcp_servers=[], rules=[]
        )
        self.assertTrue(deps.has_any)

    def test_to_dict(self):
        """to_dict produces a plain dict with all four keys."""
        deps = SkillDependencies(
            skills=["a"], agents=["b"], mcp_servers=["c"], rules=["d"]
        )
        d = deps.to_dict()
        self.assertEqual(d["skills"], ["a"])
        self.assertEqual(d["agents"], ["b"])
        self.assertEqual(d["mcp_servers"], ["c"])
        self.assertEqual(d["rules"], ["d"])


class TestSkillGenome(unittest.TestCase):
    """Test SkillGenome dataclass serialization."""

    def test_to_dict_basic(self):
        """to_dict includes all core fields."""
        genome = SkillGenome(
            name="commit",
            description="Git commit helper",
            version="1.0",
            user_invocable=True,
            allowed_tools=["Bash", "Read"],
            requires=SkillDependencies(
                skills=[], agents=[], mcp_servers=[], rules=[]
            ),
            triggers=None,
            path="skills/commit/SKILL.md",
        )
        d = genome.to_dict()
        self.assertEqual(d["name"], "commit")
        self.assertEqual(d["description"], "Git commit helper")
        self.assertEqual(d["version"], "1.0")
        self.assertTrue(d["user_invocable"])
        self.assertEqual(d["allowed_tools"], ["Bash", "Read"])
        self.assertNotIn("triggers", d)

    def test_to_dict_with_triggers(self):
        """to_dict includes triggers when present."""
        triggers = {"patterns": ["/commit"], "description": "Commit changes"}
        genome = SkillGenome(
            name="commit",
            description="Git commit helper",
            version="1.0",
            user_invocable=True,
            allowed_tools=[],
            requires=SkillDependencies(
                skills=[], agents=[], mcp_servers=[], rules=[]
            ),
            triggers=triggers,
            path="skills/commit/SKILL.md",
        )
        d = genome.to_dict()
        self.assertEqual(d["triggers"]["patterns"], ["/commit"])


class TestHealthIssue(unittest.TestCase):
    """Test HealthIssue dataclass serialization."""

    def test_to_dict(self):
        """to_dict produces expected structure with all fields."""
        issue = HealthIssue(
            skill_name="commit",
            issue_type="missing_dependency",
            message="Agent 'git-expert' not found",
            severity="error",
            remediation="Install the git-expert agent",
        )
        d = issue.to_dict()
        self.assertEqual(d["skill_name"], "commit")
        self.assertEqual(d["issue_type"], "missing_dependency")
        self.assertEqual(d["message"], "Agent 'git-expert' not found")
        self.assertEqual(d["severity"], "error")
        self.assertEqual(d["remediation"], "Install the git-expert agent")


# ---------------------------------------------------------------------------
# SkillGenomeEngine tests
# ---------------------------------------------------------------------------


def _write_skill_md(skill_dir: Path, content: str) -> None:
    """Helper: write a SKILL.md file inside a skill directory."""
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(textwrap.dedent(content))


def _make_engine(home_dir: Path, repo_dir: Path = None) -> "SkillGenomeEngine":
    """Helper: create a SkillGenomeEngine with temp directories."""
    return SkillGenomeEngine(home_dir=home_dir, repo_dir=repo_dir)


class TestParseInlineList(unittest.TestCase):
    """Test SkillGenomeEngine._parse_inline_list static method."""

    def test_parse_inline_list_basic(self):
        """Bracket list with multiple items parsed correctly."""
        result = SkillGenomeEngine._parse_inline_list("[a, b, c]")
        self.assertEqual(result, ["a", "b", "c"])

    def test_parse_inline_list_empty(self):
        """Empty brackets produce empty list."""
        result = SkillGenomeEngine._parse_inline_list("[]")
        self.assertEqual(result, [])

    def test_parse_inline_list_single(self):
        """Bare value (no brackets) produces single-element list."""
        result = SkillGenomeEngine._parse_inline_list("foo")
        self.assertEqual(result, ["foo"])


class TestParseGenomeFrontmatter(unittest.TestCase):
    """Test SkillGenomeEngine._parse_genome_frontmatter."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tmpdir.name) / "home"
        self.home.mkdir()
        self.engine = _make_engine(self.home)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_parse_genome_frontmatter_simple(self):
        """Frontmatter without requires block is parsed correctly."""
        content = textwrap.dedent("""\
            ---
            name: commit
            description: Git commit helper
            version: 1.0
            user-invocable: true
            allowed-tools: [Bash, Read]
            ---
            # Commit Skill
            Body content here.
        """)
        fm = self.engine._parse_genome_frontmatter(content)
        self.assertEqual(fm["name"], "commit")
        self.assertEqual(fm["description"], "Git commit helper")
        self.assertEqual(fm["version"], "1.0")
        self.assertEqual(fm["user-invocable"], "true")
        self.assertEqual(fm["allowed-tools"], ["Bash", "Read"])

    def test_parse_genome_frontmatter_with_requires(self):
        """Frontmatter with nested requires block parses all sub-fields."""
        content = textwrap.dedent("""\
            ---
            name: deploy
            description: Deploy helper
            requires:
              skills: [commit, review]
              agents: [git-expert]
              mcp-servers: [morph]
              rules: [no-scaffolding]
            ---
            # Deploy Skill
        """)
        fm = self.engine._parse_genome_frontmatter(content)
        self.assertEqual(fm["name"], "deploy")
        self.assertIn("requires", fm)
        req = fm["requires"]
        self.assertEqual(req["skills"], ["commit", "review"])
        self.assertEqual(req["agents"], ["git-expert"])
        self.assertEqual(req["mcp-servers"], ["morph"])
        self.assertEqual(req["rules"], ["no-scaffolding"])

    def test_parse_genome_frontmatter_no_frontmatter(self):
        """Content without frontmatter delimiters returns empty dict."""
        content = "# Just a heading\nNo frontmatter here."
        fm = self.engine._parse_genome_frontmatter(content)
        self.assertEqual(fm, {})

    def test_parse_genome_frontmatter_colon_in_description(self):
        """Description containing a colon does not break parsing."""
        content = textwrap.dedent("""\
            ---
            name: test-skill
            description: This skill does: many things at once
            ---
            Body.
        """)
        fm = self.engine._parse_genome_frontmatter(content)
        self.assertEqual(fm["description"], "This skill does: many things at once")


class TestParseGenome(unittest.TestCase):
    """Test SkillGenomeEngine.parse_genome from actual skill directories."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tmpdir.name) / "home"
        self.home.mkdir()
        self.engine = _make_engine(self.home)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_parse_genome_basic(self):
        """Minimal SKILL.md produces a SkillGenome with sensible defaults."""
        skill_dir = self.home / "skills" / "basic"
        _write_skill_md(
            skill_dir,
            """\
            ---
            name: basic
            description: A basic skill
            ---
            # Basic Skill
            """,
        )
        genome = self.engine.parse_genome(skill_dir)
        self.assertIsNotNone(genome)
        self.assertEqual(genome.name, "basic")
        self.assertEqual(genome.description, "A basic skill")
        self.assertFalse(genome.requires.has_any)

    def test_parse_genome_full(self):
        """SKILL.md with all fields including requires produces correct SkillGenome."""
        skill_dir = self.home / "skills" / "full"
        _write_skill_md(
            skill_dir,
            """\
            ---
            name: full-skill
            description: Full-featured skill
            version: 2.0
            user_invocable: true
            allowed-tools: [Bash, Read, Edit]
            requires:
              skills: [commit]
              agents: [git-expert]
              mcp-servers: []
              rules: [no-scaffolding, observe-before-editing]
            ---
            # Full Skill
            Lots of content.
            """,
        )
        genome = self.engine.parse_genome(skill_dir)
        self.assertIsNotNone(genome)
        self.assertEqual(genome.name, "full-skill")
        self.assertEqual(genome.version, "2.0")
        self.assertTrue(genome.user_invocable)
        self.assertEqual(genome.allowed_tools, ["Bash", "Read", "Edit"])
        self.assertEqual(genome.requires.skills, ["commit"])
        self.assertEqual(genome.requires.agents, ["git-expert"])
        self.assertEqual(genome.requires.mcp_servers, [])
        self.assertEqual(
            genome.requires.rules, ["no-scaffolding", "observe-before-editing"]
        )

    def test_parse_genome_no_skillmd(self):
        """Directory without SKILL.md returns None."""
        skill_dir = self.home / "skills" / "empty"
        skill_dir.mkdir(parents=True)
        genome = self.engine.parse_genome(skill_dir)
        self.assertIsNone(genome)

    def test_scan_all(self):
        """scan_all discovers all skill directories with SKILL.md files."""
        skills_dir = self.home / "skills"
        for name in ["alpha", "beta", "gamma"]:
            _write_skill_md(
                skills_dir / name,
                f"""\
                ---
                name: {name}
                description: Skill {name}
                ---
                # {name}
                """,
            )
        # Add a directory without SKILL.md -- should be skipped
        (skills_dir / "no-skill").mkdir(parents=True)

        genomes = self.engine.scan_all(self.home)
        names = {g.name for g in genomes.values()}
        self.assertEqual(names, {"alpha", "beta", "gamma"})
        self.assertEqual(len(genomes), 3)


class TestResolveDependencies(unittest.TestCase):
    """Test SkillGenomeEngine.resolve_dependencies topological sort."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tmpdir.name) / "home"
        self.home.mkdir()
        self.engine = _make_engine(self.home)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _genome(self, name: str, skill_deps: list = None) -> "SkillGenome":
        """Helper: create a SkillGenome with specified skill dependencies."""
        return SkillGenome(
            name=name,
            description=f"Skill {name}",
            version="1.0",
            user_invocable=False,
            allowed_tools=[],
            requires=SkillDependencies(
                skills=skill_deps or [],
                agents=[],
                mcp_servers=[],
                rules=[],
            ),
            triggers=None,
            path=f"skills/{name}/SKILL.md",
        )

    def test_resolve_no_deps(self):
        """Skill with no dependencies resolves to just itself."""
        genomes = {"solo": self._genome("solo")}
        order, cycles = self.engine.resolve_dependencies("solo", genomes)
        self.assertEqual(order, ["solo"])
        self.assertEqual(cycles, [])

    def test_resolve_chain(self):
        """Linear chain A->B->C resolves to [C, B, A]."""
        genomes = {
            "A": self._genome("A", ["B"]),
            "B": self._genome("B", ["C"]),
            "C": self._genome("C"),
        }
        order, cycles = self.engine.resolve_dependencies("A", genomes)
        self.assertEqual(cycles, [])
        # C must come before B, B before A
        self.assertLess(order.index("C"), order.index("B"))
        self.assertLess(order.index("B"), order.index("A"))

    def test_resolve_diamond(self):
        """Diamond A->(B,C), B->D, C->D resolves with D first, A last."""
        genomes = {
            "A": self._genome("A", ["B", "C"]),
            "B": self._genome("B", ["D"]),
            "C": self._genome("C", ["D"]),
            "D": self._genome("D"),
        }
        order, cycles = self.engine.resolve_dependencies("A", genomes)
        self.assertEqual(cycles, [])
        # D must appear before B and C; A must be last
        self.assertLess(order.index("D"), order.index("B"))
        self.assertLess(order.index("D"), order.index("C"))
        self.assertEqual(order[-1], "A")

    def test_resolve_cycle(self):
        """Circular dependency A->B->A is detected."""
        genomes = {
            "A": self._genome("A", ["B"]),
            "B": self._genome("B", ["A"]),
        }
        order, cycles = self.engine.resolve_dependencies("A", genomes)
        self.assertTrue(len(cycles) > 0)

    def test_resolve_missing_dep(self):
        """Missing dependency (B not in genomes) does not crash; A still resolves."""
        genomes = {
            "A": self._genome("A", ["B"]),
        }
        order, cycles = self.engine.resolve_dependencies("A", genomes)
        # A should still appear; B is just absent from genomes
        self.assertIn("A", order)


class TestBuildFullGraph(unittest.TestCase):
    """Test SkillGenomeEngine.build_full_graph cross-type dependency graph."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tmpdir.name) / "home"
        self.home.mkdir()
        self.engine = _make_engine(self.home)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_build_full_graph_includes_all_types(self):
        """Graph nodes include skills, agents, rules, and mcp_servers."""
        genome = SkillGenome(
            name="deploy",
            description="Deploy",
            version="1.0",
            user_invocable=False,
            allowed_tools=[],
            requires=SkillDependencies(
                skills=["commit"],
                agents=["git-expert"],
                mcp_servers=["morph"],
                rules=["no-scaffolding"],
            ),
            triggers=None,
            path="skills/deploy/SKILL.md",
        )
        genomes = {"deploy": genome}
        graph = self.engine.build_full_graph(genomes)
        # The graph should contain DependencyNode entries
        self.assertIsInstance(graph, dict)
        self.assertIn("skill:deploy", graph)


class TestCheckHealth(unittest.TestCase):
    """Test SkillGenomeEngine.check_health for dependency validation."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tmpdir.name) / "home"
        self.home.mkdir()
        self.engine = _make_engine(self.home)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _genome(self, name: str, skill_deps: list = None) -> "SkillGenome":
        """Helper: create a SkillGenome with specified skill dependencies."""
        return SkillGenome(
            name=name,
            description=f"Skill {name}",
            version="1.0",
            user_invocable=False,
            allowed_tools=[],
            requires=SkillDependencies(
                skills=skill_deps or [],
                agents=[],
                mcp_servers=[],
                rules=[],
            ),
            triggers=None,
            path=f"skills/{name}/SKILL.md",
        )

    def test_health_all_good(self):
        """No issues when all skill dependencies are present."""
        genomes = {
            "A": self._genome("A", ["B"]),
            "B": self._genome("B"),
        }
        issues = self.engine.check_health(genomes)
        # Filter to only skill-dependency issues
        skill_issues = [i for i in issues if i.issue_type == "missing_dependency"]
        self.assertEqual(len(skill_issues), 0)

    def test_health_missing_skill(self):
        """Reports missing skill dependency."""
        genomes = {
            "A": self._genome("A", ["nonexistent"]),
        }
        issues = self.engine.check_health(genomes)
        missing = [i for i in issues if i.issue_type == "missing_skill"]
        self.assertTrue(len(missing) > 0)
        self.assertIn("nonexistent", missing[0].message)

    def test_health_circular(self):
        """Reports circular dependency."""
        genomes = {
            "A": self._genome("A", ["B"]),
            "B": self._genome("B", ["A"]),
        }
        issues = self.engine.check_health(genomes)
        circular = [i for i in issues if i.issue_type == "circular_dependency"]
        self.assertTrue(len(circular) > 0)


class TestTriggerExtraction(unittest.TestCase):
    """Test SkillGenomeEngine.extract_triggers and assemble_triggers."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tmpdir.name) / "home"
        self.home.mkdir()
        self.skills_dir = self.home / "skills"
        self.skills_dir.mkdir()
        self.engine = _make_engine(self.home)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _make_skill_rules(self, data: dict) -> Path:
        """Helper: write a skill-rules.json file and return its path."""
        path = self.skills_dir / "skill-rules.json"
        with open(path, "w") as f:
            json.dump(data, f)
        return path

    def test_extract_triggers(self):
        """extract_triggers splits monolith into per-skill trigger files."""
        rules_data = {
            "skills": {
                "commit": {
                    "patterns": ["/commit"],
                    "description": "Commit changes",
                },
                "review": {
                    "patterns": ["/review"],
                    "description": "Review code",
                },
            }
        }
        rules_path = self._make_skill_rules(rules_data)
        # Create skill directories for the triggers to land in
        (self.skills_dir / "commit").mkdir()
        (self.skills_dir / "review").mkdir()

        self.engine.extract_triggers(rules_path)

        # Per-skill trigger files should now exist
        commit_triggers = self.skills_dir / "commit" / "triggers.json"
        review_triggers = self.skills_dir / "review" / "triggers.json"
        self.assertTrue(commit_triggers.exists())
        self.assertTrue(review_triggers.exists())

        with open(commit_triggers) as f:
            ct = json.load(f)
        self.assertEqual(ct["patterns"], ["/commit"])

    def test_assemble_triggers(self):
        """assemble_triggers rebuilds JSON from per-skill trigger files."""
        # Create per-skill trigger files
        commit_dir = self.skills_dir / "commit"
        commit_dir.mkdir()
        with open(commit_dir / "triggers.json", "w") as f:
            json.dump({"patterns": ["/commit"], "description": "Commit"}, f)

        review_dir = self.skills_dir / "review"
        review_dir.mkdir()
        with open(review_dir / "triggers.json", "w") as f:
            json.dump({"patterns": ["/review"], "description": "Review"}, f)

        assembled = self.engine.assemble_triggers(self.skills_dir)
        self.assertIn("skills", assembled)
        self.assertIn("commit", assembled["skills"])
        self.assertIn("review", assembled["skills"])
        self.assertEqual(
            assembled["skills"]["commit"]["patterns"], ["/commit"]
        )

    def test_extract_assemble_roundtrip(self):
        """Extract then assemble produces output matching input (skills section)."""
        original = {
            "skills": {
                "alpha": {"patterns": ["/alpha"], "description": "Alpha skill"},
                "beta": {"patterns": ["/beta", "/b"], "description": "Beta"},
            }
        }
        rules_path = self._make_skill_rules(original)
        (self.skills_dir / "alpha").mkdir()
        (self.skills_dir / "beta").mkdir()

        self.engine.extract_triggers(rules_path)
        assembled = self.engine.assemble_triggers(self.skills_dir)

        self.assertEqual(
            assembled["skills"]["alpha"], original["skills"]["alpha"]
        )
        self.assertEqual(
            assembled["skills"]["beta"], original["skills"]["beta"]
        )

    def test_has_atomized_triggers_true(self):
        """has_atomized_triggers returns True when triggers.json files exist."""
        skill_dir = self.skills_dir / "commit"
        skill_dir.mkdir()
        with open(skill_dir / "triggers.json", "w") as f:
            json.dump({"patterns": ["/commit"]}, f)

        self.assertTrue(self.engine.has_atomized_triggers(self.skills_dir))

    def test_has_atomized_triggers_false(self):
        """has_atomized_triggers returns False when no triggers.json files exist."""
        skill_dir = self.skills_dir / "commit"
        skill_dir.mkdir()
        # No triggers.json created
        self.assertFalse(self.engine.has_atomized_triggers(self.skills_dir))


class TestInstallSkill(unittest.TestCase):
    """Test SkillGenomeEngine.install_skill copying between directories."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tmpdir.name) / "home"
        self.repo = Path(self.tmpdir.name) / "repo"
        self.home.mkdir()
        self.repo.mkdir()
        self.engine = _make_engine(self.home, self.repo)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _genome(self, name: str, skill_deps: list = None) -> "SkillGenome":
        """Helper: create a SkillGenome with specified skill dependencies."""
        return SkillGenome(
            name=name,
            description=f"Skill {name}",
            version="1.0",
            user_invocable=False,
            allowed_tools=[],
            requires=SkillDependencies(
                skills=skill_deps or [],
                agents=[],
                mcp_servers=[],
                rules=[],
            ),
            triggers=None,
            path=f"skills/{name}/SKILL.md",
        )

    def test_install_basic(self):
        """install_skill copies skill directory from repo to home."""
        # Create skill in repo
        repo_skill = self.repo / "skills" / "commit"
        _write_skill_md(
            repo_skill,
            """\
            ---
            name: commit
            description: Commit helper
            ---
            # Commit
            """,
        )

        genomes = {"commit": self._genome("commit")}
        self.engine.install_skill(
            "commit", self.repo, self.home, genomes
        )

        installed = self.home / "skills" / "commit" / "SKILL.md"
        self.assertTrue(installed.exists())

    def test_install_with_deps(self):
        """install_skill copies skill and its dependencies."""
        # Create skills in repo
        for name in ["deploy", "commit"]:
            _write_skill_md(
                self.repo / "skills" / name,
                f"""\
                ---
                name: {name}
                description: {name} skill
                ---
                # {name}
                """,
            )

        genomes = {
            "deploy": self._genome("deploy", ["commit"]),
            "commit": self._genome("commit"),
        }
        self.engine.install_skill(
            "deploy", self.repo, self.home, genomes
        )

        self.assertTrue(
            (self.home / "skills" / "deploy" / "SKILL.md").exists()
        )
        self.assertTrue(
            (self.home / "skills" / "commit" / "SKILL.md").exists()
        )

    def test_install_missing_in_repo(self):
        """install_skill raises FileNotFoundError when skill dir is missing."""
        genomes = {"ghost": self._genome("ghost")}
        with self.assertRaises(FileNotFoundError):
            self.engine.install_skill(
                "ghost", self.repo, self.home, genomes
            )


class TestFormatTree(unittest.TestCase):
    """Test SkillGenomeEngine.format_tree output."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tmpdir.name) / "home"
        self.home.mkdir()
        self.engine = _make_engine(self.home)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _genome(self, name: str, skill_deps: list = None) -> "SkillGenome":
        """Helper: create a SkillGenome with specified skill dependencies."""
        return SkillGenome(
            name=name,
            description=f"Skill {name}",
            version="1.0",
            user_invocable=False,
            allowed_tools=[],
            requires=SkillDependencies(
                skills=skill_deps or [],
                agents=[],
                mcp_servers=[],
                rules=[],
            ),
            triggers=None,
            path=f"skills/{name}/SKILL.md",
        )

    def test_format_tree_basic(self):
        """format_tree produces text with the skill name and its dependencies."""
        genomes = {
            "deploy": self._genome("deploy", ["commit", "review"]),
            "commit": self._genome("commit"),
            "review": self._genome("review"),
        }
        tree = self.engine.format_tree("deploy", genomes)
        self.assertIsInstance(tree, list)
        tree_text = "\n".join(tree)
        self.assertIn("deploy", tree_text)
        self.assertIn("commit", tree_text)
        self.assertIn("review", tree_text)


if __name__ == "__main__":
    unittest.main()
