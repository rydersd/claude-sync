"""Tests for SettingsMerger: extract_portable, deep_merge, merge_for_push, merge_for_pull."""

import copy
import unittest

from conftest import claude_sync

SettingsMerger = claude_sync.SettingsMerger
PORTABLE_SETTINGS_KEYS = claude_sync.PORTABLE_SETTINGS_KEYS
MACHINE_SPECIFIC_KEYS = claude_sync.MACHINE_SPECIFIC_KEYS
RECOMMENDED_ENV_KEYS = claude_sync.RECOMMENDED_ENV_KEYS


class TestExtractPortable(unittest.TestCase):
    """Test SettingsMerger.extract_portable."""

    def test_extracts_portable_keys_only(self):
        """Only keys in PORTABLE_SETTINGS_KEYS are extracted."""
        settings = {
            "hooks": {"PostToolUse": []},
            "env": {"PATH": "/usr/bin"},
            "permissions": {"allow": []},
            "attribution": True,
            "mcpServers": {"server1": {}},
        }
        portable = SettingsMerger.extract_portable(settings)
        self.assertIn("hooks", portable)
        self.assertIn("attribution", portable)
        self.assertIn("permissions", portable)
        self.assertNotIn("env", portable)  # No recommended env keys present
        self.assertNotIn("mcpServers", portable)

    def test_missing_portable_keys_skipped(self):
        """Keys not present in input are simply absent from output."""
        settings = {"env": {"PATH": "/usr/bin"}}
        portable = SettingsMerger.extract_portable(settings)
        self.assertEqual(portable, {})

    def test_deep_copy_isolation(self):
        """Extracted values are deep copies, not references."""
        hooks = {"PostToolUse": [{"type": "command"}]}
        settings = {"hooks": hooks}
        portable = SettingsMerger.extract_portable(settings)
        # Mutating the original should not affect the extracted copy
        hooks["PostToolUse"].append({"type": "another"})
        self.assertEqual(len(portable["hooks"]["PostToolUse"]), 1)

    def test_all_portable_keys(self):
        """When all portable keys are present, all are extracted."""
        settings = {k: f"value_{k}" for k in PORTABLE_SETTINGS_KEYS}
        portable = SettingsMerger.extract_portable(settings)
        for key in PORTABLE_SETTINGS_KEYS:
            self.assertIn(key, portable)

    def test_empty_settings(self):
        """Empty settings yields empty portable."""
        self.assertEqual(SettingsMerger.extract_portable({}), {})

    def test_extracts_recommended_env_keys(self):
        """Recommended env keys are extracted from the env block."""
        settings = {
            "env": {
                "PATH": "/usr/bin",
                "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1",
            },
        }
        portable = SettingsMerger.extract_portable(settings)
        self.assertIn("env", portable)
        self.assertIn("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS", portable["env"])
        self.assertNotIn("PATH", portable["env"])

    def test_no_recommended_env_keys_means_no_env_in_portable(self):
        """If no recommended env keys present, env is not in portable."""
        settings = {"env": {"PATH": "/usr/bin", "HOME": "/Users/me"}}
        portable = SettingsMerger.extract_portable(settings)
        self.assertNotIn("env", portable)

    def test_extracts_teammate_mode_and_theme(self):
        """teammateMode and theme are portable keys."""
        settings = {"teammateMode": "tmux", "theme": "dark"}
        portable = SettingsMerger.extract_portable(settings)
        self.assertIn("teammateMode", portable)
        self.assertEqual(portable["teammateMode"], "tmux")
        self.assertIn("theme", portable)
        self.assertEqual(portable["theme"], "dark")


class TestDeepMerge(unittest.TestCase):
    """Test SettingsMerger.deep_merge."""

    def test_overlay_adds_new_keys(self):
        """New keys from overlay are added to result."""
        base = {"a": 1}
        overlay = {"b": 2}
        result = SettingsMerger.deep_merge(base, overlay)
        self.assertEqual(result, {"a": 1, "b": 2})

    def test_overlay_overwrites_leaf_values(self):
        """Overlay wins for leaf (non-dict) values."""
        base = {"a": 1}
        overlay = {"a": 2}
        result = SettingsMerger.deep_merge(base, overlay)
        self.assertEqual(result["a"], 2)

    def test_nested_dicts_merged_recursively(self):
        """Nested dicts are merged, not replaced wholesale."""
        base = {"hooks": {"pre": ["cmd1"], "post": ["cmd2"]}}
        overlay = {"hooks": {"pre": ["cmd3"]}}
        result = SettingsMerger.deep_merge(base, overlay)
        # pre is overwritten (leaf value), post is preserved
        self.assertEqual(result["hooks"]["pre"], ["cmd3"])
        self.assertEqual(result["hooks"]["post"], ["cmd2"])

    def test_deep_merge_does_not_mutate_base(self):
        """Original base dict is not modified."""
        base = {"a": {"x": 1}}
        overlay = {"a": {"y": 2}}
        base_copy = copy.deepcopy(base)
        SettingsMerger.deep_merge(base, overlay)
        self.assertEqual(base, base_copy)

    def test_deep_merge_does_not_mutate_overlay(self):
        """Original overlay dict is not modified."""
        base = {"a": 1}
        overlay = {"b": {"nested": True}}
        overlay_copy = copy.deepcopy(overlay)
        SettingsMerger.deep_merge(base, overlay)
        self.assertEqual(overlay, overlay_copy)

    def test_three_level_deep_merge(self):
        """Three levels of nesting merge correctly."""
        base = {"l1": {"l2": {"l3_a": "base"}}}
        overlay = {"l1": {"l2": {"l3_b": "overlay"}}}
        result = SettingsMerger.deep_merge(base, overlay)
        self.assertEqual(result["l1"]["l2"]["l3_a"], "base")
        self.assertEqual(result["l1"]["l2"]["l3_b"], "overlay")

    def test_overlay_replaces_non_dict_with_dict(self):
        """If base has a scalar and overlay has a dict, overlay wins."""
        base = {"a": "string"}
        overlay = {"a": {"nested": True}}
        result = SettingsMerger.deep_merge(base, overlay)
        self.assertEqual(result["a"], {"nested": True})

    def test_both_empty(self):
        """Merging two empty dicts yields empty dict."""
        self.assertEqual(SettingsMerger.deep_merge({}, {}), {})


