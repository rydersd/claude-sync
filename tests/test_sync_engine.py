"""Tests for SyncEngine: push/pull with real temp directories."""

import os
import stat
import tempfile
import unittest
from pathlib import Path

from conftest import claude_sync

SyncEngine = claude_sync.SyncEngine
PathResolver = claude_sync.PathResolver
DiffResult = claude_sync.DiffResult
FileChange = claude_sync.FileChange
Output = claude_sync.Output


class TestSyncEnginePush(unittest.TestCase):
    """Test SyncEngine.push copies files from home to repo."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmpdir.name) / "repo"
        self.repo_root.mkdir()
        (self.repo_root / ".git").mkdir()
        self.repo_claude = self.repo_root / "claude"
        self.repo_claude.mkdir()

        # Create a fake home_claude
        self.home_claude = Path(self.tmpdir.name) / "home_claude"
        self.home_claude.mkdir()

        # Set up PathResolver with custom paths
        self.resolver = PathResolver(repo_root=self.repo_root)
        # Override home_claude to use our temp dir
        self.resolver._home_claude = self.home_claude
        self.resolver._repo_claude = self.repo_claude

        self.output = Output(quiet=True)
        self.engine = SyncEngine(self.resolver, self.output)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_push_added_file(self):
        """Push copies a new file from home to repo."""
        src = self.home_claude / "rules" / "test.md"
        src.parent.mkdir(parents=True)
        src.write_text("rule content")

        diff = DiffResult(added=[FileChange("rules/test.md", "added")])
        count = self.engine.push(diff)
        self.assertEqual(count, 1)

        dst = self.repo_claude / "rules" / "test.md"
        self.assertTrue(dst.exists())
        self.assertEqual(dst.read_text(), "rule content")

    def test_push_modified_file(self):
        """Push overwrites a modified file in repo."""
        # Existing repo file
        dst = self.repo_claude / "CLAUDE.md"
        dst.write_text("old content")
        # Home file with new content
        src = self.home_claude / "CLAUDE.md"
        src.write_text("new content")

        diff = DiffResult(modified=[FileChange("CLAUDE.md", "modified")])
        count = self.engine.push(diff)
        self.assertEqual(count, 1)
        self.assertEqual(dst.read_text(), "new content")

    def test_push_deleted_file(self):
        """Push deletes files that no longer exist in home."""
        target = self.repo_claude / "rules" / "old.md"
        target.parent.mkdir(parents=True)
        target.write_text("to delete")

        diff = DiffResult(deleted=[FileChange("rules/old.md", "deleted")])
        count = self.engine.push(diff)
        self.assertEqual(count, 1)
        self.assertFalse(target.exists())

    def test_push_dry_run_does_not_copy(self):
        """Dry run counts files but doesn't actually copy."""
        src = self.home_claude / "CLAUDE.md"
        src.write_text("content")

        diff = DiffResult(added=[FileChange("CLAUDE.md", "added")])
        count = self.engine.push(diff, dry_run=True)
        self.assertEqual(count, 1)
        self.assertFalse((self.repo_claude / "CLAUDE.md").exists())

    def test_push_creates_parent_dirs(self):
        """Push creates intermediate directories as needed."""
        src = self.home_claude / "hooks" / "deep" / "nested" / "hook.sh"
        src.parent.mkdir(parents=True)
        src.write_text("#!/bin/bash")

        diff = DiffResult(added=[FileChange("hooks/deep/nested/hook.sh", "added")])
        count = self.engine.push(diff)
        self.assertEqual(count, 1)
        dst = self.repo_claude / "hooks" / "deep" / "nested" / "hook.sh"
        self.assertTrue(dst.exists())

    def test_push_cleans_empty_parent_dirs(self):
        """After deleting last file in a directory, empty dirs are removed."""
        target_dir = self.repo_claude / "rules" / "sub"
        target_dir.mkdir(parents=True)
        target_file = target_dir / "only.md"
        target_file.write_text("content")

        diff = DiffResult(deleted=[FileChange("rules/sub/only.md", "deleted")])
        self.engine.push(diff)
        # The 'sub' directory should be cleaned up
        self.assertFalse(target_dir.exists())


