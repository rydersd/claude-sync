"""Tests for DiffEngine: set-based hash comparison for push/pull."""

import unittest

from conftest import claude_sync

DiffEngine = claude_sync.DiffEngine
DiffResult = claude_sync.DiffResult
FileChange = claude_sync.FileChange


class TestDiffEnginePush(unittest.TestCase):
    """Test DiffEngine.compare in push direction (home -> repo)."""

    def test_identical_trees_no_changes(self):
        """When home and repo hashes are identical, no changes reported."""
        hashes = {"CLAUDE.md": "abc123", "rules/r.md": "def456"}
        result = DiffEngine.compare(hashes, dict(hashes), direction="push")
        self.assertFalse(result.has_changes)
        self.assertEqual(result.total_changes, 0)

    def test_new_file_in_home_is_added(self):
        """A file in home but not in repo shows as added."""
        home = {"CLAUDE.md": "abc", "rules/new.md": "xyz"}
        repo = {"CLAUDE.md": "abc"}
        result = DiffEngine.compare(home, repo, direction="push")
        self.assertEqual(len(result.added), 1)
        self.assertEqual(result.added[0].path, "rules/new.md")
        self.assertEqual(result.added[0].change_type, "added")

    def test_modified_file_detected(self):
        """A file with different hashes shows as modified."""
        home = {"CLAUDE.md": "new_hash"}
        repo = {"CLAUDE.md": "old_hash"}
        result = DiffEngine.compare(home, repo, direction="push")
        self.assertEqual(len(result.modified), 1)
        self.assertEqual(result.modified[0].path, "CLAUDE.md")
        self.assertEqual(result.modified[0].home_hash, "new_hash")
        self.assertEqual(result.modified[0].repo_hash, "old_hash")

    def test_deleted_file_in_repo(self):
        """A file in repo but not in home shows as deleted (push removes it)."""
        home = {}
        repo = {"rules/old.md": "abc"}
        result = DiffEngine.compare(home, repo, direction="push")
        self.assertEqual(len(result.deleted), 1)
        self.assertEqual(result.deleted[0].path, "rules/old.md")

    def test_mixed_changes(self):
        """Combination of added, modified, and deleted files."""
        home = {
            "CLAUDE.md": "changed",
            "rules/new.md": "new_file",
        }
        repo = {
            "CLAUDE.md": "original",
            "agents/old.md": "to_delete",
        }
        result = DiffEngine.compare(home, repo, direction="push")
        self.assertEqual(len(result.added), 1)  # rules/new.md
        self.assertEqual(len(result.modified), 1)  # CLAUDE.md
        self.assertEqual(len(result.deleted), 1)  # agents/old.md
        self.assertEqual(result.total_changes, 3)
        self.assertTrue(result.has_changes)

    def test_empty_trees(self):
        """Both empty trees yields no changes."""
        result = DiffEngine.compare({}, {}, direction="push")
        self.assertFalse(result.has_changes)

    def test_changes_are_sorted(self):
        """Added, modified, and deleted lists are sorted by path."""
        home = {"b.md": "x", "a.md": "y", "c.md": "z"}
        repo = {}
        result = DiffEngine.compare(home, repo, direction="push")
        paths = [c.path for c in result.added]
        self.assertEqual(paths, sorted(paths))


class TestDiffEnginePull(unittest.TestCase):
    """Test DiffEngine.compare in pull direction (repo -> home)."""

    def test_new_file_in_repo_is_added(self):
        """A file in repo but not home shows as added in pull direction."""
        home = {}
        repo = {"rules/new.md": "abc"}
        result = DiffEngine.compare(home, repo, direction="pull")
        self.assertEqual(len(result.added), 1)
        self.assertEqual(result.added[0].path, "rules/new.md")

    def test_deleted_file_from_repo_perspective(self):
        """A file in home but not repo shows as deleted in pull direction."""
        home = {"CLAUDE.md": "abc"}
        repo = {}
        result = DiffEngine.compare(home, repo, direction="pull")
        self.assertEqual(len(result.deleted), 1)
        self.assertEqual(result.deleted[0].path, "CLAUDE.md")

    def test_pull_modified_preserves_hashes(self):
        """Modified files in pull retain correct home_hash and repo_hash."""
        home = {"CLAUDE.md": "home_ver"}
        repo = {"CLAUDE.md": "repo_ver"}
        result = DiffEngine.compare(home, repo, direction="pull")
        self.assertEqual(len(result.modified), 1)
        change = result.modified[0]
        self.assertEqual(change.home_hash, "home_ver")
        self.assertEqual(change.repo_hash, "repo_ver")

    def test_pull_symmetric_with_push(self):
        """Push and pull on same data should produce mirrored results."""
        home = {"a.md": "h1", "b.md": "h2"}
        repo = {"b.md": "h2", "c.md": "h3"}
        push = DiffEngine.compare(home, repo, direction="push")
        pull = DiffEngine.compare(home, repo, direction="pull")
        # In push: a.md is added (in home, not repo). In pull: a.md is deleted.
        push_added_paths = {c.path for c in push.added}
        pull_deleted_paths = {c.path for c in pull.deleted}
        self.assertEqual(push_added_paths, pull_deleted_paths)


class TestDiffResultMethods(unittest.TestCase):
    """Test DiffResult dataclass utility methods."""

    def test_all_changes_combines_lists(self):
        """all_changes() returns added + modified + deleted."""
        result = DiffResult(
            added=[FileChange("a", "added")],
            modified=[FileChange("b", "modified")],
            deleted=[FileChange("c", "deleted")],
        )
        all_paths = [c.path for c in result.all_changes()]
        self.assertEqual(all_paths, ["a", "b", "c"])

    def test_to_dict_structure(self):
        """to_dict() produces expected JSON-serializable structure."""
        result = DiffResult(
            added=[FileChange("a", "added", home_hash="h1")],
        )
        d = result.to_dict()
        self.assertIn("added", d)
        self.assertIn("modified", d)
        self.assertIn("deleted", d)
        self.assertEqual(d["total_changes"], 1)
        self.assertEqual(d["added"][0]["path"], "a")


if __name__ == "__main__":
    unittest.main()
