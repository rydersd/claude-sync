"""Tests for SecretScanner: all 9 regex patterns + false positive handling."""

import tempfile
import unittest
from pathlib import Path

from conftest import claude_sync

SecretScanner = claude_sync.SecretScanner
SecretFinding = claude_sync.SecretFinding


class TestSecretScannerPatterns(unittest.TestCase):
    """Test each of the 9 secret detection patterns individually."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.base = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _scan_content(self, content: str) -> list:
        """Helper: write content to a temp file and scan it."""
        filepath = self.base / "test_file.txt"
        filepath.write_text(content)
        return SecretScanner.scan_file(filepath, "test_file.txt")

    def test_pattern_api_key_sk(self):
        """Detects sk-* API keys (20+ alphanumeric chars after 'sk-')."""
        findings = self._scan_content("my_key = sk-abcdefghijklmnopqrstuvwxyz")
        self.assertTrue(any("API Key" in f.pattern_name for f in findings))

    def test_pattern_anthropic_api_key(self):
        """Detects ANTHROPIC_API_KEY=value patterns."""
        findings = self._scan_content("ANTHROPIC_API_KEY=sk-ant-1234567890abcdef")
        self.assertTrue(any("Anthropic" in f.pattern_name for f in findings))

    def test_pattern_bearer_token(self):
        """Detects Bearer token patterns."""
        findings = self._scan_content("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.test")
        self.assertTrue(any("Bearer" in f.pattern_name for f in findings))

    def test_pattern_private_key(self):
        """Detects PEM private key headers."""
        findings = self._scan_content("-----BEGIN RSA PRIVATE KEY-----")
        self.assertTrue(any("Private Key" in f.pattern_name for f in findings))

    def test_pattern_password_assignment(self):
        """Detects password=value assignments."""
        findings = self._scan_content("password = mysecretpassword123")
        self.assertTrue(any("Password" in f.pattern_name for f in findings))

    def test_pattern_connection_string(self):
        """Detects database connection strings."""
        findings = self._scan_content("DATABASE_URL=postgres://user:pass@host:5432/db")
        self.assertTrue(any("Connection" in f.pattern_name for f in findings))

    def test_pattern_aws_key(self):
        """Detects AWS access key IDs (AKIA/ASIA prefix + 16 chars)."""
        findings = self._scan_content("aws_key = AKIAIOSFODNN7A2B3C4D")
        self.assertTrue(any("AWS" in f.pattern_name for f in findings))

    def test_pattern_github_token(self):
        """Detects GitHub tokens (ghp_/gho_/ghu_/ghs_/ghr_ prefix + 36+ chars)."""
        token = "ghp_" + "a" * 36
        findings = self._scan_content(f"GITHUB_TOKEN={token}")
        self.assertTrue(any("GitHub" in f.pattern_name for f in findings))

    def test_pattern_generic_secret(self):
        """Detects generic secret=value patterns with quoted values."""
        findings = self._scan_content('api_key = "this_is_a_long_secret_value"')
        self.assertTrue(any("Generic" in f.pattern_name for f in findings))


class TestSecretScannerFalsePositives(unittest.TestCase):
    """Test that benign content does NOT trigger false positives."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.base = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _scan_content(self, content: str) -> list:
        filepath = self.base / "test_file.txt"
        filepath.write_text(content)
        return SecretScanner.scan_file(filepath, "test_file.txt")

    def test_short_sk_prefix_not_detected(self):
        """Short 'sk-' strings (< 20 chars) should NOT match API Key pattern."""
        findings = self._scan_content("sk-short")
        sk_findings = [f for f in findings if "API Key (sk-*)" == f.pattern_name]
        self.assertEqual(len(sk_findings), 0)

    def test_plain_text_password_word(self):
        """The word 'password' in a sentence without assignment is not a finding."""
        findings = self._scan_content("Please remember your password policy")
        pwd_findings = [f for f in findings if "Password" in f.pattern_name]
        self.assertEqual(len(pwd_findings), 0)

    def test_commented_placeholder(self):
        """Placeholder-like content 'sk-placeholder' is filtered out by placeholder detection."""
        findings = self._scan_content("# key: sk-placeholder1234567890abcdef")
        sk_findings = [f for f in findings if "API Key" in f.pattern_name]
        # Placeholder indicator skips the line
        self.assertEqual(len(sk_findings), 0)

    def test_no_false_positive_on_normal_markdown(self):
        """Normal markdown documentation should not trigger any patterns."""
        content = """# Configuration Guide

This document explains how to configure the sync tool.

## Steps
1. Run init
2. Run push
3. Verify with doctor
"""
        findings = self._scan_content(content)
        self.assertEqual(len(findings), 0)

    def test_no_false_positive_on_http_url(self):
        """Plain HTTP URLs should not trigger connection string pattern."""
        findings = self._scan_content("Visit https://example.com for more info")
        conn_findings = [f for f in findings if "Connection" in f.pattern_name]
        self.assertEqual(len(conn_findings), 0)

    def test_bearer_case_insensitive(self):
        """Both 'Bearer' and 'bearer' are detected."""
        findings_upper = self._scan_content("bearer eyJhbGciOiJIUzI1NiJ9.test")
        findings_lower = self._scan_content("Bearer eyJhbGciOiJIUzI1NiJ9.test")
        self.assertTrue(len(findings_upper) > 0)
        self.assertTrue(len(findings_lower) > 0)


class TestSecretScannerOutput(unittest.TestCase):
    """Test SecretFinding output formatting."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.base = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_finding_has_line_number(self):
        """SecretFinding includes the correct line number."""
        filepath = self.base / "test.txt"
        filepath.write_text("line 1\nline 2\npassword=secret123\nline 4\n")
        findings = SecretScanner.scan_file(filepath, "test.txt")
        pwd_findings = [f for f in findings if "Password" in f.pattern_name]
        self.assertTrue(len(pwd_findings) > 0)
        self.assertEqual(pwd_findings[0].line_number, 3)

    def test_finding_masks_long_text(self):
        """Matched text longer than 12 chars is masked (first 6 + ... + last 3)."""
        filepath = self.base / "test.txt"
        filepath.write_text("password = verylongsecretpassword\n")
        findings = SecretScanner.scan_file(filepath, "test.txt")
        pwd_findings = [f for f in findings if "Password" in f.pattern_name]
        self.assertTrue(len(pwd_findings) > 0)
        masked = pwd_findings[0].matched_text
        self.assertIn("...", masked)

    def test_finding_to_dict(self):
        """SecretFinding.to_dict() produces expected structure."""
        finding = SecretFinding(
            file_path="test.md",
            line_number=5,
            pattern_name="Test Pattern",
            matched_text="sec...",
        )
        d = finding.to_dict()
        self.assertEqual(d["file_path"], "test.md")
        self.assertEqual(d["line_number"], 5)
        self.assertEqual(d["pattern_name"], "Test Pattern")

    def test_binary_file_handled_gracefully(self):
        """Scanning a binary file doesn't crash."""
        filepath = self.base / "binary.bin"
        filepath.write_bytes(bytes(range(256)))
        findings = SecretScanner.scan_file(filepath, "binary.bin")
        # Should return a list (possibly empty) without error
        self.assertIsInstance(findings, list)


if __name__ == "__main__":
    unittest.main()
