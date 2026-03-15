"""Tests for FileHasher: SHA-256 hashing, exclusion logic, tree walking."""

import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from conftest import claude_sync

FileHasher = claude_sync.FileHasher
EXCLUDE_PATHS = claude_sync.EXCLUDE_PATHS
SYNC_PATHS = claude_sync.SYNC_PATHS
HASH_CHUNK_SIZE = claude_sync.HASH_CHUNK_SIZE


class TestHashFile(unittest.TestCase):
    """Test FileHasher.hash_file with real temporary files."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.base = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_hash_known_content(self):
        """Hash of known content matches manual SHA-256 computation."""
        content = b"hello world\n"
        filepath = self.base / "test.txt"
        filepath.write_bytes(content)
        expected = hashlib.sha256(content).hexdigest()
        self.assertEqual(FileHasher.hash_file(filepath), expected)

    def test_hash_empty_file(self):
        """Hash of empty file is SHA-256 of empty bytes."""
        filepath = self.base / "empty.txt"
        filepath.write_bytes(b"")
        expected = hashlib.sha256(b"").hexdigest()
        self.assertEqual(FileHasher.hash_file(filepath), expected)

    def test_hash_binary_content(self):
        """Hash works correctly on binary content."""
        content = bytes(range(256)) * 100
        filepath = self.base / "binary.bin"
        filepath.write_bytes(content)
        expected = hashlib.sha256(content).hexdigest()
        self.assertEqual(FileHasher.hash_file(filepath), expected)

    def test_hash_large_file_multiple_chunks(self):
        """Hash is correct for files larger than HASH_CHUNK_SIZE."""
        # Create a file larger than 64KB to exercise chunked reading
        content = b"A" * (HASH_CHUNK_SIZE + 1000)
        filepath = self.base / "large.txt"
        filepath.write_bytes(content)
        expected = hashlib.sha256(content).hexdigest()
        self.assertEqual(FileHasher.hash_file(filepath), expected)

    def test_different_content_different_hash(self):
        """Two files with different content produce different hashes."""
        f1 = self.base / "file1.txt"
        f2 = self.base / "file2.txt"
        f1.write_text("content A")
        f2.write_text("content B")
        self.assertNotEqual(FileHasher.hash_file(f1), FileHasher.hash_file(f2))

    def test_same_content_same_hash(self):
        """Two files with identical content produce the same hash."""
        f1 = self.base / "file1.txt"
        f2 = self.base / "file2.txt"
        f1.write_text("identical content")
        f2.write_text("identical content")
        self.assertEqual(FileHasher.hash_file(f1), FileHasher.hash_file(f2))


class TestShouldExclude(unittest.TestCase):
    """Test FileHasher.should_exclude with various paths."""

    def setUp(self):
        self.base = Path("/fake/base")

    def test_excludes_env_file(self):
        """.env is in EXCLUDE_PATHS and should be excluded."""
        self.assertTrue(FileHasher.should_exclude(self.base / ".env", self.base))

    def test_excludes_directory_path(self):
        """Files inside excluded directories (e.g. cache/) are excluded."""
        self.assertTrue(
            FileHasher.should_exclude(self.base / "cache" / "data.json", self.base)
        )

    def test_excludes_ds_store(self):
        """.DS_Store matches WALK_EXCLUDE_PATTERNS."""
        self.assertTrue(FileHasher.should_exclude(self.base / ".DS_Store", self.base))

    def test_excludes_pycache(self):
        """__pycache__ directory entries are excluded."""
        self.assertTrue(
            FileHasher.should_exclude(
                self.base / "__pycache__" / "mod.cpython-39.pyc", self.base
            )
        )

    def test_excludes_swap_files(self):
        """Vim swap files (*.swp) are excluded."""
        self.assertTrue(
            FileHasher.should_exclude(self.base / "rules" / ".file.swp", self.base)
        )

    def test_allows_syncable_file(self):
        """A file under rules/ that isn't in EXCLUDE_PATHS passes."""
        self.assertFalse(
            FileHasher.should_exclude(self.base / "rules" / "myrule.md", self.base)
        )

    def test_excludes_session_env(self):
        """session-env/ directory is excluded."""
        self.assertTrue(
            FileHasher.should_exclude(self.base / "session-env" / "data.txt", self.base)
        )

    def test_excludes_telemetry(self):
        """telemetry/ directory is excluded."""
        self.assertTrue(
            FileHasher.should_exclude(self.base / "telemetry" / "events.json", self.base)
        )