class TestMergeForPush(unittest.TestCase):
    """Test SettingsMerger.merge_for_push."""

    def test_strips_machine_specific(self):
        """merge_for_push only keeps portable keys."""
        home = {
            "hooks": {"pre": []},
            "env": {"SECRET": "val"},
            "permissions": {"allow": ["Read"]},
            "statusLine": True,
            "mcpServers": {"server1": {}},
        }
        result = SettingsMerger.merge_for_push(home)
        self.assertIn("hooks", result)
        self.assertIn("statusLine", result)
        self.assertIn("permissions", result)
        self.assertNotIn("mcpServers", result)
        self.assertNotIn("env", result)  # No recommended env keys present

    def test_push_empty_settings(self):
        """Pushing empty settings returns empty dict."""
        result = SettingsMerger.merge_for_push({})
        self.assertEqual(result, {})


class TestMergeForPull(unittest.TestCase):
    """Test SettingsMerger.merge_for_pull."""

    def test_pull_preserves_local_machine_keys(self):
        """Pull keeps local machine-specific keys untouched."""
        local = {
            "hooks": {"pre": ["old"]},
            "env": {"PATH": "/local"},
            "permissions": {"allow": ["Bash"]},
        }
        repo = {
            "hooks": {"pre": ["new"]},
            "attribution": False,
        }
        result = SettingsMerger.merge_for_pull(local, repo)
        # Machine-specific keys preserved
        self.assertEqual(result["env"]["PATH"], "/local")
        self.assertEqual(result["permissions"]["allow"], ["Bash"])
        # Portable keys from repo applied
        self.assertEqual(result["hooks"]["pre"], ["new"])
        self.assertEqual(result["attribution"], False)

    def test_pull_strips_machine_keys_from_repo(self):
        """Even if repo has machine-specific keys, they're not pulled."""
        local = {"env": {"local": True}}
        repo = {
            "hooks": {"x": 1},
            "env": {"repo_env": True},  # Machine-specific, should be ignored
        }
        result = SettingsMerger.merge_for_pull(local, repo)
        # Only portable keys from repo are merged
        self.assertIn("hooks", result)
        # Local env preserved, repo env NOT merged
        self.assertEqual(result["env"], {"local": True})

    def test_pull_adds_new_portable_keys(self):
        """Pull adds portable keys that don't exist locally."""
        local = {}
        repo = {"hooks": {"pre": ["cmd"]}, "statusLine": True}
        result = SettingsMerger.merge_for_pull(local, repo)
        self.assertEqual(result["hooks"]["pre"], ["cmd"])
        self.assertTrue(result["statusLine"])

    def test_pull_merges_recommended_env_keys(self):
        """Pull merges recommended env keys without clobbering local env."""
        local = {
            "env": {"PATH": "/local", "HOME": "/Users/me"},
        }
        repo = {
            "hooks": {"pre": ["cmd"]},
            "env": {
                "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1",
                "SHOULD_BE_IGNORED": "yes",
            },
        }
        result = SettingsMerger.merge_for_pull(local, repo)
        # Local env keys preserved
        self.assertEqual(result["env"]["PATH"], "/local")
        self.assertEqual(result["env"]["HOME"], "/Users/me")
        # Recommended env key merged
        self.assertEqual(result["env"]["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"], "1")
        # Non-recommended env key NOT merged
        self.assertNotIn("SHOULD_BE_IGNORED", result["env"])
        # Portable key merged
        self.assertIn("hooks", result)

    def test_pull_creates_env_if_missing_locally(self):
        """Pull creates env block if local has none but remote has recommended keys."""
        local = {"hooks": {"old": True}}
        repo = {
            "env": {"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"},
        }
        result = SettingsMerger.merge_for_pull(local, repo)
        self.assertIn("env", result)
        self.assertEqual(result["env"]["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"], "1")

    def test_pull_does_not_mutate_local(self):
        """merge_for_pull doesn't modify the local settings input."""
        local = {"hooks": {"pre": ["old"]}}
        repo = {"hooks": {"pre": ["new"]}}
        local_copy = copy.deepcopy(local)
        SettingsMerger.merge_for_pull(local, repo)
        self.assertEqual(local, local_copy)


if __name__ == "__main__":
    unittest.main()
