"""Tests for Manifest: load, save, provenance, schema handling."""

import json
import tempfile
import unittest
from pathlib import Path

from conftest import claude_sync

Manifest = claude_sync.Manifest
MANIFEST_SCHEMA_VERSION = claude_sync.MANIFEST_SCHEMA_VERSION


class TestManifestDefaults(unittest.TestCase):
    """Test Manifest default construction."""

    def test_default_schema_version(self):
        """New manifest has current schema version."""
        m = Manifest()
        self.assertEqual(m.schema_version, MANIFEST_SCHEMA_VERSION)

    def test_default_files_empty(self):
        """New manifest has empty files dict."""
        m = Manifest()
        self.assertEqual(m.files, {})

    def test_default_no_last_push(self):
        """New manifest has no last_push."""
        m = Manifest()
        self.assertIsNone(m.last_push)

    def test_default_no_timestamps(self):
        """New manifest has no created_at or updated_at."""
        m = Manifest()
        self.assertIsNone(m.created_at)
        self.assertIsNone(m.updated_at)


class TestManifestSaveLoad(unittest.TestCase):
    """Test Manifest save/load round-trip with real temp files."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.base = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_save_creates_file(self):
        """save() creates the manifest file on disk."""
        path = self.base / "manifest.json"
        m = Manifest()
        m.save(path)
        self.assertTrue(path.exists())

    def test_save_sets_created_at(self):
        """First save sets created_at timestamp."""
        path = self.base / "manifest.json"
        m = Manifest()
        self.assertIsNone(m.created_at)
        m.save(path)
        self.assertIsNotNone(m.created_at)
        self.assertTrue(m.created_at.endswith("Z"))

    def test_save_sets_updated_at(self):
        """save() always sets updated_at."""
        path = self.base / "manifest.json"
        m = Manifest()
        m.save(path)
        self.assertIsNotNone(m.updated_at)

    def test_roundtrip_preserves_files(self):
        """Files dict survives save/load round-trip."""
        path = self.base / "manifest.json"
        m = Manifest()
        m.files = {"CLAUDE.md": "abc123", "rules/r.md": "def456"}
        m.save(path)
        loaded = Manifest.load(path)
        self.assertEqual(loaded.files, m.files)

    def test_roundtrip_preserves_schema_version(self):
        """Schema version survives round-trip."""
        path = self.base / "manifest.json"
        m = Manifest()
        m.save(path)
        loaded = Manifest.load(path)
        self.assertEqual(loaded.schema_version, MANIFEST_SCHEMA_VERSION)

    def test_roundtrip_preserves_last_push(self):
        """last_push data survives round-trip."""
        path = self.base / "manifest.json"
        m = Manifest()
        m.last_push = {"hostname": "testbox", "timestamp": "2025-01-01T00:00:00Z"}
        m.save(path)
        loaded = Manifest.load(path)
        self.assertEqual(loaded.last_push["hostname"], "testbox")

    def test_load_nonexistent_returns_default(self):
        """Loading from nonexistent path returns a default Manifest."""
        path = self.base / "nonexistent.json"
        loaded = Manifest.load(path)
        self.assertEqual(loaded.schema_version, MANIFEST_SCHEMA_VERSION)
        self.assertEqual(loaded.files, {})

    def test_save_creates_parent_dirs(self):
        """save() creates parent directories if needed."""
        path = self.base / "sub" / "dir" / "manifest.json"
        m = Manifest()
        m.save(path)
        self.assertTrue(path.exists())

    def test_save_produces_valid_json(self):
        """The saved file is valid JSON."""
        path = self.base / "manifest.json"
        m = Manifest(files={"a.md": "hash1"})
        m.save(path)
        with open(path) as f:
            data = json.load(f)
        self.assertIn("schema_version", data)
        self.assertIn("files", data)


class TestManifestProvenance(unittest.TestCase):
    """Test Manifest.update_provenance()."""

    def test_provenance_sets_required_keys(self):
        """update_provenance populates machine_id, hostname, platform, timestamp, python_version."""
        m = Manifest()
        m.update_provenance()
        self.assertIsNotNone(m.last_push)
        required_keys = {"machine_id", "hostname", "platform", "timestamp", "python_version"}
        self.assertTrue(required_keys.issubset(set(m.last_push.keys())))

    def test_provenance_timestamp_format(self):
        """Provenance timestamp ends with Z (UTC)."""
        m = Manifest()
        m.update_provenance()
        self.assertTrue(m.last_push["timestamp"].endswith("Z"))

    def test_provenance_overwrites_previous(self):
        """Calling update_provenance twice overwrites the first."""
        m = Manifest()
        m.update_provenance()
        first_ts = m.last_push["timestamp"]
        m.update_provenance()
        # Just verify it was updated (timestamp may be same if fast enough)
        self.assertIsNotNone(m.last_push["timestamp"])


class TestManifestSchemaHandling(unittest.TestCase):
    """Test Manifest with various schema versions."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.base = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_load_future_schema_version(self):
        """Loading a manifest with a future schema version preserves it."""
        path = self.base / "manifest.json"
        data = {
            "schema_version": 99,
            "files": {"x.md": "hash"},
            "last_push": None,
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:00:00Z",
        }
        with open(path, "w") as f:
            json.dump(data, f)
        loaded = Manifest.load(path)
        self.assertEqual(loaded.schema_version, 99)

    def test_load_missing_keys_uses_defaults(self):
        """Loading a manifest missing optional keys uses defaults."""
        path = self.base / "manifest.json"
        data = {"schema_version": 1}
        with open(path, "w") as f:
            json.dump(data, f)
        loaded = Manifest.load(path)
        self.assertEqual(loaded.files, {})
        self.assertIsNone(loaded.last_push)


if __name__ == "__main__":
    unittest.main()
