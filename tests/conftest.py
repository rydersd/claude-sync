"""
Shared import helper for claude-sync test suite.

Since claude-sync.py contains a hyphen, we use importlib to load it
as a module named 'claude_sync'. This module is imported once here
and re-exported so individual test files can do:

    from conftest import claude_sync
"""

import importlib.util
import os

_MODULE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "claude-sync.py",
)

spec = importlib.util.spec_from_file_location("claude_sync", _MODULE_PATH)
claude_sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(claude_sync)