class TestIsSyncable(unittest.TestCase):
    """Test FileHasher.is_syncable path matching."""

    def setUp(self):
        self.base = Path("/fake/base")

    def test_claude_md_is_syncable(self):
        """CLAUDE.md is explicitly in SYNC_PATHS."""
        self.assertTrue(FileHasher.is_syncable(self.base / "CLAUDE.md", self.base))

    def test_settings_json_is_syncable(self):
        """settings.json is special-cased as syncable."""
        self.assertTrue(FileHasher.is_syncable(self.base / "settings.json", self.base))

    def test_file_in_agents_dir_is_syncable(self):
        """Files under agents/ are syncable."""
        self.assertTrue(
            FileHasher.is_syncable(self.base / "agents" / "helper.md", self.base)
        )

    def test_file_in_rules_dir_is_syncable(self):
        """Files under rules/ are syncable."""
        self.assertTrue(
            FileHasher.is_syncable(self.base / "rules" / "no-scaffold.md", self.base)
        )

    def test_file_in_skills_dir_is_syncable(self):
        """Files under skills/ are syncable."""
        self.assertTrue(
            FileHasher.is_syncable(self.base / "skills" / "commit.md", self.base)
        )

    def test_file_in_hooks_dir_is_syncable(self):
        """Files under hooks/ are syncable."""
        self.assertTrue(
            FileHasher.is_syncable(self.base / "hooks" / "pre-push.sh", self.base)
        )

    def test_random_file_not_syncable(self):
        """A file outside any SYNC_PATHS prefix is not syncable."""
        self.assertFalse(
            FileHasher.is_syncable(self.base / "random.txt", self.base)
        )

    def test_env_file_not_syncable(self):
        """.env is not under any sync path."""
        self.assertFalse(FileHasher.is_syncable(self.base / ".env", self.base))


class TestWalkDirectory(unittest.TestCase):
    """Test FileHasher.walk_directory with real temp directory trees."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.base = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_empty_directory(self):
        """walk_directory on empty dir returns empty dict."""
        self.assertEqual(FileHasher.walk_directory(self.base), {})

    def test_nonexistent_directory(self):
        """walk_directory on nonexistent path returns empty dict."""
        fake = self.base / "does_not_exist"
        self.assertEqual(FileHasher.walk_directory(fake), {})

    def test_finds_syncable_files(self):
        """walk_directory finds files under SYNC_PATHS prefixes."""
        rules_dir = self.base / "rules"
        rules_dir.mkdir()
        (rules_dir / "myrule.md").write_text("rule content")
        result = FileHasher.walk_directory(self.base)
        self.assertIn(os.path.join("rules", "myrule.md"), result)

    def test_excludes_non_syncable(self):
        """walk_directory ignores files outside SYNC_PATHS."""
        (self.base / "random.txt").write_text("not syncable")
        result = FileHasher.walk_directory(self.base)
        self.assertNotIn("random.txt", result)

    def test_excludes_excluded_dirs(self):
        """walk_directory skips EXCLUDE_PATHS directories like cache/."""
        cache_dir = self.base / "cache"
        cache_dir.mkdir()
        (cache_dir / "data.json").write_text("{}")
        result = FileHasher.walk_directory(self.base)
        self.assertNotIn(os.path.join("cache", "data.json"), result)

    def test_claude_md_at_root(self):
        """CLAUDE.md at root of base_dir is found."""
        (self.base / "CLAUDE.md").write_text("# Claude config")
        result = FileHasher.walk_directory(self.base)
        self.assertIn("CLAUDE.md", result)

    def test_settings_json_found(self):
        """settings.json is found by walk_directory."""
        (self.base / "settings.json").write_text('{"hooks": {}}')
        result = FileHasher.walk_directory(self.base)
        self.assertIn("settings.json", result)

    def test_hash_values_are_sha256(self):
        """Returned hash values are valid 64-char hex strings."""
        rules_dir = self.base / "rules"
        rules_dir.mkdir()
        (rules_dir / "test.md").write_text("content")
        result = FileHasher.walk_directory(self.base)
        for h in result.values():
            self.assertEqual(len(h), 64)
            # Verify it's valid hex
            int(h, 16)


if __name__ == "__main__":
    unittest.main()
