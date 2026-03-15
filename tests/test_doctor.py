"""Tests for Doctor: health check pass/fail scenarios."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from conftest import claude_sync

Doctor = claude_sync.Doctor
PathResolver = claude_sync.PathResolver
Manifest = claude_sync.Manifest
HealthCheck = claude_sync.HealthCheck
MANIFEST_SCHEMA_VERSION = claude_sync.MANIFEST_SCHEMA_VERSION
FileHasher = claude_sync.FileHasher


class TestDoctorGitRepo(unittest.TestCase):
    """Test _check_git_repo health check."""

    def test_passes_with_git_dir(self):
        """check passes when .git directory exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir) / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            resolver = PathResolver(repo_root=repo)
            doc = Doctor(resolver)
            check = doc._check_git_repo()
            self.assertTrue(check.passed)
            self.assertEqual(check.name, "git_repo")

    def test_fails_without_git_dir(self):
        """check fails when .git directory is missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir) / "repo"
            repo.mkdir()
            # No .git directory, but pass it explicitly to PathResolver
            resolver = PathResolver(repo_root=repo)
            doc = Doctor(resolver)
            check = doc._check_git_repo()
            self.assertFalse(check.passed)


class TestDoctorHomeClaude(unittest.TestCase):
    """Test _check_home_claude health check."""

    def test_passes_when_home_claude_exists(self):
        """check passes when ~/.claude exists (real system check)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            (repo / ".git").mkdir()
            resolver = PathResolver(repo_root=repo)
            # Override home_claude to a path that exists
            resolver._home_claude = Path(tmpdir)
            doc = Doctor(resolver)
            check = doc._check_home_claude()
            self.assertTrue(check.passed)

    def test_fails_when_home_claude_missing(self):
        """check fails when home_claude path doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            (repo / ".git").mkdir()
            resolver = PathResolver(repo_root=repo)
            resolver._home_claude = Path(tmpdir) / "nonexistent"
            doc = Doctor(resolver)
            check = doc._check_home_claude()
            self.assertFalse(check.passed)


class TestDoctorRepoClaude(unittest.TestCase):
    """Test _check_repo_claude health check."""

    def test_passes_when_repo_claude_exists(self):
        """check passes when repo/claude directory exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir) / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            claude_dir = repo / "claude"
            claude_dir.mkdir()
            resolver = PathResolver(repo_root=repo)
            doc = Doctor(resolver)
            check = doc._check_repo_claude()
            self.assertTrue(check.passed)

    def test_fails_when_repo_claude_missing(self):
        """check fails when repo/claude doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir) / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            resolver = PathResolver(repo_root=repo)
            doc = Doctor(resolver)
            check = doc._check_repo_claude()
            self.assertFalse(check.passed)


class TestDoctorManifestValid(unittest.TestCase):
    """Test _check_manifest_valid health check."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmpdir.name) / "repo"
        self.repo.mkdir()
        (self.repo / ".git").mkdir()
        (self.repo / "claude").mkdir()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_passes_with_valid_manifest(self):
        """check passes when manifest.json is valid with correct schema."""
        manifest = Manifest()
        manifest.save(self.repo / "manifest.json")
        resolver = PathResolver(repo_root=self.repo)
        doc = Doctor(resolver)
        check = doc._check_manifest_valid()
        self.assertTrue(check.passed)

    def test_fails_when_manifest_missing(self):
        """check fails when manifest.json doesn't exist."""
        resolver = PathResolver(repo_root=self.repo)
        doc = Doctor(resolver)
        check = doc._check_manifest_valid()
        self.assertFalse(check.passed)

    def test_fails_with_wrong_schema_version(self):
        """check fails when schema version doesn't match expected."""
        path = self.repo / "manifest.json"
        data = {"schema_version": 999, "files": {}}
        with open(path, "w") as f:
            json.dump(data, f)
        resolver = PathResolver(repo_root=self.repo)
        doc = Doctor(resolver)
        check = doc._check_manifest_valid()
        self.assertFalse(check.passed)
        self.assertIn("999", check.message)

    def test_fails_with_corrupt_json(self):
        """check fails when manifest.json contains invalid JSON."""
        path = self.repo / "manifest.json"
        path.write_text("{invalid json!!}")
        resolver = PathResolver(repo_root=self.repo)
        doc = Doctor(resolver)
        check = doc._check_manifest_valid()
        self.assertFalse(check.passed)
        self.assertIn("corrupt", check.message)


