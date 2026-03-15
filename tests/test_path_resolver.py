"""Tests for PathResolver: git root detection, path conversions."""

import os
import tempfile
import unittest
from pathlib import Path

from conftest import claude_sync

PathResolver = claude_sync.PathResolver


class TestPathResolverWithGitRoot(unittest.TestCase):
    """Test PathResolver when given an explicit repo_root."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmpdir.name) / "myrepo"
        self.repo_root.mkdir()
        # Create a fake .git directory so it looks like a repo
        (self.repo_root / ".git").mkdir()
        self.resolver = PathResolver(repo_root=self.repo_root)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_repo_root_is_resolved(self):
        """repo_root property returns the resolved path we passed in."""
        self.assertEqual(self.resolver.repo_root, self.repo_root.resolve())

    def test_repo_claude_path(self):
        """repo_claude is repo_root / 'claude'."""
        expected = self.repo_root.resolve() / "claude"
        self.assertEqual(self.resolver.repo_claude, expected)

    def test_manifest_path(self):
        """manifest_path is repo_root / 'manifest.json'."""
        expected = self.repo_root.resolve() / "manifest.json"
        self.assertEqual(self.resolver.manifest_path, expected)

    def test_home_claude_is_user_home(self):
        """home_claude always points to ~/.claude."""
        expected = Path.home() / ".claude"
        self.assertEqual(self.resolver.home_claude, expected)

    def test_home_to_relative(self):
        """Convert absolute home path to relative sync path."""
        home = self.resolver.home_claude
        abs_path = home / "rules" / "myrule.md"
        rel = self.resolver.home_to_relative(abs_path)
        self.assertEqual(rel, os.path.join("rules", "myrule.md"))

    def test_repo_to_relative(self):
        """Convert absolute repo path to relative sync path."""
        repo_claude = self.resolver.repo_claude
        abs_path = repo_claude / "agents" / "helper.md"
        rel = self.resolver.repo_to_relative(abs_path)
        self.assertEqual(rel, os.path.join("agents", "helper.md"))

    def test_relative_to_home(self):
        """Convert relative path to absolute home path."""
        result = self.resolver.relative_to_home("skills/search.md")
        expected = self.resolver.home_claude / "skills" / "search.md"
        self.assertEqual(result, expected)

    def test_relative_to_repo(self):
        """Convert relative path to absolute repo path."""
        result = self.resolver.relative_to_repo("hooks/pre-push.sh")
        expected = self.resolver.repo_claude / "hooks" / "pre-push.sh"
        self.assertEqual(result, expected)


class TestPathResolverAutoDetect(unittest.TestCase):
    """Test git root auto-detection by walking up from cwd."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmpdir.name) / "project"
        self.repo_root.mkdir()
        (self.repo_root / ".git").mkdir()
        # Create a nested subdirectory to test walk-up
        self.subdir = self.repo_root / "src" / "deep"
        self.subdir.mkdir(parents=True)
        self._orig_cwd = os.getcwd()

    def tearDown(self):
        os.chdir(self._orig_cwd)
        self.tmpdir.cleanup()

    def test_finds_git_root_from_subdirectory(self):
        """PathResolver finds .git when cwd is a nested subdirectory."""
        os.chdir(str(self.subdir))
        resolver = PathResolver()
        self.assertEqual(resolver.repo_root, self.repo_root.resolve())

    def test_no_git_root_returns_none(self):
        """When there's no .git anywhere above, repo_root is None."""
        # Use a temp dir with no .git
        with tempfile.TemporaryDirectory() as isolated:
            os.chdir(isolated)
            resolver = PathResolver()
            self.assertIsNone(resolver.repo_root)
            self.assertIsNone(resolver.repo_claude)
            self.assertIsNone(resolver.manifest_path)


class TestPathResolverEdgeCases(unittest.TestCase):
    """Edge cases for PathResolver."""

    def test_repo_root_with_symlink(self):
        """PathResolver resolves symlinked repo roots."""
        with tempfile.TemporaryDirectory() as tmpdir:
            real_repo = Path(tmpdir) / "real_repo"
            real_repo.mkdir()
            (real_repo / ".git").mkdir()
            link_path = Path(tmpdir) / "link_repo"
            link_path.symlink_to(real_repo)
            resolver = PathResolver(repo_root=link_path)
            # Should resolve to real path
            self.assertEqual(resolver.repo_root, real_repo.resolve())

    def test_relative_roundtrip_home(self):
        """home_to_relative and relative_to_home are inverses."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            (repo / ".git").mkdir()
            resolver = PathResolver(repo_root=repo)
            home = resolver.home_claude
            original = home / "agents" / "writer.md"
            rel = resolver.home_to_relative(original)
            roundtrip = resolver.relative_to_home(rel)
            self.assertEqual(roundtrip, original)


if __name__ == "__main__":
    unittest.main()