class TestSyncEnginePull(unittest.TestCase):
    """Test SyncEngine.pull copies files from repo to home."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmpdir.name) / "repo"
        self.repo_root.mkdir()
        (self.repo_root / ".git").mkdir()
        self.repo_claude = self.repo_root / "claude"
        self.repo_claude.mkdir()

        self.home_claude = Path(self.tmpdir.name) / "home_claude"
        self.home_claude.mkdir()

        self.resolver = PathResolver(repo_root=self.repo_root)
        self.resolver._home_claude = self.home_claude
        self.resolver._repo_claude = self.repo_claude

        self.output = Output(quiet=True)
        self.engine = SyncEngine(self.resolver, self.output)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_pull_added_file(self):
        """Pull copies a new file from repo to home."""
        src = self.repo_claude / "agents" / "writer.md"
        src.parent.mkdir(parents=True)
        src.write_text("agent config")

        diff = DiffResult(added=[FileChange("agents/writer.md", "added")])
        count = self.engine.pull(diff)
        self.assertEqual(count, 1)

        dst = self.home_claude / "agents" / "writer.md"
        self.assertTrue(dst.exists())
        self.assertEqual(dst.read_text(), "agent config")

    def test_pull_sets_executable_on_sh(self):
        """Pull sets +x permission on .sh files."""
        src = self.repo_claude / "hooks" / "pre-push.sh"
        src.parent.mkdir(parents=True)
        src.write_text("#!/bin/bash\necho test")

        diff = DiffResult(added=[FileChange("hooks/pre-push.sh", "added")])
        self.engine.pull(diff)

        dst = self.home_claude / "hooks" / "pre-push.sh"
        self.assertTrue(dst.exists())
        mode = dst.stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR, "User execute bit should be set")

    def test_pull_sets_executable_on_py(self):
        """Pull sets +x permission on .py files."""
        src = self.repo_claude / "scripts" / "helper.py"
        src.parent.mkdir(parents=True)
        src.write_text("#!/usr/bin/env python3\nprint('hi')")

        diff = DiffResult(added=[FileChange("scripts/helper.py", "added")])
        self.engine.pull(diff)

        dst = self.home_claude / "scripts" / "helper.py"
        mode = dst.stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR, "User execute bit should be set")

    def test_pull_dry_run_does_not_copy(self):
        """Dry run in pull doesn't actually copy files."""
        src = self.repo_claude / "CLAUDE.md"
        src.write_text("content")

        diff = DiffResult(added=[FileChange("CLAUDE.md", "added")])
        count = self.engine.pull(diff, dry_run=True)
        self.assertEqual(count, 1)
        self.assertFalse((self.home_claude / "CLAUDE.md").exists())

    def test_pull_deleted_file(self):
        """Pull deletes files from home that are no longer in repo."""
        target = self.home_claude / "rules" / "old.md"
        target.parent.mkdir(parents=True)
        target.write_text("old content")

        diff = DiffResult(deleted=[FileChange("rules/old.md", "deleted")])
        count = self.engine.pull(diff)
        self.assertEqual(count, 1)
        self.assertFalse(target.exists())

    def test_pull_multiple_changes(self):
        """Pull handles a mix of adds and modifications."""
        # Repo has new file and modified file
        (self.repo_claude / "CLAUDE.md").write_text("updated")
        agents_dir = self.repo_claude / "agents"
        agents_dir.mkdir()
        (agents_dir / "new.md").write_text("new agent")

        # Home has old CLAUDE.md
        (self.home_claude / "CLAUDE.md").write_text("original")

        diff = DiffResult(
            added=[FileChange("agents/new.md", "added")],
            modified=[FileChange("CLAUDE.md", "modified")],
        )
        count = self.engine.pull(diff)
        self.assertEqual(count, 2)
        self.assertEqual((self.home_claude / "CLAUDE.md").read_text(), "updated")
        self.assertTrue((self.home_claude / "agents" / "new.md").exists())


if __name__ == "__main__":
    unittest.main()