class TestDoctorFileHashes(unittest.TestCase):
    """Test _check_file_hashes health check."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmpdir.name) / "repo"
        self.repo.mkdir()
        (self.repo / ".git").mkdir()
        self.claude_dir = self.repo / "claude"
        self.claude_dir.mkdir()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_passes_when_hashes_match(self):
        """check passes when all file hashes match the manifest."""
        # Create a file and record its hash in the manifest
        rules_dir = self.claude_dir / "rules"
        rules_dir.mkdir()
        test_file = rules_dir / "test.md"
        test_file.write_text("content")
        file_hash = FileHasher.hash_file(test_file)

        manifest = Manifest(files={os.path.join("rules", "test.md"): file_hash})
        manifest.save(self.repo / "manifest.json")

        resolver = PathResolver(repo_root=self.repo)
        doc = Doctor(resolver)
        check = doc._check_file_hashes()
        self.assertTrue(check.passed)

    def test_fails_when_hash_mismatch(self):
        """check fails when a file hash doesn't match the manifest."""
        rules_dir = self.claude_dir / "rules"
        rules_dir.mkdir()
        (rules_dir / "test.md").write_text("modified content")

        manifest = Manifest(files={os.path.join("rules", "test.md"): "wrong_hash"})
        manifest.save(self.repo / "manifest.json")

        resolver = PathResolver(repo_root=self.repo)
        doc = Doctor(resolver)
        check = doc._check_file_hashes()
        self.assertFalse(check.passed)
        self.assertIn("don't match", check.message)

    def test_fails_when_no_manifest(self):
        """check fails when manifest doesn't exist."""
        resolver = PathResolver(repo_root=self.repo)
        doc = Doctor(resolver)
        check = doc._check_file_hashes()
        self.assertFalse(check.passed)


class TestDoctorSettingsKeys(unittest.TestCase):
    """Test _check_settings_keys health check."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmpdir.name) / "repo"
        self.repo.mkdir()
        (self.repo / ".git").mkdir()
        self.claude_dir = self.repo / "claude"
        self.claude_dir.mkdir()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_passes_with_portable_only(self):
        """check passes when settings.json has only portable keys."""
        settings = {"hooks": {"pre": []}, "statusLine": True}
        with open(self.claude_dir / "settings.json", "w") as f:
            json.dump(settings, f)
        resolver = PathResolver(repo_root=self.repo)
        doc = Doctor(resolver)
        check = doc._check_settings_keys()
        self.assertTrue(check.passed)

    def test_fails_with_machine_specific_keys(self):
        """check fails when settings.json has machine-specific keys."""
        settings = {"hooks": {}, "env": {"PATH": "/usr/bin"}}
        with open(self.claude_dir / "settings.json", "w") as f:
            json.dump(settings, f)
        resolver = PathResolver(repo_root=self.repo)
        doc = Doctor(resolver)
        check = doc._check_settings_keys()
        self.assertFalse(check.passed)

    def test_passes_when_no_settings_json(self):
        """check passes when there's no settings.json at all."""
        resolver = PathResolver(repo_root=self.repo)
        doc = Doctor(resolver)
        check = doc._check_settings_keys()
        self.assertTrue(check.passed)


class TestDoctorNoExcludedInPortable(unittest.TestCase):
    """Test _check_no_excluded_in_portable health check."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmpdir.name) / "repo"
        self.repo.mkdir()
        (self.repo / ".git").mkdir()
        self.claude_dir = self.repo / "claude"
        self.claude_dir.mkdir()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_passes_when_no_excluded_paths(self):
        """check passes when repo/claude has no excluded paths."""
        resolver = PathResolver(repo_root=self.repo)
        doc = Doctor(resolver)
        check = doc._check_no_excluded_in_portable()
        self.assertTrue(check.passed)

    def test_fails_when_excluded_path_leaked(self):
        """check fails when an excluded path like cache/ exists in repo/claude."""
        (self.claude_dir / "cache").mkdir()
        resolver = PathResolver(repo_root=self.repo)
        doc = Doctor(resolver)
        check = doc._check_no_excluded_in_portable()
        self.assertFalse(check.passed)
        self.assertIn("cache", check.message)

    def test_fails_with_env_file(self):
        """check fails when .env file exists in repo/claude."""
        (self.claude_dir / ".env").write_text("SECRET=val")
        resolver = PathResolver(repo_root=self.repo)
        doc = Doctor(resolver)
        check = doc._check_no_excluded_in_portable()
        self.assertFalse(check.passed)


class TestDoctorRunAll(unittest.TestCase):
    """Test Doctor.run_all returns all checks."""

    def test_run_all_returns_list_of_health_checks(self):
        """run_all returns a list of HealthCheck objects."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir) / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            (repo / "claude").mkdir()
            resolver = PathResolver(repo_root=repo)
            resolver._home_claude = Path(tmpdir)  # Exists
            doc = Doctor(resolver)
            checks = doc.run_all()
            self.assertIsInstance(checks, list)
            self.assertTrue(len(checks) >= 5)
            for check in checks:
                self.assertIsInstance(check, HealthCheck)

    def test_health_check_to_dict(self):
        """HealthCheck.to_dict produces expected structure."""
        hc = HealthCheck(
            name="test_check",
            passed=True,
            message="All good",
            remediation="",
        )
        d = hc.to_dict()
        self.assertEqual(d["name"], "test_check")
        self.assertTrue(d["passed"])
        self.assertNotIn("remediation", d)  # Empty remediation excluded

    def test_health_check_to_dict_with_remediation(self):
        """HealthCheck.to_dict includes remediation when present."""
        hc = HealthCheck(
            name="broken",
            passed=False,
            message="Something wrong",
            remediation="Fix it",
        )
        d = hc.to_dict()
        self.assertEqual(d["remediation"], "Fix it")


if __name__ == "__main__":
    unittest.main()
