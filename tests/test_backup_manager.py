"""Tests for BackupManager: create, list, prune, get_backup."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from conftest import claude_sync

BackupManager = claude_sync.BackupManager
FileHasher = claude_sync.FileHasher


class TestBackupManagerCreate(unittest.TestCase):
    """Test BackupManager.create_backup."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.backup_dir = Path(self.tmpdir.name) / "backups"
        self.source_dir = Path(self.tmpdir.name) / "source"
        self.source_dir.mkdir()
        self.mgr = BackupManager(backup_dir=self.backup_dir)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_create_backup_returns_path(self):
        """create_backup returns the path to the new backup directory."""
        result = self.mgr.create_backup(self.source_dir)
        self.assertTrue(result.exists())
        self.assertTrue(result.is_dir())

    def test_create_backup_with_label(self):
        """When a label is provided, it's appended to the directory name."""
        result = self.mgr.create_backup(self.source_dir, label="pre-push")
        self.assertIn("pre-push", result.name)

    def test_create_backup_writes_metadata(self):
        """Backup directory contains .backup-meta.json."""
        result = self.mgr.create_backup(self.source_dir, label="test")
        meta_path = result / ".backup-meta.json"
        self.assertTrue(meta_path.exists())
        with open(meta_path) as f:
            meta = json.load(f)
        self.assertEqual(meta["label"], "test")
        self.assertIn("file_count", meta)
        self.assertIn("timestamp", meta)

    def test_create_backup_copies_syncable_files(self):
        """Backup copies only syncable files from the source directory."""
        rules_dir = self.source_dir / "rules"
        rules_dir.mkdir()
        (rules_dir / "myrule.md").write_text("rule content")
        # Also create a non-syncable file
        (self.source_dir / "random.txt").write_text("not synced")

        result = self.mgr.create_backup(self.source_dir)
        backed_up = result / "rules" / "myrule.md"
        self.assertTrue(backed_up.exists())
        self.assertEqual(backed_up.read_text(), "rule content")
        # Non-syncable file should NOT be backed up
        self.assertFalse((result / "random.txt").exists())

    def test_create_backup_from_empty_source(self):
        """Backup of empty directory creates dir with only metadata."""
        result = self.mgr.create_backup(self.source_dir)
        self.assertTrue(result.exists())
        meta = result / ".backup-meta.json"
        self.assertTrue(meta.exists())
        with open(meta) as f:
            data = json.load(f)
        self.assertEqual(data["file_count"], 0)

    def test_create_backup_from_nonexistent_source(self):
        """Backup of nonexistent source dir still creates dir with metadata."""
        fake_source = Path(self.tmpdir.name) / "nonexistent"
        result = self.mgr.create_backup(fake_source)
        self.assertTrue(result.exists())


class TestBackupManagerList(unittest.TestCase):
    """Test BackupManager.list_backups."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.backup_dir = Path(self.tmpdir.name) / "backups"
        self.source_dir = Path(self.tmpdir.name) / "source"
        self.source_dir.mkdir()
        self.mgr = BackupManager(backup_dir=self.backup_dir)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_list_empty_no_backups(self):
        """list_backups returns empty list when no backups exist."""
        self.assertEqual(self.mgr.list_backups(), [])

    def test_list_returns_created_backups(self):
        """list_backups returns entries for each created backup."""
        self.mgr.create_backup(self.source_dir, label="first")
        time.sleep(0.01)  # Ensure unique timestamp
        self.mgr.create_backup(self.source_dir, label="second")
        backups = self.mgr.list_backups()
        self.assertEqual(len(backups), 2)

    def test_list_ordered_newest_first(self):
        """list_backups returns newest backup first."""
        self.mgr.create_backup(self.source_dir, label="older")
        time.sleep(1.1)  # Ensure distinct timestamps
        self.mgr.create_backup(self.source_dir, label="newer")
        backups = self.mgr.list_backups()
        self.assertEqual(len(backups), 2)
        # Newest should be first
        self.assertIn("newer", backups[0]["name"])

    def test_list_includes_metadata(self):
        """Listed backups include metadata from .backup-meta.json."""
        self.mgr.create_backup(self.source_dir, label="withdata")
        backups = self.mgr.list_backups()
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0]["label"], "withdata")
        self.assertIn("path", backups[0])

    def test_list_handles_backup_without_meta(self):
        """Backups without .backup-meta.json still appear in list."""
        manual_dir = self.backup_dir / "20250101-120000-manual"
        manual_dir.mkdir(parents=True)
        backups = self.mgr.list_backups()
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0]["name"], "20250101-120000-manual")


class TestBackupManagerPrune(unittest.TestCase):
    """Test BackupManager.prune."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.backup_dir = Path(self.tmpdir.name) / "backups"
        self.source_dir = Path(self.tmpdir.name) / "source"
        self.source_dir.mkdir()
        self.mgr = BackupManager(backup_dir=self.backup_dir)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_prune_removes_oldest(self):
        """prune(keep=2) removes oldest backups beyond the 2 most recent."""
        for i in range(4):
            self.mgr.create_backup(self.source_dir, label=f"b{i}")
            time.sleep(1.1)  # Ensure unique timestamps
        self.assertEqual(len(self.mgr.list_backups()), 4)
        pruned = self.mgr.prune(keep=2)
        self.assertEqual(pruned, 2)
        self.assertEqual(len(self.mgr.list_backups()), 2)

    def test_prune_no_op_when_under_limit(self):
        """prune returns 0 when number of backups <= keep."""
        self.mgr.create_backup(self.source_dir)
        pruned = self.mgr.prune(keep=5)
        self.assertEqual(pruned, 0)

    def test_prune_empty_dir(self):
        """prune on no backups returns 0."""
        pruned = self.mgr.prune(keep=3)
        self.assertEqual(pruned, 0)

    def test_prune_keeps_newest(self):
        """After pruning, the newest backups are the ones retained."""
        for i in range(3):
            self.mgr.create_backup(self.source_dir, label=f"b{i}")
            time.sleep(1.1)
        self.mgr.prune(keep=1)
        remaining = self.mgr.list_backups()
        self.assertEqual(len(remaining), 1)
        self.assertIn("b2", remaining[0]["name"])


class TestBackupManagerGetBackup(unittest.TestCase):
    """Test BackupManager.get_backup (exact and partial match)."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.backup_dir = Path(self.tmpdir.name) / "backups"
        self.source_dir = Path(self.tmpdir.name) / "source"
        self.source_dir.mkdir()
        self.mgr = BackupManager(backup_dir=self.backup_dir)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_get_backup_exact_match(self):
        """get_backup finds backup by exact directory name."""
        bp = self.mgr.create_backup(self.source_dir, label="exacttest")
        result = self.mgr.get_backup(bp.name)
        self.assertIsNotNone(result)
        self.assertEqual(result.name, bp.name)

    def test_get_backup_partial_match(self):
        """get_backup finds backup by partial name match."""
        self.mgr.create_backup(self.source_dir, label="uniquelabel")
        result = self.mgr.get_backup("uniquelabel")
        self.assertIsNotNone(result)
        self.assertIn("uniquelabel", result.name)

    def test_get_backup_returns_none_for_missing(self):
        """get_backup returns None when no matching backup exists."""
        result = self.mgr.get_backup("nonexistent")
        self.assertIsNone(result)

    def test_get_backup_empty_backup_dir(self):
        """get_backup returns None when backup dir doesn't exist."""
        mgr = BackupManager(backup_dir=Path(self.tmpdir.name) / "nope")
        result = mgr.get_backup("anything")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
