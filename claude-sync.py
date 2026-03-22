#!/usr/bin/env python3
"""
claude-sync: Sync Claude Code configuration between machines.

A single-file Python CLI tool using only stdlib. Syncs ~/.claude config
to a git repo's claude/ directory for portability across machines.

Usage:
    python claude-sync.py init          Initialize sync in current repo
    python claude-sync.py status        Show sync status
    python claude-sync.py push          Push ~/.claude -> repo/claude
    python claude-sync.py pull          Pull repo/claude -> ~/.claude
    python claude-sync.py diff          Show file differences
    python claude-sync.py resolve       Show and resolve sync conflicts
    python claude-sync.py history       Show file sync history
    python claude-sync.py doctor        Run health checks
    python claude-sync.py backup        Manage backups
    python claude-sync.py restore       Restore from backup
    python claude-sync.py watch         Watch for changes and auto-sync
    python claude-sync.py hooks         Install/uninstall git hooks
    python claude-sync.py ecosystem     Ecosystem analysis (duplicates, stats)
    python claude-sync.py tracker       Manage tracker connections
    python claude-sync.py pair          Pair with a remote device
    python claude-sync.py genome        Skill dependency management

Exit codes:
    0 = success
    1 = error
    2 = dirty (changes exist)
    3 = secrets detected
    4 = not initialized
"""

import argparse
import copy
import datetime
import difflib
import fnmatch
import hashlib
import json
import os
import platform
import re
import shutil
import signal
import stat
import sys
import textwrap
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Optional: watchdog for OS-native file system events (falls back to polling)
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler, FileModifiedEvent, FileCreatedEvent, FileDeletedEvent
    HAS_WATCHDOG = True
except ImportError:
    HAS_WATCHDOG = False


# =============================================================================
# Constants
# =============================================================================

MANIFEST_SCHEMA_VERSION = 2
MANIFEST_FILENAME = "manifest.json"
BACKUP_DIR = Path.home() / ".claude-sync-backups"
DEFAULT_BACKUP_RETENTION = 5
HASH_CHUNK_SIZE = 65536  # 64KB

# Paths to sync (relative to ~/.claude or repo/claude)
SYNC_PATHS = [
    "CLAUDE.md",
    "agents/",
    "skills/",
    "rules/",
    "hooks/",
    "scripts/",
    "memory/",
    "worksets/",
]

# Paths that NEVER sync
EXCLUDE_PATHS = [
    ".env",
    "mcp_config.json",
    "session-env/",
    "todos/",
    "projects/",
    "history.jsonl",
    "stats-cache.json",
    "telemetry/",
    "cache/",
    "state/",
    "plans/",
    "downloads/",
    "plugins/",
    "shell-snapshots/",
    "paste-cache/",
    "file-history/",
    "debug/",
    "statsig/",
    ".workset-vault/",
    "worksets/_state.json",
    "worksets/_affinity.json",
]

# Exclusion patterns for tree walking
WALK_EXCLUDE_PATTERNS = [
    "node_modules",
    "__pycache__",
    ".pyc",
    ".DS_Store",
    "*.swp",
    "*.swo",
    "*~",
]

# settings.json keys that are portable (safe to sync)
PORTABLE_SETTINGS_KEYS = [
    "hooks",
    "statusLine",
    "attribution",
]

# settings.json keys that are machine-specific (never sync)
MACHINE_SPECIFIC_KEYS = [
    "env",
    "permissions",
]


# =============================================================================
# Exit Codes
# =============================================================================

class ExitCode(IntEnum):
    OK = 0
    ERROR = 1
    DIRTY = 2
    SECRETS = 3
    NOT_INITIALIZED = 4


# =============================================================================
# Phase 1: Core MVP
# =============================================================================

class PathResolver:
    """Resolves ~/.claude and repo/claude paths, auto-detects git root."""

    def __init__(self, repo_root: Optional[Path] = None):
        self._home_claude = Path.home() / ".claude"
        if repo_root:
            self._repo_root = repo_root.resolve()
        else:
            self._repo_root = self._find_git_root()
        self._repo_claude = self._repo_root / "claude" if self._repo_root else None

    @property
    def home_claude(self) -> Path:
        return self._home_claude

    @property
    def repo_root(self) -> Optional[Path]:
        return self._repo_root

    @property
    def repo_claude(self) -> Optional[Path]:
        return self._repo_claude

    @property
    def manifest_path(self) -> Optional[Path]:
        if self._repo_root:
            return self._repo_root / MANIFEST_FILENAME
        return None

    def _find_git_root(self) -> Optional[Path]:
        """Walk up from cwd to find .git directory."""
        current = Path.cwd().resolve()
        while True:
            if (current / ".git").exists():
                return current
            parent = current.parent
            if parent == current:
                return None
            current = parent

    def home_to_relative(self, abs_path: Path) -> str:
        """Convert absolute home path to relative sync path."""
        return str(abs_path.relative_to(self._home_claude))

    def repo_to_relative(self, abs_path: Path) -> str:
        """Convert absolute repo path to relative sync path."""
        return str(abs_path.relative_to(self._repo_claude))

    def relative_to_home(self, rel_path: str) -> Path:
        """Convert relative sync path to absolute home path."""
        return self._home_claude / rel_path

    def relative_to_repo(self, rel_path: str) -> Path:
        """Convert relative sync path to absolute repo path."""
        return self._repo_claude / rel_path


class FileHasher:
    """SHA-256 hashing with 64KB chunks and tree walking."""

    @staticmethod
    def hash_file(path: Path) -> str:
        """Compute SHA-256 hash of a file."""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(HASH_CHUNK_SIZE)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def should_exclude(path: Path, base_dir: Path) -> bool:
        """Check if a path should be excluded from syncing."""
        rel = str(path.relative_to(base_dir))
        # Check against explicit exclude paths
        for excl in EXCLUDE_PATHS:
            if excl.endswith("/"):
                # Directory exclusion
                dir_name = excl.rstrip("/")
                if rel == dir_name or rel.startswith(dir_name + "/"):
                    return True
            else:
                if rel == excl:
                    return True
        # Check against walk exclusion patterns
        for part in path.parts:
            for pattern in WALK_EXCLUDE_PATTERNS:
                if fnmatch.fnmatch(part, pattern):
                    return True
        return False

    @staticmethod
    def is_syncable(path: Path, base_dir: Path) -> bool:
        """Check if a path falls under a syncable path prefix."""
        rel = str(path.relative_to(base_dir))
        for sync_path in SYNC_PATHS:
            if sync_path.endswith("/"):
                prefix = sync_path.rstrip("/")
                if rel.startswith(prefix + "/") or rel == prefix:
                    return True
            else:
                if rel == sync_path:
                    return True
        # Also check settings.json (partial sync)
        if rel == "settings.json":
            return True
        return False

    @classmethod
    def walk_directory(cls, base_dir: Path) -> Dict[str, str]:
        """Walk a directory and return {relative_path: sha256_hash} for syncable files."""
        result = {}
        if not base_dir.exists():
            return result
        for root_path, dirs, files in os.walk(base_dir):
            root = Path(root_path)
            # Filter out excluded directories in-place
            dirs[:] = [
                d for d in dirs
                if not cls.should_exclude(root / d, base_dir)
            ]
            for fname in files:
                fpath = root / fname
                if cls.should_exclude(fpath, base_dir):
                    continue
                if not cls.is_syncable(fpath, base_dir):
                    continue
                if fpath.is_file():
                    rel = str(fpath.relative_to(base_dir))
                    try:
                        result[rel] = cls.hash_file(fpath)
                    except (OSError, PermissionError):
                        pass
        return result


@dataclass
class FileChange:
    """Represents a single file change between home and repo."""
    path: str
    change_type: str  # "added", "modified", "deleted", "conflicted"
    home_hash: Optional[str] = None
    repo_hash: Optional[str] = None
    base_hash: Optional[str] = None  # hash at last successful sync (merge base)

    def to_dict(self) -> dict:
        d = {
            "path": self.path,
            "change_type": self.change_type,
            "home_hash": self.home_hash,
            "repo_hash": self.repo_hash,
        }
        if self.base_hash is not None:
            d["base_hash"] = self.base_hash
        return d


@dataclass
class DiffResult:
    """Result of comparing home and repo file trees."""
    added: List[FileChange] = field(default_factory=list)
    modified: List[FileChange] = field(default_factory=list)
    deleted: List[FileChange] = field(default_factory=list)
    conflicted: List[FileChange] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.modified or self.deleted or self.conflicted)

    @property
    def has_conflicts(self) -> bool:
        return bool(self.conflicted)

    @property
    def total_changes(self) -> int:
        return len(self.added) + len(self.modified) + len(self.deleted) + len(self.conflicted)

    def all_changes(self) -> List[FileChange]:
        return self.added + self.modified + self.deleted + self.conflicted

    def safe_changes(self) -> List[FileChange]:
        """Return only non-conflicted changes (safe to apply)."""
        return self.added + self.modified + self.deleted

    def to_dict(self) -> dict:
        d = {
            "added": [c.to_dict() for c in self.added],
            "modified": [c.to_dict() for c in self.modified],
            "deleted": [c.to_dict() for c in self.deleted],
            "total_changes": self.total_changes,
        }
        if self.conflicted:
            d["conflicted"] = [c.to_dict() for c in self.conflicted]
        return d


class DiffEngine:
    """Set-based hash comparison between home and repo."""

    @staticmethod
    def compare(home_hashes: Dict[str, str], repo_hashes: Dict[str, str],
                direction: str = "push",
                base_hashes: Optional[Dict[str, str]] = None) -> DiffResult:
        """
        Compare file trees. When base_hashes is provided, performs three-way
        merge classification using the manifest as merge base.

        direction='push': home is source, repo is target
        direction='pull': repo is source, home is target

        Three-way logic (when base_hashes provided):
          base == local != remote  -> remote-only change (safe for pull)
          base != local == remote  -> already synced (skip)
          base != local != remote  -> CONFLICT (both sides changed)
          base == local == remote  -> no change
        """
        result = DiffResult()
        if direction == "push":
            source, target = home_hashes, repo_hashes
        else:
            source, target = repo_hashes, home_hashes

        source_paths = set(source.keys())
        target_paths = set(target.keys())
        all_paths = source_paths | target_paths
        if base_hashes is not None:
            all_paths |= set(base_hashes.keys())

        for path in sorted(all_paths):
            home_h = home_hashes.get(path)
            repo_h = repo_hashes.get(path)
            src_h = source.get(path)
            tgt_h = target.get(path)

            # No difference between source and target — skip
            if src_h == tgt_h:
                continue

            base_h = base_hashes.get(path) if base_hashes is not None else None

            # Three-way classification when base is available
            if base_hashes is not None:
                # Both sides changed differently from base -> conflict
                if (base_h != home_h and base_h != repo_h
                        and home_h != repo_h):
                    result.conflicted.append(FileChange(
                        path=path,
                        change_type="conflicted",
                        home_hash=home_h,
                        repo_hash=repo_h,
                        base_hash=base_h,
                    ))
                    continue

                # Source changed, target unchanged from base -> safe source-only change
                if base_h != src_h and base_h == tgt_h:
                    if tgt_h is None:
                        result.added.append(FileChange(
                            path=path, change_type="added",
                            home_hash=home_h, repo_hash=repo_h, base_hash=base_h,
                        ))
                    else:
                        result.modified.append(FileChange(
                            path=path, change_type="modified",
                            home_hash=home_h, repo_hash=repo_h, base_hash=base_h,
                        ))
                    continue

                # Target changed, source unchanged from base -> skip (other side is ahead)
                # BUT if target is None (file missing), restore it from source
                if base_h == src_h and base_h != tgt_h:
                    if tgt_h is None:
                        result.added.append(FileChange(
                            path=path, change_type="added",
                            home_hash=home_h, repo_hash=repo_h, base_hash=base_h,
                        ))
                    else:
                        continue

                    continue

                # File deleted on source side, unchanged on target
                if src_h is None and base_h == tgt_h and base_h is not None:
                    result.deleted.append(FileChange(
                        path=path, change_type="deleted",
                        home_hash=home_h, repo_hash=repo_h, base_hash=base_h,
                    ))
                    continue

                # File deleted on target side, unchanged on source -> restore it
                if tgt_h is None and base_h == src_h and base_h is not None:
                    result.added.append(FileChange(
                        path=path, change_type="added",
                        home_hash=home_h, repo_hash=repo_h, base_hash=base_h,
                    ))
                    continue

            # Two-way fallback (no base, or base is None for new files)
            if src_h and not tgt_h:
                result.added.append(FileChange(
                    path=path, change_type="added",
                    home_hash=home_h, repo_hash=repo_h, base_hash=base_h,
                ))
            elif not src_h and tgt_h:
                result.deleted.append(FileChange(
                    path=path, change_type="deleted",
                    home_hash=home_h, repo_hash=repo_h, base_hash=base_h,
                ))
            elif src_h != tgt_h:
                result.modified.append(FileChange(
                    path=path, change_type="modified",
                    home_hash=home_h, repo_hash=repo_h, base_hash=base_h,
                ))

        return result


FILE_HISTORY_MAX_ENTRIES = 20  # max history entries per file


@dataclass
class Manifest:
    """Manifest.json lifecycle: schema v2, file hashes, push/pull provenance, file history."""

    schema_version: int = MANIFEST_SCHEMA_VERSION
    files: Dict[str, str] = field(default_factory=dict)
    last_push: Optional[Dict[str, Any]] = None
    last_pull: Optional[Dict[str, Any]] = None
    file_history: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        """Load manifest from disk. Handles v1 -> v2 migration transparently."""
        if not path.exists():
            return cls()
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(
            schema_version=data.get("schema_version", 1),
            files=data.get("files", {}),
            last_push=data.get("last_push"),
            last_pull=data.get("last_pull"),
            file_history=data.get("file_history", {}),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
        )

    def save(self, path: Path) -> None:
        """Save manifest to disk. Always writes as schema v2."""
        now = datetime.datetime.utcnow().isoformat() + "Z"
        if not self.created_at:
            self.created_at = now
        self.updated_at = now
        self.schema_version = MANIFEST_SCHEMA_VERSION
        data = {
            "schema_version": self.schema_version,
            "files": self.files,
            "last_push": self.last_push,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.last_pull is not None:
            data["last_pull"] = self.last_pull
        if self.file_history:
            data["file_history"] = self.file_history
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")

    def _provenance_dict(self) -> Dict[str, Any]:
        """Current machine provenance info."""
        return {
            "machine_id": str(uuid.getnode()),
            "hostname": platform.node(),
            "platform": platform.system(),
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "python_version": platform.python_version(),
        }

    def update_provenance(self, action: str = "push") -> None:
        """Update push or pull provenance with current machine info."""
        prov = self._provenance_dict()
        if action == "push":
            self.last_push = prov
        elif action == "pull":
            self.last_pull = prov

    def record_file_history(self, changed_paths: List[str], action: str,
                            new_hashes: Dict[str, str]) -> None:
        """Append a history entry for each changed file, capped at FILE_HISTORY_MAX_ENTRIES."""
        entry_base = {
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "machine_id": str(uuid.getnode()),
            "hostname": platform.node(),
            "action": action,
        }
        for path in changed_paths:
            entry = dict(entry_base)
            entry["hash"] = new_hashes.get(path, "")
            if path not in self.file_history:
                self.file_history[path] = []
            self.file_history[path].append(entry)
            # Cap history length
            if len(self.file_history[path]) > FILE_HISTORY_MAX_ENTRIES:
                self.file_history[path] = self.file_history[path][-FILE_HISTORY_MAX_ENTRIES:]


class Output:
    """TTY-aware colored output with ANSI codes."""

    # ANSI color codes
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"

    def __init__(self, json_mode: bool = False, verbose: bool = False,
                 quiet: bool = False):
        self.json_mode = json_mode
        self.verbose = verbose
        self.quiet = quiet
        self.is_tty = sys.stdout.isatty() and not json_mode
        self._json_data: Dict[str, Any] = {}

    def _color(self, text: str, color: str) -> str:
        if self.is_tty:
            return f"{color}{text}{self.RESET}"
        return text

    def header(self, text: str) -> None:
        if self.json_mode or self.quiet:
            return
        print(f"\n{self._color(text, self.BOLD)}")

    def success(self, text: str) -> None:
        if self.json_mode:
            return
        print(f"  {self._color('OK', self.GREEN)} {text}")

    def warning(self, text: str) -> None:
        if self.json_mode:
            return
        print(f"  {self._color('WARN', self.YELLOW)} {text}")

    def error(self, text: str) -> None:
        if self.json_mode:
            return
        print(f"  {self._color('ERR', self.RED)} {text}", file=sys.stderr)

    def info(self, text: str) -> None:
        if self.json_mode or self.quiet:
            return
        print(f"  {text}")

    def detail(self, text: str) -> None:
        """Only shown in verbose mode."""
        if self.json_mode or not self.verbose:
            return
        print(f"    {self._color(text, self.DIM)}")

    def file_added(self, path: str) -> None:
        if self.json_mode:
            return
        print(f"    {self._color('+', self.GREEN)} {path}")

    def file_modified(self, path: str) -> None:
        if self.json_mode:
            return
        print(f"    {self._color('~', self.YELLOW)} {path}")

    def file_deleted(self, path: str) -> None:
        if self.json_mode:
            return
        print(f"    {self._color('-', self.RED)} {path}")

    def diff_line(self, line: str) -> None:
        """Print a colored diff line."""
        if self.json_mode:
            return
        if line.startswith("+++") or line.startswith("---"):
            print(self._color(line, self.BOLD))
        elif line.startswith("@@"):
            print(self._color(line, self.CYAN))
        elif line.startswith("+"):
            print(self._color(line, self.GREEN))
        elif line.startswith("-"):
            print(self._color(line, self.RED))
        else:
            print(line)

    def file_conflicted(self, path: str) -> None:
        if self.json_mode:
            return
        print(f"    {self._color('!', self.MAGENTA)} {path} {self._color('[CONFLICT]', self.MAGENTA)}")

    def print_changes(self, diff_result: DiffResult, direction: str = "push") -> None:
        """Print a summary of file changes."""
        if direction == "push":
            label = "home -> repo"
        else:
            label = "repo -> home"

        if not diff_result.has_changes:
            self.success(f"No changes ({label})")
            return

        self.info(f"Changes ({label}):")
        for change in diff_result.added:
            self.file_added(change.path)
        for change in diff_result.modified:
            self.file_modified(change.path)
        for change in diff_result.deleted:
            self.file_deleted(change.path)
        for change in diff_result.conflicted:
            self.file_conflicted(change.path)

        summary_parts = []
        if diff_result.added:
            summary_parts.append(f"{len(diff_result.added)} added")
        if diff_result.modified:
            summary_parts.append(f"{len(diff_result.modified)} modified")
        if diff_result.deleted:
            summary_parts.append(f"{len(diff_result.deleted)} deleted")
        if diff_result.conflicted:
            summary_parts.append(f"{len(diff_result.conflicted)} conflicted")
        self.info(f"  Total: {', '.join(summary_parts)}")

    def set_json(self, key: str, value: Any) -> None:
        """Set a key in the JSON output."""
        self._json_data[key] = value

    def flush_json(self) -> None:
        """Print accumulated JSON data."""
        if self.json_mode and self._json_data:
            print(json.dumps(self._json_data, indent=2, sort_keys=True))

    def confirm(self, prompt: str) -> bool:
        """Ask for user confirmation. Returns True if yes."""
        if not self.is_tty:
            return False
        try:
            response = input(f"\n  {prompt} [y/N] ").strip().lower()
            return response in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            print()
            return False


class SyncEngine:
    """Push (home->repo) and pull (repo->home) with file copy."""

    def __init__(self, paths: PathResolver, output: Output):
        self.paths = paths
        self.output = output

    def push(self, diff_result: DiffResult, dry_run: bool = False) -> int:
        """Copy files from home to repo based on diff."""
        count = 0
        for change in diff_result.added + diff_result.modified:
            src = self.paths.relative_to_home(change.path)
            dst = self.paths.relative_to_repo(change.path)
            if dry_run:
                self.output.detail(f"Would copy {src} -> {dst}")
                count += 1
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(dst))
            count += 1

        for change in diff_result.deleted:
            dst = self.paths.relative_to_repo(change.path)
            if dry_run:
                self.output.detail(f"Would delete {dst}")
                count += 1
                continue
            if dst.exists():
                dst.unlink()
                count += 1
                # Clean up empty parent directories
                self._cleanup_empty_dirs(dst.parent, self.paths.repo_claude)

        return count

    def pull(self, diff_result: DiffResult, dry_run: bool = False) -> int:
        """Copy files from repo to home based on diff."""
        count = 0
        for change in diff_result.added + diff_result.modified:
            src = self.paths.relative_to_repo(change.path)
            dst = self.paths.relative_to_home(change.path)
            if dry_run:
                self.output.detail(f"Would copy {src} -> {dst}")
                count += 1
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(dst))
            # Set executable permission on .sh and .py files
            if change.path.endswith(".sh") or change.path.endswith(".py"):
                self._set_executable(dst)
            count += 1

        for change in diff_result.deleted:
            dst = self.paths.relative_to_home(change.path)
            if dry_run:
                self.output.detail(f"Would delete {dst}")
                count += 1
                continue
            if dst.exists():
                dst.unlink()
                count += 1
                self._cleanup_empty_dirs(dst.parent, self.paths.home_claude)

        return count

    @staticmethod
    def _set_executable(path: Path) -> None:
        """Set +x permission on a file."""
        current = path.stat().st_mode
        path.chmod(current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    @staticmethod
    def _cleanup_empty_dirs(dir_path: Path, stop_at: Path) -> None:
        """Remove empty directories up to stop_at."""
        current = dir_path
        while current != stop_at and current.is_dir():
            try:
                if any(current.iterdir()):
                    break
                current.rmdir()
                current = current.parent
            except OSError:
                break


# =============================================================================
# Phase 2: Safety
# =============================================================================

@dataclass
class SecretFinding:
    """A detected secret in a file."""
    file_path: str
    line_number: int
    pattern_name: str
    matched_text: str

    def to_dict(self) -> dict:
        return {
            "file_path": self.file_path,
            "line_number": self.line_number,
            "pattern_name": self.pattern_name,
            "matched_text": self.matched_text,
        }


class SecretScanner:
    """Scans files for potential secrets before push."""

    PATTERNS = [
        ("API Key (sk-*)", re.compile(r'sk-[a-zA-Z0-9]{20,}')),
        ("Anthropic API Key", re.compile(r'ANTHROPIC_API_KEY\s*[=:]\s*\S+')),
        ("Bearer Token", re.compile(r'[Bb]earer\s+[a-zA-Z0-9\-._~+/]+=*')),
        ("Private Key", re.compile(r'-----BEGIN\s+\w*\s*PRIVATE KEY-----')),
        ("Password Assignment", re.compile(r'(?:password|passwd|pass)\s*[=:]\s*\S+', re.IGNORECASE)),
        ("Connection String", re.compile(r'(?:mysql|postgres|mongodb|redis)://\S+')),
        ("AWS Key", re.compile(r'(?:AKIA|ASIA)[A-Z0-9]{16}')),
        ("GitHub Token", re.compile(r'(?:ghp|gho|ghu|ghs|ghr)_[a-zA-Z0-9]{36,}')),
        ("Generic Secret", re.compile(r'(?:secret|token|api_key)\s*[=:]\s*["\'][^"\']{8,}["\']', re.IGNORECASE)),
    ]

    @classmethod
    def scan_file(cls, path: Path, rel_path: str) -> List[SecretFinding]:
        """Scan a single file for secrets."""
        findings = []
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line_num, line in enumerate(f, 1):
                    for pattern_name, regex in cls.PATTERNS:
                        match = regex.search(line)
                        if match:
                            # Mask the matched text for display
                            text = match.group()
                            if len(text) > 12:
                                masked = text[:6] + "..." + text[-3:]
                            else:
                                masked = text[:3] + "..."
                            findings.append(SecretFinding(
                                file_path=rel_path,
                                line_number=line_num,
                                pattern_name=pattern_name,
                                matched_text=masked,
                            ))
        except (OSError, UnicodeDecodeError):
            pass
        return findings

    @classmethod
    def scan_directory(cls, base_dir: Path) -> List[SecretFinding]:
        """Scan all syncable files in a directory for secrets."""
        findings = []
        hashes = FileHasher.walk_directory(base_dir)
        for rel_path in hashes:
            abs_path = base_dir / rel_path
            findings.extend(cls.scan_file(abs_path, rel_path))
        return findings


class BackupManager:
    """Timestamped backups with retention pruning."""

    def __init__(self, backup_dir: Path = BACKUP_DIR):
        self.backup_dir = backup_dir

    def create_backup(self, source_dir: Path, label: str = "") -> Path:
        """Create a timestamped backup of a directory."""
        timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        name = f"{timestamp}"
        if label:
            name += f"-{label}"
        backup_path = self.backup_dir / name
        backup_path.mkdir(parents=True, exist_ok=True)

        file_count = 0
        if source_dir.exists():
            # Copy syncable files only
            hashes = FileHasher.walk_directory(source_dir)
            for rel_path in hashes:
                src = source_dir / rel_path
                dst = backup_path / rel_path
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src), str(dst))
                file_count += 1

        # Write backup metadata
        meta = {
            "timestamp": timestamp,
            "label": label,
            "source": str(source_dir),
            "file_count": file_count,
            "created_at": datetime.datetime.utcnow().isoformat() + "Z",
        }
        with open(backup_path / ".backup-meta.json", "w") as f:
            json.dump(meta, f, indent=2)
            f.write("\n")

        return backup_path

    def list_backups(self) -> List[Dict[str, Any]]:
        """List all available backups, newest first."""
        backups = []
        if not self.backup_dir.exists():
            return backups
        for entry in sorted(self.backup_dir.iterdir(), reverse=True):
            if entry.is_dir():
                meta_path = entry / ".backup-meta.json"
                if meta_path.exists():
                    with open(meta_path) as f:
                        meta = json.load(f)
                    meta["path"] = str(entry)
                    meta["name"] = entry.name
                    backups.append(meta)
                else:
                    backups.append({
                        "name": entry.name,
                        "path": str(entry),
                        "timestamp": entry.name[:15] if len(entry.name) >= 15 else entry.name,
                        "label": "",
                    })
        return backups

    def prune(self, keep: int = DEFAULT_BACKUP_RETENTION) -> int:
        """Remove old backups, keeping the most recent N."""
        backups = self.list_backups()
        pruned = 0
        if len(backups) <= keep:
            return pruned
        for backup in backups[keep:]:
            path = Path(backup["path"])
            if path.exists():
                shutil.rmtree(str(path))
                pruned += 1
        return pruned

    def get_backup(self, name: str) -> Optional[Path]:
        """Get a backup path by name (exact or partial match)."""
        if not self.backup_dir.exists():
            return None
        # Try exact match first
        exact = self.backup_dir / name
        if exact.exists():
            return exact
        # Try partial match
        for entry in sorted(self.backup_dir.iterdir(), reverse=True):
            if entry.is_dir() and name in entry.name:
                return entry
        return None


class SettingsMerger:
    """Deep merge for settings.json with portable/machine-specific key handling."""

    @staticmethod
    def extract_portable(settings: Dict[str, Any]) -> Dict[str, Any]:
        """Extract only portable keys from settings."""
        portable = {}
        for key in PORTABLE_SETTINGS_KEYS:
            if key in settings:
                portable[key] = copy.deepcopy(settings[key])
        return portable

    @staticmethod
    def deep_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
        """Deep merge overlay into base. overlay wins for leaf values."""
        result = copy.deepcopy(base)
        for key, value in overlay.items():
            if (key in result and isinstance(result[key], dict)
                    and isinstance(value, dict)):
                result[key] = SettingsMerger.deep_merge(result[key], value)
            else:
                result[key] = copy.deepcopy(value)
        return result

    @classmethod
    def merge_for_push(cls, home_settings: Dict[str, Any]) -> Dict[str, Any]:
        """Prepare settings for push: extract portable keys only."""
        return cls.extract_portable(home_settings)

    @classmethod
    def merge_for_pull(cls, local_settings: Dict[str, Any],
                       repo_settings: Dict[str, Any]) -> Dict[str, Any]:
        """Merge repo portable settings into local settings."""
        portable = cls.extract_portable(repo_settings)
        return cls.deep_merge(local_settings, portable)


# =============================================================================
# Phase 3: Diagnostics
# =============================================================================

@dataclass
class HealthCheck:
    """Result of a single health check."""
    name: str
    passed: bool
    message: str
    remediation: str = ""

    def to_dict(self) -> dict:
        d = {"name": self.name, "passed": self.passed, "message": self.message}
        if self.remediation:
            d["remediation"] = self.remediation
        return d


class Doctor:
    """Runs health checks on the sync setup."""

    def __init__(self, paths: PathResolver):
        self.paths = paths

    def run_all(self) -> List[HealthCheck]:
        """Run all health checks."""
        checks = [
            self._check_git_repo(),
            self._check_home_claude(),
            self._check_repo_claude(),
            self._check_manifest_valid(),
            self._check_file_hashes(),
            self._check_script_permissions(),
            self._check_git_status(),
            self._check_settings_keys(),
            self._check_no_excluded_in_portable(),
        ]
        return checks

    def _check_git_repo(self) -> HealthCheck:
        if self.paths.repo_root and (self.paths.repo_root / ".git").exists():
            return HealthCheck("git_repo", True, "Git repository found")
        return HealthCheck(
            "git_repo", False,
            "No git repository found",
            "Run 'git init' or navigate to a git repository",
        )

    def _check_home_claude(self) -> HealthCheck:
        if self.paths.home_claude.exists():
            return HealthCheck("home_claude", True, f"~/.claude exists at {self.paths.home_claude}")
        return HealthCheck(
            "home_claude", False,
            "~/.claude directory not found",
            "Claude Code creates this automatically. Run Claude Code first.",
        )

    def _check_repo_claude(self) -> HealthCheck:
        if self.paths.repo_claude and self.paths.repo_claude.exists():
            return HealthCheck("repo_claude", True, f"repo/claude exists at {self.paths.repo_claude}")
        return HealthCheck(
            "repo_claude", False,
            "repo/claude directory not found",
            "Run 'claude-sync init' to initialize sync",
        )

    def _check_manifest_valid(self) -> HealthCheck:
        if not self.paths.manifest_path:
            return HealthCheck("manifest", False, "No manifest path", "Initialize sync first")
        if not self.paths.manifest_path.exists():
            return HealthCheck(
                "manifest", False,
                "manifest.json not found",
                "Run 'claude-sync init' or 'claude-sync push' to create it",
            )
        try:
            manifest = Manifest.load(self.paths.manifest_path)
            if manifest.schema_version not in (1, MANIFEST_SCHEMA_VERSION):
                return HealthCheck(
                    "manifest", False,
                    f"Schema version unsupported: {manifest.schema_version} (expected 1 or {MANIFEST_SCHEMA_VERSION})",
                    "Re-run push to update manifest",
                )
            return HealthCheck("manifest", True, "manifest.json is valid")
        except (json.JSONDecodeError, KeyError) as e:
            return HealthCheck(
                "manifest", False,
                f"manifest.json is corrupt: {e}",
                "Delete manifest.json and re-run push",
            )

    def _check_file_hashes(self) -> HealthCheck:
        if not self.paths.manifest_path or not self.paths.manifest_path.exists():
            return HealthCheck("file_hashes", False, "No manifest to verify", "Push first")
        if not self.paths.repo_claude or not self.paths.repo_claude.exists():
            return HealthCheck("file_hashes", False, "No repo/claude to verify", "Push first")
        manifest = Manifest.load(self.paths.manifest_path)
        current = FileHasher.walk_directory(self.paths.repo_claude)
        mismatches = []
        for path, expected_hash in manifest.files.items():
            actual = current.get(path)
            if actual != expected_hash:
                mismatches.append(path)
        if mismatches:
            return HealthCheck(
                "file_hashes", False,
                f"{len(mismatches)} file(s) don't match manifest: {', '.join(mismatches[:3])}",
                "Run push to update or restore from backup",
            )
        return HealthCheck("file_hashes", True, "All file hashes match manifest")

    def _check_script_permissions(self) -> HealthCheck:
        """Check that .sh and .py files have executable permission."""
        issues = []
        for base in [self.paths.home_claude, self.paths.repo_claude]:
            if not base or not base.exists():
                continue
            hashes = FileHasher.walk_directory(base)
            for rel_path in hashes:
                if rel_path.endswith(".sh") or rel_path.endswith(".py"):
                    fpath = base / rel_path
                    if fpath.exists() and not os.access(str(fpath), os.X_OK):
                        issues.append(f"{base.name}/{rel_path}")
        if issues:
            return HealthCheck(
                "script_permissions", False,
                f"{len(issues)} script(s) missing +x: {', '.join(issues[:3])}",
                "Run 'chmod +x' on the listed scripts, or re-run pull",
            )
        return HealthCheck("script_permissions", True, "All scripts have correct permissions")

    def _check_git_status(self) -> HealthCheck:
        """Check if git working tree is clean."""
        if not self.paths.repo_root:
            return HealthCheck("git_clean", False, "No git repo", "")
        try:
            import subprocess
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(self.paths.repo_root),
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                return HealthCheck("git_clean", False, "Could not check git status", "")
            if result.stdout.strip():
                lines = result.stdout.strip().split("\n")
                return HealthCheck(
                    "git_clean", False,
                    f"{len(lines)} uncommitted change(s)",
                    "Commit or stash changes before syncing",
                )
            return HealthCheck("git_clean", True, "Git working tree is clean")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return HealthCheck("git_clean", False, "Git command not available", "Install git")

    def _check_settings_keys(self) -> HealthCheck:
        """Check that settings.json portable keys are valid."""
        if not self.paths.repo_claude:
            return HealthCheck("settings_keys", False, "No repo/claude", "")
        settings_path = self.paths.repo_claude / "settings.json"
        if not settings_path.exists():
            return HealthCheck("settings_keys", True, "No settings.json in repo (ok)")
        try:
            with open(settings_path) as f:
                data = json.load(f)
            non_portable = [k for k in data if k in MACHINE_SPECIFIC_KEYS]
            if non_portable:
                return HealthCheck(
                    "settings_keys", False,
                    f"Machine-specific keys in repo settings: {non_portable}",
                    "Re-run push to strip machine-specific keys",
                )
            return HealthCheck("settings_keys", True, "Settings keys are portable-only")
        except json.JSONDecodeError as e:
            return HealthCheck("settings_keys", False, f"Invalid settings.json: {e}", "Fix or delete settings.json")

    def _check_no_excluded_in_portable(self) -> HealthCheck:
        """Check that excluded paths haven't leaked into repo/claude."""
        if not self.paths.repo_claude or not self.paths.repo_claude.exists():
            return HealthCheck("no_excluded", True, "No repo/claude to check")
        leaked = []
        for excl in EXCLUDE_PATHS:
            check_path = self.paths.repo_claude / excl.rstrip("/")
            if check_path.exists():
                leaked.append(excl)
        if leaked:
            return HealthCheck(
                "no_excluded", False,
                f"Excluded paths found in repo: {leaked}",
                "Remove these from repo/claude and re-push",
            )
        return HealthCheck("no_excluded", True, "No excluded paths leaked into repo")


# =============================================================================
# Phase 5: Automation (watch + git hooks)
# =============================================================================

LOCKFILE_PATH = Path.home() / ".claude-sync.lock"
DEFAULT_WATCH_INTERVAL = 30  # seconds


class SyncEventHandler:
    """Handles OS-native file system events via watchdog with debouncing.

    Collects changed paths within a 500ms debounce window, with a maximum
    batch duration of 2000ms before forcing a flush.

    Only instantiated when watchdog is available (HAS_WATCHDOG is True).
    Inherits from FileSystemEventHandler at runtime to avoid import errors.
    """

    def __init__(self, sync_callback, watch_path: Path, debounce_ms: int = 500):
        # Inherit from FileSystemEventHandler dynamically to avoid
        # NameError when watchdog is not installed
        if HAS_WATCHDOG:
            FileSystemEventHandler.__init__(self)
        self.sync_callback = sync_callback
        self.watch_path = watch_path
        self.debounce_ms = debounce_ms
        self._pending_changes: set = set()
        self._debounce_timer: Optional[threading.Timer] = None
        self._first_event_time: Optional[float] = None
        self._max_batch_ms = 2000  # Force flush after 2s of accumulated events
        self._lock = threading.Lock()

    def _is_syncable(self, path: str) -> bool:
        """Check if a file change is for a syncable path."""
        try:
            abs_path = Path(path)
            rel = str(abs_path.relative_to(self.watch_path))
        except ValueError:
            return False

        # Check against EXCLUDE_PATHS first
        for excl in EXCLUDE_PATHS:
            if excl.endswith("/"):
                dir_name = excl.rstrip("/")
                if rel == dir_name or rel.startswith(dir_name + "/"):
                    return False
            else:
                if rel == excl:
                    return False

        # Check walk exclusion patterns
        for part in Path(rel).parts:
            for pattern in WALK_EXCLUDE_PATTERNS:
                if fnmatch.fnmatch(part, pattern):
                    return False

        # Check against SYNC_PATHS
        for sync_path in SYNC_PATHS:
            if sync_path.endswith("/"):
                prefix = sync_path.rstrip("/")
                if rel.startswith(prefix + "/") or rel == prefix:
                    return True
            else:
                if rel == sync_path:
                    return True

        # settings.json is also syncable
        if rel == "settings.json":
            return True

        return False

    def on_modified(self, event):
        if not event.is_directory and self._is_syncable(event.src_path):
            self._add_change(event.src_path)

    def on_created(self, event):
        if not event.is_directory and self._is_syncable(event.src_path):
            self._add_change(event.src_path)

    def on_deleted(self, event):
        if not event.is_directory and self._is_syncable(event.src_path):
            self._add_change(event.src_path)

    def _add_change(self, path: str):
        """Add a changed path with debouncing: 500ms window, 2s max batch."""
        with self._lock:
            self._pending_changes.add(path)
            now = time.time() * 1000  # milliseconds

            if self._first_event_time is None:
                self._first_event_time = now

            # Force flush if 2s since first event in this batch
            if now - self._first_event_time >= self._max_batch_ms:
                self._flush()
                return

            # Reset/start debounce timer
            if self._debounce_timer:
                self._debounce_timer.cancel()
            self._debounce_timer = threading.Timer(
                self.debounce_ms / 1000.0, self._flush
            )
            self._debounce_timer.daemon = True
            self._debounce_timer.start()

    def _flush(self):
        """Flush pending changes to the sync callback."""
        with self._lock:
            if self._pending_changes:
                changes = self._pending_changes.copy()
                self._pending_changes.clear()
                self._first_event_time = None
                if self._debounce_timer:
                    self._debounce_timer.cancel()
                    self._debounce_timer = None
                # Call sync in a thread to avoid blocking the observer
                threading.Thread(
                    target=self.sync_callback, args=(changes,), daemon=True
                ).start()

    def stop(self):
        """Cancel any pending debounce timer."""
        with self._lock:
            if self._debounce_timer:
                self._debounce_timer.cancel()
                self._debounce_timer = None


# Build SyncEventHandler's base class dynamically so it works even without watchdog
if HAS_WATCHDOG:
    # Re-create the class with proper inheritance from FileSystemEventHandler
    _OrigSyncEventHandler = SyncEventHandler
    class SyncEventHandler(FileSystemEventHandler, _OrigSyncEventHandler):  # type: ignore[no-redef]
        """Watchdog-aware event handler with debounced sync callback."""
        def __init__(self, *args, **kwargs):
            FileSystemEventHandler.__init__(self)
            _OrigSyncEventHandler.__init__(self, *args, **kwargs)


class FileWatcher:
    """Watches for file changes and triggers sync.

    Uses watchdog for OS-native file system events if available,
    falls back to polling otherwise.
    """

    def __init__(self, paths: PathResolver, output: Output, interval: int = DEFAULT_WATCH_INTERVAL):
        self.paths = paths
        self.output = output
        self.interval = interval
        self._running = False
        self._use_watchdog = HAS_WATCHDOG
        # Polling-specific state
        self._last_home_mtimes: Dict[str, float] = {}
        self._last_repo_mtimes: Dict[str, float] = {}

    def _acquire_lock(self) -> bool:
        """Acquire lockfile. Returns True if acquired."""
        if LOCKFILE_PATH.exists():
            # Check if the PID in the lockfile is still running
            try:
                pid = int(LOCKFILE_PATH.read_text().strip())
                os.kill(pid, 0)  # check if process exists
                return False  # another watcher is running
            except (ValueError, OSError):
                LOCKFILE_PATH.unlink(missing_ok=True)
        LOCKFILE_PATH.write_text(str(os.getpid()))
        return True

    def _release_lock(self) -> None:
        """Release lockfile."""
        try:
            if LOCKFILE_PATH.exists():
                pid = int(LOCKFILE_PATH.read_text().strip())
                if pid == os.getpid():
                    LOCKFILE_PATH.unlink(missing_ok=True)
        except (ValueError, OSError):
            LOCKFILE_PATH.unlink(missing_ok=True)

    def watch(self) -> int:
        """Run the watch loop. Returns exit code.

        Automatically selects watchdog (OS-native events) or polling
        based on whether the watchdog library is available.
        """
        if not self._acquire_lock():
            self.output.error("Another claude-sync watcher is already running.")
            return ExitCode.ERROR

        self._running = True

        def _signal_handler(signum, frame):
            self._running = False

        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)

        try:
            if self._use_watchdog:
                return self._watch_with_watchdog()
            else:
                return self._watch_with_polling()
        finally:
            self._release_lock()
            self.output.info("Watcher stopped.")

    def _watch_with_watchdog(self) -> int:
        """Watch using OS-native file system events via watchdog."""
        self.output.info("Watching for changes via watchdog (OS-native events)... (Ctrl+C to stop)")

        handler = SyncEventHandler(
            sync_callback=self._handle_watchdog_changes,
            watch_path=self.paths.home_claude,
        )
        observer = Observer()
        observer.schedule(handler, str(self.paths.home_claude), recursive=True)

        # Also watch repo/claude if it exists
        repo_handler = None
        if self.paths.repo_claude and self.paths.repo_claude.exists():
            repo_handler = SyncEventHandler(
                sync_callback=self._handle_watchdog_changes,
                watch_path=self.paths.repo_claude,
            )
            observer.schedule(repo_handler, str(self.paths.repo_claude), recursive=True)

        observer.start()
        try:
            while self._running:
                time.sleep(1)
        except KeyboardInterrupt:
            self._running = False
        finally:
            handler.stop()
            if repo_handler:
                repo_handler.stop()
            observer.stop()
            observer.join()

        return ExitCode.OK

    def _handle_watchdog_changes(self, changed_paths: set) -> None:
        """Handle a batch of changed files detected by watchdog.

        Determines whether the changes are in home or repo, computes
        a diff, and syncs accordingly.
        """
        # Classify changes as home or repo
        home_changed = False
        repo_changed = False
        for p in changed_paths:
            abs_p = Path(p)
            try:
                abs_p.relative_to(self.paths.home_claude)
                home_changed = True
            except ValueError:
                pass
            if self.paths.repo_claude:
                try:
                    abs_p.relative_to(self.paths.repo_claude)
                    repo_changed = True
                except ValueError:
                    pass

        if not home_changed and not repo_changed:
            return

        # Perform the same sync logic as the polling path
        self._do_sync(home_changed, repo_changed)

    def _watch_with_polling(self) -> int:
        """Fallback: polling-based file watching."""
        self.output.info(f"Watching for changes every {self.interval}s (polling)... (Ctrl+C to stop)")

        # Initial mtime snapshot
        self._last_home_mtimes = self._collect_mtimes(self.paths.home_claude)
        self._last_repo_mtimes = self._collect_mtimes(self.paths.repo_claude)

        try:
            while self._running:
                time.sleep(self.interval)
                if not self._running:
                    break
                self._check_and_sync_polling()
        except KeyboardInterrupt:
            self._running = False

        return ExitCode.OK

    def _collect_mtimes(self, base_dir: Path) -> Dict[str, float]:
        """Collect modification times for syncable files."""
        mtimes = {}
        if not base_dir or not base_dir.exists():
            return mtimes
        hashes = FileHasher.walk_directory(base_dir)
        for rel_path in hashes:
            fpath = base_dir / rel_path
            try:
                mtimes[rel_path] = fpath.stat().st_mtime
            except OSError:
                pass
        return mtimes

    def _has_changes(self, old_mtimes: Dict[str, float],
                     new_mtimes: Dict[str, float]) -> bool:
        """Check if any mtimes changed or files added/removed."""
        if set(old_mtimes.keys()) != set(new_mtimes.keys()):
            return True
        return any(old_mtimes[k] != new_mtimes[k] for k in old_mtimes)

    def _check_and_sync_polling(self) -> None:
        """Check for changes via polling and trigger sync if needed."""
        new_home_mtimes = self._collect_mtimes(self.paths.home_claude)
        new_repo_mtimes = self._collect_mtimes(self.paths.repo_claude)

        home_changed = self._has_changes(self._last_home_mtimes, new_home_mtimes)
        repo_changed = self._has_changes(self._last_repo_mtimes, new_repo_mtimes)

        if not home_changed and not repo_changed:
            return

        self._do_sync(home_changed, repo_changed)

        # Update mtime snapshots
        self._last_home_mtimes = self._collect_mtimes(self.paths.home_claude)
        self._last_repo_mtimes = self._collect_mtimes(self.paths.repo_claude)

    def _do_sync(self, home_changed: bool, repo_changed: bool) -> None:
        """Core sync logic shared by both watchdog and polling watchers."""
        # Load manifest for three-way merge
        manifest = Manifest.load(self.paths.manifest_path)
        base_hashes = manifest.files if manifest.files else None
        home_hashes = FileHasher.walk_directory(self.paths.home_claude)
        repo_hashes = FileHasher.walk_directory(self.paths.repo_claude)

        if home_changed:
            diff = DiffEngine.compare(home_hashes, repo_hashes, "push", base_hashes=base_hashes)
            if diff.has_conflicts:
                self.output.warning(f"[{self._timestamp()}] Conflicts detected — pausing auto-sync")
                for c in diff.conflicted:
                    self.output.warning(f"  CONFLICT {c.path}")
                self.output.info("  Run 'claude-sync resolve' to fix conflicts")
            elif diff.has_changes:
                self.output.info(f"[{self._timestamp()}] Home changes detected, auto-pushing...")
                engine = SyncEngine(self.paths, self.output)
                count = engine.push(diff)
                new_repo_hashes = FileHasher.walk_directory(self.paths.repo_claude)
                changed_paths = [c.path for c in diff.added + diff.modified]
                manifest.files = new_repo_hashes
                manifest.update_provenance("push")
                manifest.record_file_history(changed_paths, "push", new_repo_hashes)
                manifest.save(self.paths.manifest_path)
                self.output.success(f"[{self._timestamp()}] Auto-pushed {count} file(s)")

        if repo_changed and not home_changed:
            diff = DiffEngine.compare(home_hashes, repo_hashes, "pull", base_hashes=base_hashes)
            if diff.has_conflicts:
                self.output.warning(f"[{self._timestamp()}] Conflicts detected — pausing auto-sync")
            elif diff.has_changes:
                self.output.info(f"[{self._timestamp()}] Repo changes detected, auto-pulling...")
                engine = SyncEngine(self.paths, self.output)
                count = engine.pull(diff)
                new_home_hashes = FileHasher.walk_directory(self.paths.home_claude)
                changed_paths = [c.path for c in diff.added + diff.modified]
                manifest.files = FileHasher.walk_directory(self.paths.repo_claude)
                manifest.update_provenance("pull")
                manifest.record_file_history(changed_paths, "pull", new_home_hashes)
                manifest.save(self.paths.manifest_path)
                self.output.success(f"[{self._timestamp()}] Auto-pulled {count} file(s)")

    @staticmethod
    def _timestamp() -> str:
        return datetime.datetime.now().strftime("%H:%M:%S")


class GitHookManager:
    """Install/uninstall git hooks for auto-sync."""

    POST_MERGE_HOOK = textwrap.dedent("""\
        #!/bin/bash
        # claude-sync: auto-pull after git pull/merge
        if command -v python3 &>/dev/null; then
            SCRIPT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
            python3 "$SCRIPT_DIR/claude-sync.py" pull --yes --quiet 2>/dev/null || true
        fi
    """)

    PRE_PUSH_HOOK = textwrap.dedent("""\
        #!/bin/bash
        # claude-sync: auto-push before git push
        if command -v python3 &>/dev/null; then
            SCRIPT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
            python3 "$SCRIPT_DIR/claude-sync.py" push --yes --quiet 2>/dev/null || true
        fi
    """)

    HOOK_MARKER = "# claude-sync:"

    def __init__(self, repo_root: Path, output: Output):
        self.hooks_dir = repo_root / ".git" / "hooks"
        self.output = output

    def install(self) -> int:
        """Install post-merge and pre-push hooks."""
        if not self.hooks_dir.exists():
            self.output.error("No .git/hooks directory found.")
            return ExitCode.ERROR

        installed = 0
        for name, content in [("post-merge", self.POST_MERGE_HOOK),
                               ("pre-push", self.PRE_PUSH_HOOK)]:
            hook_path = self.hooks_dir / name
            if hook_path.exists():
                existing = hook_path.read_text()
                if self.HOOK_MARKER in existing:
                    self.output.info(f"  {name}: already installed")
                    continue
                # Append to existing hook
                with open(hook_path, "a") as f:
                    f.write(f"\n{content}")
                self.output.success(f"  {name}: appended to existing hook")
            else:
                hook_path.write_text(content)
                self.output.success(f"  {name}: installed")
            hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            installed += 1

        return ExitCode.OK

    def uninstall(self) -> int:
        """Remove claude-sync hooks."""
        removed = 0
        for name in ["post-merge", "pre-push"]:
            hook_path = self.hooks_dir / name
            if not hook_path.exists():
                continue
            content = hook_path.read_text()
            if self.HOOK_MARKER not in content:
                continue
            # Remove claude-sync lines
            lines = content.splitlines(keepends=True)
            filtered = [l for l in lines if self.HOOK_MARKER not in l
                        and "claude-sync" not in l]
            remaining = "".join(filtered).strip()
            if remaining and remaining != "#!/bin/bash":
                hook_path.write_text(remaining + "\n")
                self.output.success(f"  {name}: claude-sync lines removed")
            else:
                hook_path.unlink()
                self.output.success(f"  {name}: removed (was claude-sync only)")
            removed += 1

        if not removed:
            self.output.info("  No claude-sync hooks found to remove.")
        return ExitCode.OK


# =============================================================================
# Tracker / Pairing Configuration
# =============================================================================

class SyncConfig:
    """Manages ~/.claude/sync-config.json for tracker and pairing configuration."""

    CONFIG_PATH = Path.home() / ".claude" / "sync-config.json"

    DEFAULT: Dict[str, Any] = {
        "trackers": [],
        "auto_sync": {"enabled": True, "debounce_ms": 500},
        "security": {"require_pairing": True, "allow_unpaired_lan": True},
    }

    @classmethod
    def load(cls) -> Dict[str, Any]:
        """Load sync config from disk, returning defaults if not present."""
        if cls.CONFIG_PATH.exists():
            try:
                return json.loads(cls.CONFIG_PATH.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return copy.deepcopy(cls.DEFAULT)

    @classmethod
    def save(cls, config: Dict[str, Any]) -> None:
        """Persist sync config to disk."""
        cls.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        cls.CONFIG_PATH.write_text(
            json.dumps(config, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def add_tracker(cls, url: str, name: str) -> None:
        """Add a tracker server to the configuration."""
        config = cls.load()
        # Check for duplicates
        if any(t["url"] == url for t in config["trackers"]):
            print(f"  Tracker already configured: {url}")
            return
        config["trackers"].append({"url": url, "name": name, "enabled": True})
        cls.save(config)
        print(f"  Added tracker: {name} ({url})")

    @classmethod
    def remove_tracker(cls, url: str) -> None:
        """Remove a tracker server from the configuration."""
        config = cls.load()
        before = len(config["trackers"])
        config["trackers"] = [t for t in config["trackers"] if t["url"] != url]
        if len(config["trackers"]) == before:
            print(f"  Tracker not found: {url}")
            return
        cls.save(config)
        print(f"  Removed tracker: {url}")

    @classmethod
    def list_trackers(cls) -> None:
        """Print all configured trackers."""
        config = cls.load()
        if not config["trackers"]:
            print("  No trackers configured.")
            print("  Use 'claude-sync tracker add <url> --name <name>' to add one.")
            return
        for t in config["trackers"]:
            status = "enabled" if t.get("enabled", True) else "disabled"
            print(f"  {t['name']} ({t['url']}) [{status}]")

    @classmethod
    def toggle_tracker(cls, url: str, enabled: bool) -> None:
        """Enable or disable a tracker by URL."""
        config = cls.load()
        for t in config["trackers"]:
            if t["url"] == url:
                t["enabled"] = enabled
                cls.save(config)
                state = "Enabled" if enabled else "Disabled"
                print(f"  {state} tracker: {url}")
                return
        print(f"  Tracker not found: {url}")


def handle_tracker_command(args) -> int:
    """Dispatch tracker subcommands."""
    cmd = getattr(args, "tracker_command", None)
    if cmd == "add":
        SyncConfig.add_tracker(args.url, args.name)
    elif cmd == "remove":
        SyncConfig.remove_tracker(args.url)
    elif cmd == "list":
        SyncConfig.list_trackers()
    elif cmd == "enable":
        SyncConfig.toggle_tracker(args.url, True)
    elif cmd == "disable":
        SyncConfig.toggle_tracker(args.url, False)
    else:
        print("  Tracker commands:")
        print("    claude-sync tracker add <url> --name <name>   Add a tracker")
        print("    claude-sync tracker remove <url>              Remove a tracker")
        print("    claude-sync tracker list                      List trackers")
        print("    claude-sync tracker enable <url>              Enable a tracker")
        print("    claude-sync tracker disable <url>             Disable a tracker")
    return ExitCode.OK


def handle_pair_command(args, output: Output) -> int:
    """Handle the pair subcommand for device pairing."""
    device_id = getattr(args, "device_id", None)
    code = getattr(args, "code", None)

    if not device_id:
        # Generate a pairing code for this device
        my_id = f"{platform.node()}-{uuid.getnode()}"
        pairing_code = hashlib.sha256(
            f"{my_id}-{time.time()}".encode()
        ).hexdigest()[:8].upper()
        output.header("Device pairing")
        output.info(f"  Device ID:    {my_id}")
        output.info(f"  Pairing code: {pairing_code}")
        output.info("")
        output.info("  On the other device, run:")
        output.info(f"    claude-sync pair {my_id} --code {pairing_code}")

        # Store the pending pairing
        config = SyncConfig.load()
        if "pending_pairings" not in config:
            config["pending_pairings"] = {}
        config["pending_pairings"][pairing_code] = {
            "device_id": my_id,
            "created_at": datetime.datetime.utcnow().isoformat() + "Z",
        }
        SyncConfig.save(config)
        return ExitCode.OK
    else:
        # Complete a pairing with a remote device
        if not code:
            output.error("Pairing code required. Use --code <code>")
            return ExitCode.ERROR

        my_id = f"{platform.node()}-{uuid.getnode()}"
        config = SyncConfig.load()
        if "paired_devices" not in config:
            config["paired_devices"] = []

        # Check for duplicate
        if any(d["device_id"] == device_id for d in config["paired_devices"]):
            output.warning(f"Already paired with device: {device_id}")
            return ExitCode.OK

        config["paired_devices"].append({
            "device_id": device_id,
            "pairing_code": code,
            "paired_at": datetime.datetime.utcnow().isoformat() + "Z",
            "paired_by": my_id,
        })
        SyncConfig.save(config)
        output.success(f"Paired with device: {device_id}")
        return ExitCode.OK


# =============================================================================
# Phase 6: Ecosystem Intelligence
# =============================================================================

@dataclass
class SimilarityPair:
    """A pair of similar files with composite score."""
    path_a: str
    path_b: str
    score: float
    breakdown: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "path_a": self.path_a,
            "path_b": self.path_b,
            "score": round(self.score, 3),
            "breakdown": {k: round(v, 3) for k, v in self.breakdown.items()},
        }


# =============================================================================
# Phase 7: Worksets
# =============================================================================

@dataclass
class WorksetDefinition:
    """A named workset configuration for activating agent/skill subsets."""
    name: str
    description: str = ""
    tags: List[str] = field(default_factory=list)
    agents: List[str] = field(default_factory=list)
    skills: List[str] = field(default_factory=list)
    exclude_agents: List[str] = field(default_factory=list)
    exclude_skills: List[str] = field(default_factory=list)
    extends: List[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "WorksetDefinition":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class WorksetState:
    """Current workset activation state (machine-local)."""
    active_workset: Optional[str] = None
    activated_at: Optional[str] = None
    resolved_agents: List[str] = field(default_factory=list)
    resolved_skills: List[str] = field(default_factory=list)
    vault_initialized: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "WorksetState":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class WorksetEngine:
    """Vault-based workset management with hardlink activation."""

    PENDING_MARKER = ".workset-pending"

    def __init__(self, home_dir: Path):
        self.home_dir = home_dir
        self.vault_dir = home_dir / ".workset-vault"
        self.worksets_dir = home_dir / "worksets"
        self.state_path = self.worksets_dir / "_state.json"
        self.affinity_path = self.worksets_dir / "_affinity.json"
        self.agents_dir = home_dir / "agents"
        self.skills_dir = home_dir / "skills"

    # ---- State management ----

    def load_state(self) -> WorksetState:
        if self.state_path.exists():
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                return WorksetState.from_dict(data)
            except (json.JSONDecodeError, TypeError):
                pass
        return WorksetState()

    def save_state(self, state: WorksetState) -> None:
        self.worksets_dir.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(state.to_dict(), indent=2) + "\n", encoding="utf-8"
        )

    # ---- Definition management ----

    def load_definitions(self) -> Dict[str, WorksetDefinition]:
        defs = {}
        if not self.worksets_dir.exists():
            return defs
        for f in sorted(self.worksets_dir.glob("*.json")):
            if f.name.startswith("_"):
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                ws = WorksetDefinition.from_dict(data)
                defs[ws.name] = ws
            except (json.JSONDecodeError, TypeError, KeyError):
                continue
        return defs

    def save_definition(self, ws: WorksetDefinition) -> Path:
        self.worksets_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        if not ws.created_at:
            ws.created_at = now
        ws.updated_at = now
        path = self.worksets_dir / f"{ws.name}.json"
        path.write_text(json.dumps(ws.to_dict(), indent=2) + "\n", encoding="utf-8")
        return path

    def delete_definition(self, name: str) -> bool:
        path = self.worksets_dir / f"{name}.json"
        if path.exists():
            path.unlink()
            return True
        return False

    # ---- Tag parsing ----

    def parse_agent_tags(self) -> Dict[str, List[str]]:
        """Parse tags from all agent frontmatter in the vault (or agents dir if no vault)."""
        source = self.vault_dir / "agents" if self.vault_dir.exists() else self.agents_dir
        result: Dict[str, List[str]] = {}
        if not source.exists():
            return result
        for f in source.glob("*.md"):
            name = f.stem
            try:
                content = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            tags = self._extract_tags(content)
            result[name] = tags
        return result

    @staticmethod
    def _extract_tags(content: str) -> List[str]:
        """Extract tags list from YAML frontmatter."""
        if not content.startswith("---"):
            return []
        end = content.find("---", 3)
        if end <= 0:
            return []
        fm = content[3:end]
        for line in fm.splitlines():
            stripped = line.strip()
            if stripped.startswith("tags:"):
                val = stripped[5:].strip()
                if val.startswith("[") and val.endswith("]"):
                    items = val[1:-1].split(",")
                    return [item.strip().strip('"').strip("'") for item in items if item.strip()]
                elif val:
                    return [val.strip('"').strip("'")]
        return []

    # ---- Resolution ----

    def resolve_workset(self, name: str, definitions: Optional[Dict[str, WorksetDefinition]] = None,
                        _visited: Optional[Set[str]] = None) -> tuple:
        """Resolve a workset into concrete agent and skill lists.

        Returns (agent_names: List[str], skill_names: List[str]).
        """
        if definitions is None:
            definitions = self.load_definitions()
        if _visited is None:
            _visited = set()

        if name in _visited:
            return [], []
        _visited.add(name)

        ws = definitions.get(name)
        if not ws:
            return [], []

        agents: Set[str] = set()
        skills: Set[str] = set()

        # 1. Expand extends (recursive, cycle-detected)
        for parent_name in ws.extends:
            p_agents, p_skills = self.resolve_workset(parent_name, definitions, _visited)
            agents.update(p_agents)
            skills.update(p_skills)

        # 2. Expand tags
        if ws.tags:
            agent_tags = self.parse_agent_tags()
            for agent_name, atags in agent_tags.items():
                for t in ws.tags:
                    if t.lower() in [at.lower() for at in atags]:
                        agents.add(agent_name)
                        break

        # 3. Add explicit agents/skills
        agents.update(ws.agents)
        skills.update(ws.skills)

        # 4. Resolve skill dependencies: auto-include required agents
        #    Match skills to agents by name (most skills share agent names)
        source = self.vault_dir / "skills" if self.vault_dir.exists() else self.skills_dir
        if source.exists():
            for skill_name in list(skills):
                skill_md = source / skill_name / "SKILL.md"
                if skill_md.exists():
                    self._resolve_skill_deps(skill_md, agents, skills)

        # For agents without matching skills, auto-include the matching skill if it exists
        for agent_name in list(agents):
            skill_dir = source / agent_name
            if skill_dir.exists() and (skill_dir / "SKILL.md").exists():
                skills.add(agent_name)

        # 5. Apply exclusions last
        agents -= set(ws.exclude_agents)
        skills -= set(ws.exclude_skills)

        return sorted(agents), sorted(skills)

    def _resolve_skill_deps(self, skill_md: Path, agents: Set[str], skills: Set[str]) -> None:
        """Parse skill SKILL.md for requires: block and add dependencies."""
        try:
            content = skill_md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        if not content.startswith("---"):
            return
        end = content.find("---", 3)
        if end <= 0:
            return
        fm = content[3:end]
        in_requires = False
        for line in fm.splitlines():
            stripped = line.strip()
            if stripped.startswith("requires:"):
                in_requires = True
                continue
            if in_requires:
                if not line.startswith((" ", "\t")) and stripped:
                    break
                if stripped.startswith("agents:"):
                    val = stripped[7:].strip()
                    if val.startswith("["):
                        items = val[1:].rstrip("]").split(",")
                        agents.update(i.strip().strip('"').strip("'") for i in items if i.strip())
                elif stripped.startswith("skills:"):
                    val = stripped[7:].strip()
                    if val.startswith("["):
                        items = val[1:].rstrip("]").split(",")
                        skills.update(i.strip().strip('"').strip("'") for i in items if i.strip())

    # ---- Vault management ----

    def init_vault(self) -> tuple:
        """One-time migration: move agents/skills to vault, hardlink back.

        Returns (agent_count, skill_count).
        """
        state = self.load_state()
        if state.vault_initialized and self.vault_dir.exists():
            # Already initialized — reconcile by copying any new files to vault
            self._reconcile_vault()
            return self._count_vault()

        # Create vault directories
        vault_agents = self.vault_dir / "agents"
        vault_skills = self.vault_dir / "skills"
        vault_agents.mkdir(parents=True, exist_ok=True)
        vault_skills.mkdir(parents=True, exist_ok=True)

        # Move agents to vault
        agent_count = 0
        if self.agents_dir.exists():
            for f in self.agents_dir.glob("*.md"):
                dest = vault_agents / f.name
                shutil.copy2(str(f), str(dest))
                agent_count += 1

        # Move skills to vault (entire directories)
        skill_count = 0
        if self.skills_dir.exists():
            for d in self.skills_dir.iterdir():
                if d.is_dir() and not d.name.startswith("_"):
                    dest = vault_skills / d.name
                    if not dest.exists():
                        shutil.copytree(str(d), str(dest))
                    skill_count += 1
                elif d.is_file():
                    # Copy loose files (skill-rules.json etc)
                    dest = vault_skills / d.name
                    shutil.copy2(str(d), str(dest))

        # Mark as initialized
        state.vault_initialized = True
        state.active_workset = None
        self.save_state(state)

        # Hardlink everything back (full set active by default)
        self._activate_all()

        return agent_count, skill_count

    def _reconcile_vault(self) -> None:
        """Copy any agents/skills from active dirs that aren't in vault yet."""
        vault_agents = self.vault_dir / "agents"
        vault_skills = self.vault_dir / "skills"
        if self.agents_dir.exists():
            for f in self.agents_dir.glob("*.md"):
                dest = vault_agents / f.name
                if not dest.exists():
                    shutil.copy2(str(f), str(dest))
        if self.skills_dir.exists():
            for d in self.skills_dir.iterdir():
                if d.is_dir() and not d.name.startswith("_"):
                    dest = vault_skills / d.name
                    if not dest.exists():
                        shutil.copytree(str(d), str(dest))

    def _count_vault(self) -> tuple:
        vault_agents = self.vault_dir / "agents"
        vault_skills = self.vault_dir / "skills"
        ac = len(list(vault_agents.glob("*.md"))) if vault_agents.exists() else 0
        sc = len([d for d in vault_skills.iterdir() if d.is_dir()]) if vault_skills.exists() else 0
        return ac, sc

    # ---- Activation ----

    def activate(self, name: str) -> tuple:
        """Activate a workset: populate agents/skills with subset from vault.

        Returns (agent_count, skill_count).
        """
        state = self.load_state()
        if not state.vault_initialized:
            raise RuntimeError("Vault not initialized. Run 'claude-sync workset init' first.")

        definitions = self.load_definitions()
        if name not in definitions:
            raise ValueError(f"Workset '{name}' not found. Available: {', '.join(definitions.keys())}")

        agents, skills = self.resolve_workset(name, definitions)

        # Write pending marker for crash recovery
        pending = self.home_dir / self.PENDING_MARKER
        pending.write_text(name, encoding="utf-8")

        try:
            # Clear active directories
            self._clear_active_dirs()

            # Hardlink agents from vault
            vault_agents = self.vault_dir / "agents"
            self.agents_dir.mkdir(exist_ok=True)
            linked_agents = 0
            for agent_name in agents:
                src = vault_agents / f"{agent_name}.md"
                if src.exists():
                    self._hardlink_or_copy(src, self.agents_dir / f"{agent_name}.md")
                    linked_agents += 1

            # Hardlink skills from vault
            vault_skills = self.vault_dir / "skills"
            self.skills_dir.mkdir(exist_ok=True)
            linked_skills = 0
            for skill_name in skills:
                src_dir = vault_skills / skill_name
                if src_dir.is_dir():
                    self._hardlink_tree(src_dir, self.skills_dir / skill_name)
                    linked_skills += 1
            # Also copy loose files in skills/ (like skill-rules.json)
            for f in vault_skills.iterdir():
                if f.is_file():
                    self._hardlink_or_copy(f, self.skills_dir / f.name)

            # Update state
            state.active_workset = name
            state.activated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
            state.resolved_agents = agents
            state.resolved_skills = skills
            self.save_state(state)

            # Record affinity
            self._record_affinity(name)

        finally:
            # Remove pending marker
            if pending.exists():
                pending.unlink()

        return linked_agents, linked_skills

    def deactivate(self) -> tuple:
        """Restore full set from vault. Returns (agent_count, skill_count)."""
        state = self.load_state()
        if not state.vault_initialized:
            raise RuntimeError("Vault not initialized.")

        self._activate_all()

        state.active_workset = None
        state.activated_at = None
        state.resolved_agents = []
        state.resolved_skills = []
        self.save_state(state)

        return self._count_active()

    def _activate_all(self) -> None:
        """Hardlink everything from vault to active directories."""
        self._clear_active_dirs()

        vault_agents = self.vault_dir / "agents"
        vault_skills = self.vault_dir / "skills"

        self.agents_dir.mkdir(exist_ok=True)
        self.skills_dir.mkdir(exist_ok=True)

        if vault_agents.exists():
            for f in vault_agents.glob("*.md"):
                self._hardlink_or_copy(f, self.agents_dir / f.name)

        if vault_skills.exists():
            for item in vault_skills.iterdir():
                if item.is_dir():
                    self._hardlink_tree(item, self.skills_dir / item.name)
                elif item.is_file():
                    self._hardlink_or_copy(item, self.skills_dir / item.name)

    def _clear_active_dirs(self) -> None:
        """Remove all contents of agents/ and skills/ directories."""
        if self.agents_dir.exists():
            shutil.rmtree(str(self.agents_dir))
        if self.skills_dir.exists():
            shutil.rmtree(str(self.skills_dir))

    def _count_active(self) -> tuple:
        ac = len(list(self.agents_dir.glob("*.md"))) if self.agents_dir.exists() else 0
        sc = len([d for d in self.skills_dir.iterdir() if d.is_dir()]) if self.skills_dir.exists() else 0
        return ac, sc

    @staticmethod
    def _hardlink_or_copy(src: Path, dest: Path) -> None:
        """Create hardlink, falling back to copy if cross-filesystem."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(str(src), str(dest))
        except OSError:
            shutil.copy2(str(src), str(dest))

    @staticmethod
    def _hardlink_tree(src_dir: Path, dest_dir: Path) -> None:
        """Recursively hardlink a directory tree."""
        dest_dir.mkdir(parents=True, exist_ok=True)
        for item in src_dir.iterdir():
            dest = dest_dir / item.name
            if item.is_dir():
                WorksetEngine._hardlink_tree(item, dest)
            elif item.is_file():
                WorksetEngine._hardlink_or_copy(item, dest)

    # ---- Affinity Engine ----

    def _load_affinity(self) -> dict:
        if self.affinity_path.exists():
            try:
                return json.loads(self.affinity_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, TypeError):
                pass
        return {"projects": {}, "language_affinity": {}}

    def _save_affinity(self, data: dict) -> None:
        self.worksets_dir.mkdir(parents=True, exist_ok=True)
        self.affinity_path.write_text(
            json.dumps(data, indent=2) + "\n", encoding="utf-8"
        )

    def _record_affinity(self, workset_name: str) -> None:
        """Record this activation in affinity data."""
        affinity = self._load_affinity()
        project_key = self._detect_project_key()
        languages = self._detect_languages()

        if project_key:
            proj = affinity["projects"].setdefault(project_key, {
                "activations": {}, "languages": [], "last_workset": None,
                "last_activated": None,
            })
            proj["activations"][workset_name] = proj["activations"].get(workset_name, 0) + 1
            proj["languages"] = languages
            proj["last_workset"] = workset_name
            proj["last_activated"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

        for lang in languages:
            lang_entry = affinity["language_affinity"].setdefault(lang, {})
            lang_entry[workset_name] = lang_entry.get(workset_name, 0) + 1

        self._save_affinity(affinity)

    @staticmethod
    def _detect_project_key() -> Optional[str]:
        """Get project identity from git remote."""
        try:
            import subprocess
            result = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                url = result.stdout.strip()
                # Normalize: git@github.com:user/repo.git -> github.com/user/repo
                url = re.sub(r"^(https?://|git@)", "", url)
                url = re.sub(r"\.git$", "", url)
                url = url.replace(":", "/")
                return url
        except Exception:
            pass
        return None

    @staticmethod
    def _detect_languages() -> List[str]:
        """Detect languages in current directory by file extensions and markers."""
        cwd = Path.cwd()
        lang_map = {
            "Package.swift": "swift", "*.xcodeproj": "swift",
            "package.json": "javascript", "tsconfig.json": "typescript",
            "Cargo.toml": "rust", "go.mod": "go",
            "pyproject.toml": "python", "setup.py": "python", "requirements.txt": "python",
            "Gemfile": "ruby", "build.gradle": "kotlin", "pom.xml": "java",
        }
        detected = set()
        for marker, lang in lang_map.items():
            if "*" in marker:
                if list(cwd.glob(marker)):
                    detected.add(lang)
            elif (cwd / marker).exists():
                detected.add(lang)

        # Also check common file extensions in top-level src
        ext_map = {".swift": "swift", ".py": "python", ".ts": "typescript",
                   ".js": "javascript", ".rs": "rust", ".go": "go", ".rb": "ruby",
                   ".java": "java", ".kt": "kotlin"}
        try:
            for f in list(cwd.rglob("*"))[:500]:  # Cap to avoid slow repos
                if f.suffix in ext_map:
                    detected.add(ext_map[f.suffix])
        except OSError:
            pass
        return sorted(detected)

    def suggest_workset(self) -> Optional[tuple]:
        """Suggest a workset based on project affinity.

        Returns (workset_name, confidence, reason) or None.
        """
        affinity = self._load_affinity()
        definitions = self.load_definitions()
        if not definitions:
            return None

        # Strategy 1: Project-based affinity
        project_key = self._detect_project_key()
        if project_key and project_key in affinity.get("projects", {}):
            proj = affinity["projects"][project_key]
            activations = proj.get("activations", {})
            if activations:
                total = sum(activations.values())
                best = max(activations, key=activations.get)
                confidence = activations[best] / total if total > 0 else 0
                if best in definitions:
                    return (best, confidence, f"Used {activations[best]}/{total} times for {project_key}")

        # Strategy 2: Language-based affinity
        languages = self._detect_languages()
        if languages and affinity.get("language_affinity"):
            scores: Dict[str, int] = {}
            for lang in languages:
                lang_affinities = affinity["language_affinity"].get(lang, {})
                for ws_name, count in lang_affinities.items():
                    if ws_name in definitions:
                        scores[ws_name] = scores.get(ws_name, 0) + count
            if scores:
                best = max(scores, key=scores.get)
                total = sum(scores.values())
                confidence = scores[best] / total if total > 0 else 0
                return (best, confidence, f"Language match ({', '.join(languages)})")

        # Strategy 3: Tag matching against project markers
        if languages:
            lang_tag_map = {
                "swift": ["Dev"], "python": ["Dev"], "typescript": ["Dev"],
                "javascript": ["Dev"], "rust": ["Dev"], "go": ["Dev"],
            }
            matched_tags = set()
            for lang in languages:
                matched_tags.update(lang_tag_map.get(lang, []))
            if matched_tags:
                for ws_name, ws_def in definitions.items():
                    if set(ws_def.tags) & matched_tags:
                        return (ws_name, 0.3, f"Tag match from detected languages")

        return None

    # ---- Recovery ----

    def recover_if_needed(self) -> bool:
        """Check for interrupted activation and recover."""
        pending = self.home_dir / self.PENDING_MARKER
        if pending.exists():
            pending.unlink()
            self._activate_all()
            state = self.load_state()
            state.active_workset = None
            self.save_state(state)
            return True
        return False


# =============================================================================
# Phase 8: Skill Genome
# =============================================================================

@dataclass
class SkillDependencies:
    """Dependencies declared by a skill."""
    skills: List[str] = field(default_factory=list)
    agents: List[str] = field(default_factory=list)
    mcp_servers: List[str] = field(default_factory=list)
    rules: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "skills": self.skills,
            "agents": self.agents,
            "mcp_servers": self.mcp_servers,
            "rules": self.rules,
        }

    @property
    def has_any(self) -> bool:
        return bool(self.skills or self.agents or self.mcp_servers or self.rules)


@dataclass
class SkillGenome:
    """Complete genome of a skill: metadata + dependencies + triggers."""
    name: str
    description: str
    version: str
    user_invocable: bool
    allowed_tools: List[str]
    requires: SkillDependencies
    triggers: Optional[Dict] = None
    path: str = ""

    def to_dict(self) -> dict:
        d = {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "user_invocable": self.user_invocable,
            "allowed_tools": self.allowed_tools,
            "requires": self.requires.to_dict(),
            "path": self.path,
        }
        if self.triggers:
            d["triggers"] = self.triggers
        return d


@dataclass
class DependencyNode:
    """A node in the cross-type dependency graph."""
    name: str
    node_type: str          # "skill", "agent", "mcp_server", "rule"
    exists: bool
    dependents: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)


@dataclass
class HealthIssue:
    """A dependency health problem."""
    skill_name: str
    issue_type: str         # "missing_skill", "missing_agent", "missing_mcp", etc.
    message: str
    severity: str           # "error", "warning", "info"
    remediation: str

    def to_dict(self) -> dict:
        return {
            "skill_name": self.skill_name,
            "issue_type": self.issue_type,
            "message": self.message,
            "severity": self.severity,
            "remediation": self.remediation,
        }


class SkillGenomeEngine:
    """Dependency resolution and health checking for the skill ecosystem."""

    def __init__(self, home_dir: Path, repo_dir: Optional[Path] = None):
        self.home_dir = home_dir
        self.repo_dir = repo_dir
        self._genome_cache: Dict[str, SkillGenome] = {}

    @staticmethod
    def _parse_inline_list(val: str) -> List[str]:
        """Parse [a, b, c] inline list format."""
        val = val.strip()
        if val.startswith("[") and val.endswith("]"):
            items = val[1:-1].split(",")
            return [item.strip().strip('"').strip("'") for item in items if item.strip()]
        return [val] if val else []

    def _parse_genome_frontmatter(self, content: str) -> Dict:
        """Parse SKILL.md frontmatter including nested requires: block.

        Purpose-built for skill frontmatter schema. Handles inline lists
        [a, b, c] and block-style indented sub-keys under requires:.
        """
        meta: Dict[str, Any] = {}
        if not content.startswith("---"):
            return meta
        end = content.find("---", 3)
        if end <= 0:
            return meta

        fm = content[3:end]
        current_section: Optional[str] = None

        for line in fm.strip().splitlines():
            if not line.strip():
                continue

            stripped = line.lstrip()
            indent = len(line) - len(stripped)

            if ":" not in stripped:
                continue

            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip()

            if indent == 0:
                if key == "requires" and not val:
                    current_section = "requires"
                    meta["requires"] = {}
                else:
                    current_section = None
                    if val.startswith("["):
                        meta[key] = self._parse_inline_list(val)
                    else:
                        meta[key] = val.strip('"').strip("'")
            elif indent > 0 and current_section == "requires":
                meta.setdefault("requires", {})[key] = self._parse_inline_list(val)

        return meta

    def parse_genome(self, skill_dir: Path) -> Optional[SkillGenome]:
        """Parse a skill directory into a SkillGenome."""
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            return None

        content = skill_md.read_text(encoding="utf-8", errors="replace")
        meta = self._parse_genome_frontmatter(content)

        name = meta.get("name", skill_dir.name)
        if isinstance(name, list):
            name = name[0] if name else skill_dir.name
        desc = meta.get("description", "")
        if isinstance(desc, list):
            desc = desc[0] if desc else ""
        version = meta.get("version", "0.0.0")
        if isinstance(version, list):
            version = version[0] if version else "0.0.0"
        user_invocable = str(
            meta.get("user_invocable", meta.get("user-invocable", "false"))
        ).lower() == "true"

        allowed_tools = meta.get("allowed-tools", [])
        if isinstance(allowed_tools, str):
            allowed_tools = self._parse_inline_list(allowed_tools)

        requires_raw = meta.get("requires", {})
        if not isinstance(requires_raw, dict):
            requires_raw = {}
        requires = SkillDependencies(
            skills=requires_raw.get("skills", []),
            agents=requires_raw.get("agents", []),
            mcp_servers=requires_raw.get("mcp-servers", requires_raw.get("mcp_servers", [])),
            rules=requires_raw.get("rules", []),
        )

        # Load triggers.json if it exists
        triggers = None
        triggers_path = skill_dir / "triggers.json"
        if triggers_path.exists():
            try:
                with open(triggers_path) as f:
                    triggers = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

        return SkillGenome(
            name=name,
            description=desc,
            version=version,
            user_invocable=user_invocable,
            allowed_tools=allowed_tools if isinstance(allowed_tools, list) else [],
            requires=requires,
            triggers=triggers,
            path=f"skills/{skill_dir.name}/SKILL.md",
        )

    def scan_all(self, base_dir: Optional[Path] = None) -> Dict[str, SkillGenome]:
        """Scan all skill directories and return genomes."""
        if self._genome_cache and base_dir is None:
            return self._genome_cache

        search_dir = base_dir or self.home_dir
        skills_dir = search_dir / "skills"
        if not skills_dir.exists():
            return {}

        result: Dict[str, SkillGenome] = {}
        for entry in sorted(skills_dir.iterdir()):
            if entry.is_dir() and not entry.name.startswith((".", "_")):
                genome = self.parse_genome(entry)
                if genome:
                    result[genome.name] = genome

        if base_dir is None:
            self._genome_cache = result
        return result

    def resolve_dependencies(self, skill_name: str,
                             genomes: Optional[Dict[str, SkillGenome]] = None
                             ) -> Tuple[List[str], List[str]]:
        """Resolve dependency tree using DFS post-order topological sort.
        Returns (install_order, cycles). install_order has deps first."""
        if genomes is None:
            genomes = self.scan_all()

        visited: Set[str] = set()
        in_stack: Set[str] = set()
        result: List[str] = []
        cycles: List[str] = []

        def dfs(name: str) -> None:
            if name in visited:
                return
            if name in in_stack:
                cycles.append(name)
                return
            in_stack.add(name)

            genome = genomes.get(name)
            if genome:
                for dep in genome.requires.skills:
                    dfs(dep)

            in_stack.discard(name)
            visited.add(name)
            result.append(name)

        dfs(skill_name)
        return result, cycles

    def build_full_graph(self, genomes: Optional[Dict[str, SkillGenome]] = None
                         ) -> Dict[str, DependencyNode]:
        """Build complete cross-type dependency graph (skills + agents + MCP + rules)."""
        if genomes is None:
            genomes = self.scan_all()

        graph: Dict[str, DependencyNode] = {}

        for name, genome in genomes.items():
            skill_key = f"skill:{name}"
            if skill_key not in graph:
                graph[skill_key] = DependencyNode(
                    name=name, node_type="skill", exists=True,
                )

            for dep_skill in genome.requires.skills:
                dep_key = f"skill:{dep_skill}"
                graph[skill_key].dependencies.append(dep_key)
                if dep_key not in graph:
                    graph[dep_key] = DependencyNode(
                        name=dep_skill, node_type="skill",
                        exists=dep_skill in genomes,
                    )
                graph[dep_key].dependents.append(skill_key)

            for dep_agent in genome.requires.agents:
                dep_key = f"agent:{dep_agent}"
                graph[skill_key].dependencies.append(dep_key)
                if dep_key not in graph:
                    agent_path = self.home_dir / "agents" / f"{dep_agent}.md"
                    graph[dep_key] = DependencyNode(
                        name=dep_agent, node_type="agent",
                        exists=agent_path.exists(),
                    )
                graph[dep_key].dependents.append(skill_key)

            for dep_mcp in genome.requires.mcp_servers:
                dep_key = f"mcp:{dep_mcp}"
                graph[skill_key].dependencies.append(dep_key)
                if dep_key not in graph:
                    graph[dep_key] = DependencyNode(
                        name=dep_mcp, node_type="mcp_server",
                        exists=self._mcp_server_exists(dep_mcp),
                    )
                graph[dep_key].dependents.append(skill_key)

            for dep_rule in genome.requires.rules:
                dep_key = f"rule:{dep_rule}"
                graph[skill_key].dependencies.append(dep_key)
                if dep_key not in graph:
                    rule_path = self.home_dir / "rules" / f"{dep_rule}.md"
                    graph[dep_key] = DependencyNode(
                        name=dep_rule, node_type="rule",
                        exists=rule_path.exists(),
                    )
                graph[dep_key].dependents.append(skill_key)

        return graph

    def check_health(self, genomes: Optional[Dict[str, SkillGenome]] = None
                     ) -> List[HealthIssue]:
        """Verify all declared dependencies exist."""
        if genomes is None:
            genomes = self.scan_all()

        issues: List[HealthIssue] = []
        graph = self.build_full_graph(genomes)

        # Missing dependencies
        for key, node in sorted(graph.items()):
            if not node.exists and node.dependents:
                dependent_names = [
                    graph[d].name for d in node.dependents if d in graph
                ]
                issues.append(HealthIssue(
                    skill_name=", ".join(dependent_names),
                    issue_type=f"missing_{node.node_type}",
                    message=f"{node.node_type} '{node.name}' required but not found",
                    severity="error" if node.node_type in ("skill", "agent") else "warning",
                    remediation=self._remediation_for(node),
                ))

        # Circular dependencies
        checked_cycles: Set[str] = set()
        for name in genomes:
            _, cycles = self.resolve_dependencies(name, genomes)
            for c in cycles:
                if c not in checked_cycles:
                    checked_cycles.add(c)
                    issues.append(HealthIssue(
                        skill_name=name,
                        issue_type="circular_dependency",
                        message=f"Circular dependency detected involving '{c}'",
                        severity="error",
                        remediation="Remove or restructure the circular skill dependencies",
                    ))

        return issues

    @staticmethod
    def _remediation_for(node: DependencyNode) -> str:
        if node.node_type == "skill":
            return f"Install skill '{node.name}' or remove from requires"
        elif node.node_type == "agent":
            return f"Create agents/{node.name}.md or remove from requires"
        elif node.node_type == "mcp_server":
            return f"Add '{node.name}' to mcp_config.json or remove from requires"
        elif node.node_type == "rule":
            return f"Create rules/{node.name}.md or remove from requires"
        return "Check dependency configuration"

    def _mcp_server_exists(self, name: str) -> bool:
        """Check if an MCP server is configured in mcp_config.json."""
        mcp_path = self.home_dir / "mcp_config.json"
        if not mcp_path.exists():
            return False
        try:
            with open(mcp_path) as f:
                config = json.load(f)
            return name in config.get("mcpServers", {})
        except (json.JSONDecodeError, OSError):
            return False

    def extract_triggers(self, skill_rules_path: Path) -> Dict[str, int]:
        """Split monolith skill-rules.json into per-skill triggers.json files.
        Returns {"extracted": N, "skipped": N, "agent_triggers": N}."""
        if not skill_rules_path.exists():
            return {"extracted": 0, "skipped": 0, "agent_triggers": 0}

        with open(skill_rules_path) as f:
            rules = json.load(f)

        skills_section = rules.get("skills", {})
        agents_section = rules.get("agents", {})
        skills_dir = skill_rules_path.parent

        extracted = 0
        skipped = 0

        for skill_name, config in skills_section.items():
            skill_dir = skills_dir / skill_name
            if not skill_dir.is_dir():
                skipped += 1
                continue

            triggers_path = skill_dir / "triggers.json"
            with open(triggers_path, "w") as f:
                json.dump(config, f, indent=2, sort_keys=True)
                f.write("\n")
            extracted += 1

        agent_count = 0
        if agents_section:
            agent_triggers_path = skills_dir / "_agent-triggers.json"
            with open(agent_triggers_path, "w") as f:
                json.dump(agents_section, f, indent=2, sort_keys=True)
                f.write("\n")
            agent_count = len(agents_section)

        return {"extracted": extracted, "skipped": skipped, "agent_triggers": agent_count}

    def assemble_triggers(self, skills_dir: Path) -> Dict:
        """Rebuild skill-rules.json from per-skill triggers.json files."""
        skills_section: Dict[str, Any] = {}
        agents_section: Dict[str, Any] = {}

        for entry in sorted(skills_dir.iterdir()):
            if entry.is_dir() and not entry.name.startswith((".", "_")):
                triggers_path = entry / "triggers.json"
                if triggers_path.exists():
                    try:
                        with open(triggers_path) as f:
                            skills_section[entry.name] = json.load(f)
                    except (json.JSONDecodeError, OSError):
                        pass

        agent_triggers_path = skills_dir / "_agent-triggers.json"
        if agent_triggers_path.exists():
            try:
                with open(agent_triggers_path) as f:
                    agents_section = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

        return {
            "version": "1.0",
            "description": "Skill activation triggers for Claude Code (assembled from per-skill triggers)",
            "skills": skills_section,
            "agents": agents_section,
            "notes": {
                "assembled": True,
                "source": "Per-skill triggers.json files",
            },
        }

    def has_atomized_triggers(self, skills_dir: Path) -> bool:
        """Check if any per-skill triggers.json files exist."""
        if not skills_dir.exists():
            return False
        for entry in skills_dir.iterdir():
            if entry.is_dir() and (entry / "triggers.json").exists():
                return True
        return False

    def format_tree(self, skill_name: str, genomes: Dict[str, SkillGenome],
                    indent: int = 0, visited: Optional[Set[str]] = None) -> List[str]:
        """Format dependency tree as indented text lines."""
        if visited is None:
            visited = set()
        lines: List[str] = []
        genome = genomes.get(skill_name)
        prefix = "  " * indent
        marker = "├── " if indent > 0 else ""

        version_tag = f" v{genome.version}" if genome and genome.version != "0.0.0" else ""
        lines.append(f"{prefix}{marker}{skill_name}{version_tag}")

        if skill_name in visited:
            return lines
        visited.add(skill_name)

        if genome:
            sub_indent = indent + 1
            sub_prefix = "  " * sub_indent

            for dep in genome.requires.skills:
                if dep in genomes:
                    lines.extend(self.format_tree(dep, genomes, sub_indent, visited))
                else:
                    lines.append(f"{sub_prefix}├── {dep} (missing)")

            for agent in genome.requires.agents:
                lines.append(f"{sub_prefix}├── {agent} (agent)")

            for mcp in genome.requires.mcp_servers:
                lines.append(f"{sub_prefix}├── {mcp} (mcp)")

            for rule in genome.requires.rules:
                lines.append(f"{sub_prefix}├── {rule} (rule)")

        return lines

    def package_skill(self, skill_name: str, genomes: Dict[str, SkillGenome],
                      output_path: Path) -> List[str]:
        """Package a skill + all deps as tar.gz. Returns included file list."""
        import tarfile

        install_order, cycles = self.resolve_dependencies(skill_name, genomes)
        if cycles:
            raise ValueError(f"Cannot package: circular dependencies involving {cycles}")

        included: List[str] = []
        added_arcnames: Set[str] = set()

        with tarfile.open(output_path, "w:gz") as tar:
            for name in install_order:
                genome = genomes.get(name)
                skill_dir = self.home_dir / "skills" / name
                if skill_dir.is_dir():
                    for fpath in sorted(skill_dir.rglob("*")):
                        if fpath.is_file():
                            arcname = str(fpath.relative_to(self.home_dir))
                            if arcname not in added_arcnames:
                                tar.add(fpath, arcname=arcname)
                                included.append(arcname)
                                added_arcnames.add(arcname)

                if not genome:
                    continue
                for agent_name in genome.requires.agents:
                    agent_path = self.home_dir / "agents" / f"{agent_name}.md"
                    if agent_path.exists():
                        arcname = str(agent_path.relative_to(self.home_dir))
                        if arcname not in added_arcnames:
                            tar.add(agent_path, arcname=arcname)
                            included.append(arcname)
                            added_arcnames.add(arcname)

                for rule_name in genome.requires.rules:
                    rule_path = self.home_dir / "rules" / f"{rule_name}.md"
                    if rule_path.exists():
                        arcname = str(rule_path.relative_to(self.home_dir))
                        if arcname not in added_arcnames:
                            tar.add(rule_path, arcname=arcname)
                            included.append(arcname)
                            added_arcnames.add(arcname)

        return included

    def install_skill(self, skill_name: str, repo_dir: Path, home_dir: Path,
                      genomes: Dict[str, SkillGenome]
                      ) -> Tuple[List[str], List[str]]:
        """Install a skill + deps from repo to home.
        Returns (installed_paths, warnings)."""
        install_order, cycles = self.resolve_dependencies(skill_name, genomes)
        if cycles:
            raise ValueError(f"Cannot install: circular dependencies involving {cycles}")

        # Validate all deps exist in repo BEFORE copying
        missing: List[str] = []
        for name in install_order:
            if not (repo_dir / "skills" / name).is_dir():
                missing.append(f"skills/{name}/")

        genome = genomes.get(skill_name)
        warnings: List[str] = []
        if genome:
            for agent_name in genome.requires.agents:
                if not (repo_dir / "agents" / f"{agent_name}.md").exists():
                    missing.append(f"agents/{agent_name}.md")
            for rule_name in genome.requires.rules:
                if not (repo_dir / "rules" / f"{rule_name}.md").exists():
                    missing.append(f"rules/{rule_name}.md")
            for mcp_name in genome.requires.mcp_servers:
                if not self._mcp_server_exists(mcp_name):
                    warnings.append(
                        f"MCP server '{mcp_name}' not in mcp_config.json "
                        f"— add it to use {skill_name}"
                    )

        if missing:
            raise FileNotFoundError(f"Missing in repo: {', '.join(missing)}")

        # Copy atomically (all skills, then agents, then rules)
        installed: List[str] = []
        for name in install_order:
            src_dir = repo_dir / "skills" / name
            dst_dir = home_dir / "skills" / name
            dst_dir.mkdir(parents=True, exist_ok=True)
            for fpath in sorted(src_dir.rglob("*")):
                if fpath.is_file():
                    rel = fpath.relative_to(src_dir)
                    dest = dst_dir / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(fpath, dest)
            installed.append(f"skills/{name}/")

        if genome:
            for agent_name in genome.requires.agents:
                src = repo_dir / "agents" / f"{agent_name}.md"
                if src.exists():
                    dest = home_dir / "agents" / f"{agent_name}.md"
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest)
                    installed.append(f"agents/{agent_name}.md")
            for rule_name in genome.requires.rules:
                src = repo_dir / "rules" / f"{rule_name}.md"
                if src.exists():
                    dest = home_dir / "rules" / f"{rule_name}.md"
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest)
                    installed.append(f"rules/{rule_name}.md")

        return installed, warnings


class EcosystemAnalyzer:
    """Multi-signal similarity engine for agents, skills, and rules."""

    # Signal weights for composite score
    WEIGHT_NAME = 0.3
    WEIGHT_KEYWORDS = 0.3
    WEIGHT_DESCRIPTION = 0.25
    WEIGHT_STRUCTURE = 0.15

    # Common stop words to exclude from keyword extraction
    STOP_WORDS = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "shall", "can", "need", "dare", "ought",
        "used", "to", "of", "in", "for", "on", "with", "at", "by", "from",
        "as", "into", "through", "during", "before", "after", "above", "below",
        "between", "out", "off", "over", "under", "again", "further", "then",
        "once", "and", "but", "or", "nor", "not", "so", "yet", "both", "each",
        "few", "more", "most", "other", "some", "such", "no", "only", "own",
        "same", "than", "too", "very", "just", "because", "if", "when", "this",
        "that", "these", "those", "it", "its", "use", "using", "agent", "skill",
        "tool", "you", "your", "file", "files", "code",
    }

    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self._file_cache: Dict[str, str] = {}
        self._meta_cache: Dict[str, Dict[str, str]] = {}

    def _read_file(self, rel_path: str) -> str:
        """Read and cache file contents."""
        if rel_path not in self._file_cache:
            fpath = self.base_dir / rel_path
            try:
                self._file_cache[rel_path] = fpath.read_text(encoding="utf-8", errors="replace")
            except OSError:
                self._file_cache[rel_path] = ""
        return self._file_cache[rel_path]

    def _parse_frontmatter(self, content: str) -> Dict[str, str]:
        """Extract YAML frontmatter fields (name, description)."""
        meta = {}
        if content.startswith("---"):
            end = content.find("---", 3)
            if end > 0:
                fm = content[3:end]
                for line in fm.strip().splitlines():
                    if ":" in line:
                        key, _, val = line.partition(":")
                        meta[key.strip()] = val.strip().strip('"').strip("'")
        return meta

    def _get_metadata(self, rel_path: str) -> Dict[str, str]:
        """Get cached metadata for a file."""
        if rel_path not in self._meta_cache:
            content = self._read_file(rel_path)
            self._meta_cache[rel_path] = self._parse_frontmatter(content)
        return self._meta_cache[rel_path]

    def _slug_from_path(self, rel_path: str) -> str:
        """Extract slug name from file path."""
        # agents/debug-agent.md -> debug-agent
        # skills/morph-search/SKILL.md -> morph-search
        parts = Path(rel_path).parts
        if parts[-1] in ("SKILL.md", "README.md"):
            return parts[-2] if len(parts) > 1 else parts[-1]
        return Path(parts[-1]).stem

    def _extract_keywords(self, content: str) -> Set[str]:
        """Extract keywords using simple TF-based approach."""
        # Lowercase, extract words
        words = re.findall(r'[a-z]{3,}', content.lower())
        # Filter stop words
        filtered = [w for w in words if w not in self.STOP_WORDS]
        # Simple TF: top words by frequency
        freq: Dict[str, int] = {}
        for w in filtered:
            freq[w] = freq.get(w, 0) + 1
        # Return top 20 keywords
        sorted_words = sorted(freq.items(), key=lambda x: x[1], reverse=True)
        return {w for w, _ in sorted_words[:20]}

    def _extract_tool_refs(self, content: str) -> Set[str]:
        """Extract tool references from content."""
        tools = set()
        # Look for tool names in allowed-tools or common references
        for tool in ["Bash", "Read", "Write", "Edit", "Grep", "Glob", "Agent",
                      "WebFetch", "WebSearch", "NotebookEdit"]:
            if tool.lower() in content.lower():
                tools.add(tool)
        return tools

    def _name_similarity(self, slug_a: str, slug_b: str) -> float:
        """Slug name similarity using SequenceMatcher."""
        return difflib.SequenceMatcher(None, slug_a, slug_b).ratio()

    def _keyword_similarity(self, kw_a: Set[str], kw_b: Set[str]) -> float:
        """Jaccard similarity between keyword sets."""
        if not kw_a or not kw_b:
            return 0.0
        intersection = kw_a & kw_b
        union = kw_a | kw_b
        return len(intersection) / len(union)

    def _description_similarity(self, desc_a: str, desc_b: str) -> float:
        """Description similarity using SequenceMatcher."""
        if not desc_a or not desc_b:
            return 0.0
        return difflib.SequenceMatcher(None, desc_a.lower(), desc_b.lower()).ratio()

    def _structural_similarity(self, tools_a: Set[str], tools_b: Set[str],
                                headings_a: List[str], headings_b: List[str]) -> float:
        """Structural similarity based on shared tools and heading patterns."""
        tool_sim = 0.0
        if tools_a or tools_b:
            union = tools_a | tools_b
            intersection = tools_a & tools_b
            tool_sim = len(intersection) / len(union) if union else 0.0

        heading_sim = 0.0
        if headings_a and headings_b:
            heading_sim = difflib.SequenceMatcher(
                None, headings_a, headings_b
            ).ratio()

        return (tool_sim + heading_sim) / 2.0

    def _extract_headings(self, content: str) -> List[str]:
        """Extract markdown headings."""
        return [line.strip().lstrip("#").strip()
                for line in content.splitlines()
                if line.strip().startswith("#")]

    def _compute_similarity(self, path_a: str, path_b: str) -> SimilarityPair:
        """Compute composite similarity between two files."""
        content_a = self._read_file(path_a)
        content_b = self._read_file(path_b)
        meta_a = self._get_metadata(path_a)
        meta_b = self._get_metadata(path_b)

        slug_a = self._slug_from_path(path_a)
        slug_b = self._slug_from_path(path_b)

        name_sim = self._name_similarity(slug_a, slug_b)
        kw_sim = self._keyword_similarity(
            self._extract_keywords(content_a),
            self._extract_keywords(content_b),
        )
        desc_sim = self._description_similarity(
            meta_a.get("description", ""),
            meta_b.get("description", ""),
        )
        struct_sim = self._structural_similarity(
            self._extract_tool_refs(content_a),
            self._extract_tool_refs(content_b),
            self._extract_headings(content_a),
            self._extract_headings(content_b),
        )

        composite = (
            self.WEIGHT_NAME * name_sim +
            self.WEIGHT_KEYWORDS * kw_sim +
            self.WEIGHT_DESCRIPTION * desc_sim +
            self.WEIGHT_STRUCTURE * struct_sim
        )

        return SimilarityPair(
            path_a=path_a, path_b=path_b, score=composite,
            breakdown={
                "name": name_sim,
                "keywords": kw_sim,
                "description": desc_sim,
                "structure": struct_sim,
            },
        )

    def _collect_ecosystem_files(self) -> List[str]:
        """Collect all agent and skill definition files."""
        files = []
        for root_path, dirs, filenames in os.walk(self.base_dir):
            root = Path(root_path)
            rel_root = str(root.relative_to(self.base_dir))
            # Only look in agents/ and skills/
            if not (rel_root.startswith("agents") or rel_root.startswith("skills")):
                continue
            for fname in filenames:
                if fname.endswith(".md"):
                    fpath = root / fname
                    rel = str(fpath.relative_to(self.base_dir))
                    files.append(rel)
        return sorted(files)

    def find_duplicates(self, threshold: float = 0.6) -> List[SimilarityPair]:
        """Find pairs of files that exceed the similarity threshold."""
        files = self._collect_ecosystem_files()
        pairs = []
        for i, path_a in enumerate(files):
            for path_b in files[i + 1:]:
                pair = self._compute_similarity(path_a, path_b)
                if pair.score >= threshold:
                    pairs.append(pair)
        return sorted(pairs, key=lambda p: p.score, reverse=True)

    def find_related(self, target_path: str, threshold: float = 0.4) -> List[SimilarityPair]:
        """Find files related to a specific target file."""
        files = self._collect_ecosystem_files()
        matches = []
        for path in files:
            if path == target_path:
                continue
            pair = self._compute_similarity(target_path, path)
            if pair.score >= threshold:
                matches.append(pair)
        return sorted(matches, key=lambda p: p.score, reverse=True)

    def categorize(self) -> Dict[str, List[str]]:
        """Categorize files by type (agents, skills, rules, etc.)."""
        categories: Dict[str, List[str]] = {}
        files = self._collect_ecosystem_files()
        for f in files:
            parts = Path(f).parts
            cat = parts[0] if parts else "other"
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(f)
        return categories

    def stats(self) -> Dict[str, Any]:
        """Compute ecosystem statistics."""
        files = self._collect_ecosystem_files()
        categories = self.categorize()
        total_size = 0
        for f in files:
            fpath = self.base_dir / f
            try:
                total_size += fpath.stat().st_size
            except OSError:
                pass
        return {
            "total_files": len(files),
            "categories": {k: len(v) for k, v in categories.items()},
            "total_size_kb": round(total_size / 1024, 1),
        }

    def find_stale(self, manifest: Manifest, days: int = 90) -> List[str]:
        """Find files not referenced in manifest history for N days."""
        stale = []
        cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=days)
        cutoff_str = cutoff.isoformat() + "Z"

        files = self._collect_ecosystem_files()
        for f in files:
            history = manifest.file_history.get(f, [])
            if not history:
                # No history at all — check file mtime as fallback
                fpath = self.base_dir / f
                try:
                    mtime = datetime.datetime.utcfromtimestamp(fpath.stat().st_mtime)
                    if mtime < cutoff:
                        stale.append(f)
                except OSError:
                    stale.append(f)
            else:
                last_entry = history[-1]
                last_ts = last_entry.get("timestamp", "")
                if last_ts < cutoff_str:
                    stale.append(f)
        return stale


# =============================================================================
# Phase 4: CLI Application
# =============================================================================

class ClaudeSync:
    """Main CLI application."""

    def __init__(self):
        self.args = None
        self.output = None
        self.paths = None

    def run(self) -> int:
        """Parse args and dispatch to command handler."""
        parser = self._build_parser()
        self.args = parser.parse_args()

        if not hasattr(self.args, "command") or not self.args.command:
            parser.print_help()
            return ExitCode.ERROR

        # Initialize output
        self.output = Output(
            json_mode=getattr(self.args, "json", False),
            verbose=getattr(self.args, "verbose", False),
            quiet=getattr(self.args, "quiet", False),
        )

        # Initialize path resolver
        self.paths = PathResolver()

        # Dispatch to command
        handlers = {
            "init": self._cmd_init,
            "status": self._cmd_status,
            "push": self._cmd_push,
            "pull": self._cmd_pull,
            "diff": self._cmd_diff,
            "doctor": self._cmd_doctor,
            "backup": self._cmd_backup,
            "restore": self._cmd_restore,
            "resolve": self._cmd_resolve,
            "history": self._cmd_history,
            "watch": self._cmd_watch,
            "hooks": self._cmd_hooks,
            "ecosystem": self._cmd_ecosystem,
            "drift": self._cmd_drift,
            "tracker": self._cmd_tracker,
            "pair": self._cmd_pair,
            "genome": self._cmd_genome,
            "workset": self._cmd_workset,
        }
        handler = handlers.get(self.args.command)
        if handler:
            try:
                code = handler()
                self.output.flush_json()
                return code
            except KeyboardInterrupt:
                print()
                return ExitCode.ERROR
            except Exception as e:
                self.output.error(f"Unexpected error: {e}")
                if self.output.verbose:
                    import traceback
                    traceback.print_exc()
                return ExitCode.ERROR
        else:
            parser.print_help()
            return ExitCode.ERROR

    def _build_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            prog="claude-sync",
            description="Sync Claude Code configuration between machines",
        )
        parser.add_argument("--json", action="store_true", help="Output in JSON format")
        parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
        parser.add_argument("--quiet", "-q", action="store_true", help="Quiet output")

        subparsers = parser.add_subparsers(dest="command", help="Available commands")

        # init
        init_p = subparsers.add_parser("init", help="Initialize sync in current repo")
        init_p.add_argument("--force", action="store_true", help="Force re-initialization")

        # status
        subparsers.add_parser("status", help="Show sync status")

        # push
        push_p = subparsers.add_parser("push", help="Push ~/.claude -> repo/claude")
        push_p.add_argument("--dry-run", action="store_true", help="Show what would happen")
        push_p.add_argument("--yes", "-y", action="store_true", help="Skip confirmation")
        push_p.add_argument("--force", action="store_true", help="Push even if secrets or conflicts detected")
        push_p.add_argument("--ours", action="store_true", help="Resolve conflicts with local (home) version")
        push_p.add_argument("--theirs", action="store_true", help="Resolve conflicts with remote (repo) version")

        # pull
        pull_p = subparsers.add_parser("pull", help="Pull repo/claude -> ~/.claude")
        pull_p.add_argument("--dry-run", action="store_true", help="Show what would happen")
        pull_p.add_argument("--yes", "-y", action="store_true", help="Skip confirmation")
        pull_p.add_argument("--force", action="store_true", help="Pull even if conflicts detected")
        pull_p.add_argument("--ours", action="store_true", help="Resolve conflicts with local (home) version")
        pull_p.add_argument("--theirs", action="store_true", help="Resolve conflicts with remote (repo) version")

        # resolve
        resolve_p = subparsers.add_parser("resolve", help="Show and resolve sync conflicts")
        resolve_p.add_argument("--ours", action="store_true", help="Resolve all conflicts with local version")
        resolve_p.add_argument("--theirs", action="store_true", help="Resolve all conflicts with remote version")

        # history
        history_p = subparsers.add_parser("history", help="Show file sync history")
        history_p.add_argument("file", nargs="?", help="Specific file to show history for")

        # diff
        diff_p = subparsers.add_parser("diff", help="Show file differences")
        diff_p.add_argument("--direction", choices=["push", "pull"], default="push",
                           help="Direction to diff (default: push)")
        diff_p.add_argument("file", nargs="?", help="Specific file to diff")

        # doctor
        subparsers.add_parser("doctor", help="Run health checks")

        # backup
        backup_p = subparsers.add_parser("backup", help="Manage backups")
        backup_sub = backup_p.add_subparsers(dest="backup_command")
        backup_sub.add_parser("create", help="Create a backup")
        backup_sub.add_parser("list", help="List backups")
        prune_p = backup_sub.add_parser("prune", help="Remove old backups")
        prune_p.add_argument("--keep", type=int, default=DEFAULT_BACKUP_RETENTION,
                            help=f"Number of backups to keep (default: {DEFAULT_BACKUP_RETENTION})")

        # restore
        restore_p = subparsers.add_parser("restore", help="Restore from backup")
        restore_p.add_argument("name", nargs="?", help="Backup name (latest if omitted)")
        restore_p.add_argument("--dry-run", action="store_true", help="Show what would happen")
        restore_p.add_argument("--yes", "-y", action="store_true", help="Skip confirmation")

        # watch
        watch_p = subparsers.add_parser(
            "watch",
            help="Watch for changes and auto-sync (uses watchdog if installed, falls back to polling)",
        )
        watch_p.add_argument("--interval", type=int, default=DEFAULT_WATCH_INTERVAL,
                            help=f"Poll interval in seconds for fallback mode (default: {DEFAULT_WATCH_INTERVAL})")

        # hooks
        hooks_p = subparsers.add_parser("hooks", help="Install/uninstall git hooks for auto-sync")
        hooks_sub = hooks_p.add_subparsers(dest="hooks_command")
        hooks_sub.add_parser("install", help="Install post-merge and pre-push hooks")
        hooks_sub.add_parser("uninstall", help="Remove claude-sync hooks")

        # ecosystem
        eco_p = subparsers.add_parser("ecosystem", help="Ecosystem analysis and management")
        eco_sub = eco_p.add_subparsers(dest="eco_command")
        dup_p = eco_sub.add_parser("duplicates", help="Find similar/duplicate agents and skills")
        dup_p.add_argument("--threshold", type=float, default=0.6,
                          help="Similarity threshold 0.0-1.0 (default: 0.6)")
        related_p = eco_sub.add_parser("related", help="Find files related to a given file")
        related_p.add_argument("file", help="File to find related items for")
        related_p.add_argument("--threshold", type=float, default=0.4,
                              help="Similarity threshold (default: 0.4)")
        eco_sub.add_parser("catalog", help="Categorized listing of all agents and skills")
        eco_sub.add_parser("stats", help="Ecosystem statistics")
        stale_p = eco_sub.add_parser("stale", help="Find stale/unused files")
        stale_p.add_argument("--days", type=int, default=90,
                            help="Days since last sync to consider stale (default: 90)")
        timeline_p = eco_sub.add_parser("timeline", help="Evolution timeline from git history")
        timeline_p.add_argument("--since", help="Start date (YYYY-MM-DD)")
        prune_eco_p = eco_sub.add_parser("prune", help="Remove stale files")
        prune_eco_p.add_argument("--dry-run", action="store_true", help="Show what would be removed")
        prune_eco_p.add_argument("--days", type=int, default=90,
                                help="Days threshold for staleness")
        archive_p = eco_sub.add_parser("archive", help="Archive a file to repo archive")
        archive_p.add_argument("file", help="File to archive")

        # drift
        subparsers.add_parser("drift", help="Compare local state against known machine versions")

        # tracker
        tracker_parser = subparsers.add_parser("tracker", help="Manage tracker connections")
        tracker_sub = tracker_parser.add_subparsers(dest="tracker_command")

        tracker_add = tracker_sub.add_parser("add", help="Add a tracker server")
        tracker_add.add_argument("url", help="Tracker WebSocket URL (wss://...)")
        tracker_add.add_argument("--name", required=True, help="Human-readable name")

        tracker_remove = tracker_sub.add_parser("remove", help="Remove a tracker server")
        tracker_remove.add_argument("url", help="Tracker URL to remove")

        tracker_sub.add_parser("list", help="List configured trackers")

        tracker_enable = tracker_sub.add_parser("enable", help="Enable a tracker")
        tracker_enable.add_argument("url", help="Tracker URL to enable")

        tracker_disable = tracker_sub.add_parser("disable", help="Disable a tracker")
        tracker_disable.add_argument("url", help="Tracker URL to disable")

        # pair
        pair_parser = subparsers.add_parser("pair", help="Pair with a remote device")
        pair_parser.add_argument("device_id", nargs="?", help="Device ID to pair with")
        pair_parser.add_argument("--code", help="Pairing code from the other device")

        # genome
        genome_parser = subparsers.add_parser("genome", help="Skill dependency management")
        genome_sub = genome_parser.add_subparsers(dest="genome_command")

        genome_sub.add_parser("scan", help="Show all skills with dependency declarations")

        genome_sub.add_parser("health", help="Check for missing deps, broken refs, cycles")

        graph_p = genome_sub.add_parser("graph", help="Visualize dependency tree")
        graph_p.add_argument("--skill", help="Show tree for specific skill")
        graph_p.add_argument("--format", choices=["tree", "flat", "dot"], default="tree",
                             help="Output format (default: tree)")

        install_p = genome_sub.add_parser("install", help="Install skill with dependency resolution")
        install_p.add_argument("skill", help="Skill name to install")

        genome_sub.add_parser("extract-triggers",
                              help="One-time migration: split skill-rules.json into per-skill files")

        genome_sub.add_parser("assemble-triggers",
                              help="Rebuild skill-rules.json from per-skill triggers")

        package_p = genome_sub.add_parser("package", help="Export skill + deps as tar.gz")
        package_p.add_argument("skill", help="Skill name to package")
        package_p.add_argument("--output", "-o", help="Output path (default: <skill>.tar.gz)")

        # workset
        workset_parser = subparsers.add_parser("workset", help="Manage agent/skill worksets")
        workset_sub = workset_parser.add_subparsers(dest="workset_command")

        workset_sub.add_parser("init", help="Initialize vault (one-time setup)")

        ws_create = workset_sub.add_parser("create", help="Create a new workset")
        ws_create.add_argument("name", help="Workset name (e.g., dev, design, writing)")
        ws_create.add_argument("--tags", help="Comma-separated tags to include (e.g., Dev,Design)")
        ws_create.add_argument("--agents", help="Comma-separated agent names to include")
        ws_create.add_argument("--skills", help="Comma-separated skill names to include")
        ws_create.add_argument("--exclude-agents", help="Comma-separated agents to exclude")
        ws_create.add_argument("--exclude-skills", help="Comma-separated skills to exclude")
        ws_create.add_argument("--extends", help="Comma-separated parent workset names")
        ws_create.add_argument("--description", "-d", help="Workset description")

        ws_show = workset_sub.add_parser("show", help="Show resolved workset contents")
        ws_show.add_argument("name", help="Workset name")
        ws_show.add_argument("--resolved", action="store_true", help="Show fully resolved agent/skill list")

        workset_sub.add_parser("list", help="List all defined worksets")

        ws_activate = workset_sub.add_parser("activate", help="Activate a workset")
        ws_activate.add_argument("name", help="Workset name to activate")

        workset_sub.add_parser("deactivate", help="Restore full agent/skill set")

        ws_delete = workset_sub.add_parser("delete", help="Delete a workset definition")
        ws_delete.add_argument("name", help="Workset name to delete")

        workset_sub.add_parser("status", help="Show current workset activation state")

        ws_suggest = workset_sub.add_parser("suggest", help="Suggest workset based on project context")
        ws_suggest.add_argument("--auto", action="store_true",
                                help="Auto-activate if confidence > 80%%")

        return parser

    def _require_init(self) -> bool:
        """Check that sync is initialized. Returns True if ok."""
        if not self.paths.repo_root:
            self.output.error("Not in a git repository.")
            self.output.info("  Navigate to a git repository and try again.")
            return False
        if not self.paths.repo_claude or not self.paths.repo_claude.exists():
            self.output.error("Sync not initialized.")
            self.output.info("  Run 'claude-sync init' first.")
            return False
        return True

    # ---- Commands ----

    def _cmd_init(self) -> int:
        """Initialize sync in current repo."""
        if not self.paths.repo_root:
            self.output.error("Not in a git repository.")
            self.output.info("  Navigate to a git repository or run 'git init' first.")
            return ExitCode.ERROR

        if not self.paths.home_claude.exists():
            self.output.error("~/.claude not found.")
            self.output.info("  Run Claude Code at least once to create the config directory.")
            return ExitCode.ERROR

        self.output.header("Initializing claude-sync")

        # Check if already initialized
        if self.paths.repo_claude.exists() and not getattr(self.args, "force", False):
            self.output.warning("Already initialized. Use --force to reinitialize.")
            if self.output.json_mode:
                self.output.set_json("status", "already_initialized")
                self.output.set_json("repo_root", str(self.paths.repo_root))
            return ExitCode.OK

        # Create repo/claude directory
        self.paths.repo_claude.mkdir(parents=True, exist_ok=True)
        self.output.success(f"Created {self.paths.repo_claude}")

        # Create backup before init
        backup_mgr = BackupManager()
        backup_path = backup_mgr.create_backup(self.paths.home_claude, "pre-init")
        self.output.success(f"Backed up ~/.claude to {backup_path.name}")

        # Create initial manifest
        manifest = Manifest()
        manifest.save(self.paths.manifest_path)
        self.output.success(f"Created {MANIFEST_FILENAME}")

        # Create .gitignore if it doesn't exist
        gitignore_path = self.paths.repo_root / ".gitignore"
        if not gitignore_path.exists():
            gitignore_content = (
                "# Python\n"
                "__pycache__/\n"
                "*.py[cod]\n"
                "*$py.class\n"
                "*.so\n"
                "\n"
                "# Node\n"
                "node_modules/\n"
                "\n"
                "# OS\n"
                ".DS_Store\n"
                "Thumbs.db\n"
                "\n"
                "# IDE\n"
                ".vscode/\n"
                ".idea/\n"
                "*.swp\n"
                "*.swo\n"
                "*~\n"
                "\n"
                "# claude-sync\n"
                "claude/cache/\n"
                "claude/state/\n"
                "claude/telemetry/\n"
            )
            with open(gitignore_path, "w") as f:
                f.write(gitignore_content)
            self.output.success("Created .gitignore")

        self.output.info("")
        self.output.info("Sync initialized. Next steps:")
        self.output.info("  claude-sync push    Push config to repo")
        self.output.info("  claude-sync status  Check sync status")

        if self.output.json_mode:
            self.output.set_json("status", "initialized")
            self.output.set_json("repo_root", str(self.paths.repo_root))
            self.output.set_json("repo_claude", str(self.paths.repo_claude))

        return ExitCode.OK

    def _cmd_status(self) -> int:
        """Show sync status."""
        self.output.header("claude-sync status")

        # Check basics
        if not self.paths.repo_root:
            self.output.error("Not in a git repository")
            if self.output.json_mode:
                self.output.set_json("initialized", False)
                self.output.set_json("error", "not_in_git_repo")
            return ExitCode.NOT_INITIALIZED

        initialized = (self.paths.repo_claude and self.paths.repo_claude.exists()
                       and self.paths.manifest_path.exists())

        if self.output.json_mode:
            self.output.set_json("initialized", initialized)
            self.output.set_json("repo_root", str(self.paths.repo_root))
            self.output.set_json("home_claude", str(self.paths.home_claude))

        if not initialized:
            self.output.warning("Not initialized. Run 'claude-sync init' first.")
            if self.output.json_mode:
                self.output.set_json("error", "not_initialized")
            return ExitCode.NOT_INITIALIZED

        self.output.info(f"Repo root:    {self.paths.repo_root}")
        self.output.info(f"Home claude:  {self.paths.home_claude}")
        self.output.info(f"Repo claude:  {self.paths.repo_claude}")

        # Load manifest
        manifest = Manifest.load(self.paths.manifest_path)
        if manifest.last_push:
            lp = manifest.last_push
            self.output.info(f"Last push:    {lp.get('timestamp', 'unknown')} "
                            f"from {lp.get('hostname', 'unknown')} "
                            f"({lp.get('platform', 'unknown')})")

        # Compute diffs both directions
        home_hashes = FileHasher.walk_directory(self.paths.home_claude)
        repo_hashes = FileHasher.walk_directory(self.paths.repo_claude)

        push_diff = DiffEngine.compare(home_hashes, repo_hashes, "push")
        pull_diff = DiffEngine.compare(home_hashes, repo_hashes, "pull")

        self.output.header("Push status (home -> repo)")
        self.output.print_changes(push_diff, "push")

        self.output.header("Pull status (repo -> home)")
        self.output.print_changes(pull_diff, "pull")

        if self.output.json_mode:
            self.output.set_json("push_changes", push_diff.to_dict())
            self.output.set_json("pull_changes", pull_diff.to_dict())
            self.output.set_json("home_file_count", len(home_hashes))
            self.output.set_json("repo_file_count", len(repo_hashes))
            self.output.set_json("manifest", {
                "file_count": len(manifest.files),
                "last_push": manifest.last_push,
            })

        if push_diff.has_changes or pull_diff.has_changes:
            return ExitCode.DIRTY
        return ExitCode.OK

    def _cmd_push(self) -> int:
        """Push ~/.claude -> repo/claude with three-way conflict detection."""
        if not self._require_init():
            return ExitCode.NOT_INITIALIZED

        # Bracket: deactivate workset so push sees full agent/skill set
        ws_engine = WorksetEngine(self.paths.home_claude)
        ws_state = ws_engine.load_state()
        ws_was_active = ws_state.active_workset if ws_state.vault_initialized else None
        if ws_was_active:
            ws_engine.deactivate()
            self.output.info("  (Temporarily deactivated workset for full sync)")

        try:
            result = self._do_push()
        finally:
            if ws_was_active:
                try:
                    ws_engine.activate(ws_was_active)
                    self.output.info(f"  (Re-activated workset '{ws_was_active}')")
                except Exception:
                    pass
        return result

    def _do_push(self) -> int:
        """Internal push logic."""
        dry_run = getattr(self.args, "dry_run", False)
        force = getattr(self.args, "force", False)
        yes = getattr(self.args, "yes", False)
        ours = getattr(self.args, "ours", False)
        theirs = getattr(self.args, "theirs", False)

        self.output.header("Push: ~/.claude -> repo/claude")

        # Scan for secrets
        self.output.info("Scanning for secrets...")
        findings = SecretScanner.scan_directory(self.paths.home_claude)
        if findings:
            self.output.warning(f"Found {len(findings)} potential secret(s):")
            for finding in findings:
                self.output.warning(
                    f"  {finding.file_path}:{finding.line_number} "
                    f"[{finding.pattern_name}] {finding.matched_text}"
                )
            if self.output.json_mode:
                self.output.set_json("secrets", [f.to_dict() for f in findings])
            if not force:
                self.output.error("Push blocked. Use --force to push anyway.")
                return ExitCode.SECRETS

        # Load manifest as merge base for three-way diff
        manifest = Manifest.load(self.paths.manifest_path)
        base_hashes = manifest.files if manifest.files else None

        # Compute diff with three-way merge
        home_hashes = FileHasher.walk_directory(self.paths.home_claude)
        repo_hashes = FileHasher.walk_directory(self.paths.repo_claude)
        diff_result = DiffEngine.compare(
            home_hashes, repo_hashes, "push", base_hashes=base_hashes
        )

        # Filter derived artifacts (skill-rules.json) when atomized triggers exist
        self._filter_derived_artifacts(diff_result)

        # Handle conflicts
        if diff_result.has_conflicts:
            self.output.warning(f"\n  {len(diff_result.conflicted)} conflict(s) detected "
                                "(both sides changed since last sync):")
            for c in diff_result.conflicted:
                self.output.warning(f"    CONFLICT {c.path}")

            if ours:
                # Resolve all conflicts with local (home) version
                self.output.info("  Resolving conflicts with --ours (keeping home version)")
                for c in diff_result.conflicted:
                    diff_result.modified.append(FileChange(
                        path=c.path, change_type="modified",
                        home_hash=c.home_hash, repo_hash=c.repo_hash,
                        base_hash=c.base_hash,
                    ))
                diff_result.conflicted.clear()
            elif theirs:
                # Resolve all conflicts with remote (repo) version — skip them on push
                self.output.info("  Resolving conflicts with --theirs (keeping repo version)")
                diff_result.conflicted.clear()
            elif not force:
                self.output.error(
                    "Push blocked due to conflicts. Options:\n"
                    "    --ours    Keep home (local) version for all conflicts\n"
                    "    --theirs  Keep repo (remote) version for all conflicts\n"
                    "    --force   Push anyway (overwrites repo)\n"
                    "    resolve   Use 'claude-sync resolve' for per-file resolution"
                )
                if self.output.json_mode:
                    self.output.set_json("status", "conflicts")
                    self.output.set_json("conflicts", [c.to_dict() for c in diff_result.conflicted])
                return ExitCode.DIRTY
            else:
                # Force: treat conflicts as modifications (home wins)
                for c in diff_result.conflicted:
                    diff_result.modified.append(FileChange(
                        path=c.path, change_type="modified",
                        home_hash=c.home_hash, repo_hash=c.repo_hash,
                        base_hash=c.base_hash,
                    ))
                diff_result.conflicted.clear()

        if not diff_result.has_changes:
            self.output.success("Already up to date. Nothing to push.")
            if self.output.json_mode:
                self.output.set_json("status", "up_to_date")
            return ExitCode.OK

        # Show preview
        self.output.print_changes(diff_result, "push")

        if dry_run:
            self.output.info("\n  (dry run - no changes made)")
            if self.output.json_mode:
                self.output.set_json("status", "dry_run")
                self.output.set_json("changes", diff_result.to_dict())
            return ExitCode.OK

        # Confirm
        if not yes and not self.output.confirm("Proceed with push?"):
            self.output.info("Push cancelled.")
            return ExitCode.OK

        # Backup before push
        backup_mgr = BackupManager()
        backup_mgr.create_backup(self.paths.repo_claude, "pre-push")
        self.output.detail("Created pre-push backup")

        # Perform push
        engine = SyncEngine(self.paths, self.output)
        count = engine.push(diff_result)

        # Handle settings.json separately
        home_settings_path = self.paths.home_claude / "settings.json"
        if home_settings_path.exists():
            with open(home_settings_path) as f:
                home_settings = json.load(f)
            portable = SettingsMerger.merge_for_push(home_settings)
            if portable:
                repo_settings_path = self.paths.repo_claude / "settings.json"
                repo_settings_path.parent.mkdir(parents=True, exist_ok=True)
                with open(repo_settings_path, "w") as f:
                    json.dump(portable, f, indent=2, sort_keys=True)
                    f.write("\n")
                self.output.detail("Pushed portable settings")

        # Update manifest with new file state and history
        new_repo_hashes = FileHasher.walk_directory(self.paths.repo_claude)
        changed_paths = [c.path for c in diff_result.added + diff_result.modified]
        manifest.files = new_repo_hashes
        manifest.update_provenance("push")
        manifest.record_file_history(changed_paths, "push", new_repo_hashes)
        manifest.save(self.paths.manifest_path)

        # Reassemble derived artifacts after push
        self._maybe_assemble_triggers(target="repo")

        self.output.success(f"Pushed {count} file(s)")

        if self.output.json_mode:
            self.output.set_json("status", "pushed")
            self.output.set_json("files_synced", count)
            self.output.set_json("changes", diff_result.to_dict())

        return ExitCode.OK

    def _cmd_pull(self) -> int:
        """Pull repo/claude -> ~/.claude with three-way conflict detection."""
        if not self._require_init():
            return ExitCode.NOT_INITIALIZED

        # Bracket: deactivate workset so pull sees full agent/skill set
        ws_engine = WorksetEngine(self.paths.home_claude)
        ws_state = ws_engine.load_state()
        ws_was_active = ws_state.active_workset if ws_state.vault_initialized else None
        if ws_was_active:
            ws_engine.deactivate()
            self.output.info("  (Temporarily deactivated workset for full sync)")

        try:
            result = self._do_pull()
        finally:
            if ws_was_active:
                # Re-sync vault from newly pulled files, then re-activate
                try:
                    ws_engine._reconcile_vault()
                    ws_engine.activate(ws_was_active)
                    self.output.info(f"  (Re-activated workset '{ws_was_active}')")
                except Exception:
                    pass
        return result

    def _do_pull(self) -> int:
        """Internal pull logic."""
        dry_run = getattr(self.args, "dry_run", False)
        yes = getattr(self.args, "yes", False)
        ours = getattr(self.args, "ours", False)
        theirs = getattr(self.args, "theirs", False)
        force = getattr(self.args, "force", False)

        self.output.header("Pull: repo/claude -> ~/.claude")

        # Load manifest as merge base
        manifest = Manifest.load(self.paths.manifest_path)
        base_hashes = manifest.files if manifest.files else None

        # Compute diff with three-way merge
        home_hashes = FileHasher.walk_directory(self.paths.home_claude)
        repo_hashes = FileHasher.walk_directory(self.paths.repo_claude)
        diff_result = DiffEngine.compare(
            home_hashes, repo_hashes, "pull", base_hashes=base_hashes
        )

        # Filter derived artifacts (skill-rules.json) when atomized triggers exist
        self._filter_derived_artifacts(diff_result)

        # Handle conflicts
        if diff_result.has_conflicts:
            self.output.warning(f"\n  {len(diff_result.conflicted)} conflict(s) detected "
                                "(both sides changed since last sync):")
            for c in diff_result.conflicted:
                self.output.warning(f"    CONFLICT {c.path}")

            if theirs:
                # Resolve with remote (repo) version — apply them
                self.output.info("  Resolving conflicts with --theirs (keeping repo version)")
                for c in diff_result.conflicted:
                    diff_result.modified.append(FileChange(
                        path=c.path, change_type="modified",
                        home_hash=c.home_hash, repo_hash=c.repo_hash,
                        base_hash=c.base_hash,
                    ))
                diff_result.conflicted.clear()
            elif ours:
                # Resolve with local (home) version — skip them on pull
                self.output.info("  Resolving conflicts with --ours (keeping home version)")
                diff_result.conflicted.clear()
            elif not force:
                self.output.error(
                    "Pull blocked due to conflicts. Options:\n"
                    "    --ours    Keep home (local) version for all conflicts\n"
                    "    --theirs  Keep repo (remote) version for all conflicts\n"
                    "    --force   Pull anyway (overwrites home)\n"
                    "    resolve   Use 'claude-sync resolve' for per-file resolution"
                )
                if self.output.json_mode:
                    self.output.set_json("status", "conflicts")
                    self.output.set_json("conflicts", [c.to_dict() for c in diff_result.conflicted])
                return ExitCode.DIRTY
            else:
                # Force: treat conflicts as modifications (repo wins)
                for c in diff_result.conflicted:
                    diff_result.modified.append(FileChange(
                        path=c.path, change_type="modified",
                        home_hash=c.home_hash, repo_hash=c.repo_hash,
                        base_hash=c.base_hash,
                    ))
                diff_result.conflicted.clear()

        if not diff_result.has_changes:
            self.output.success("Already up to date. Nothing to pull.")
            if self.output.json_mode:
                self.output.set_json("status", "up_to_date")
            return ExitCode.OK

        # Show preview
        self.output.print_changes(diff_result, "pull")

        if dry_run:
            self.output.info("\n  (dry run - no changes made)")
            if self.output.json_mode:
                self.output.set_json("status", "dry_run")
                self.output.set_json("changes", diff_result.to_dict())
            return ExitCode.OK

        # Confirm
        if not yes and not self.output.confirm("Proceed with pull?"):
            self.output.info("Pull cancelled.")
            return ExitCode.OK

        # Backup before pull
        backup_mgr = BackupManager()
        backup_path = backup_mgr.create_backup(self.paths.home_claude, "pre-pull")
        self.output.detail(f"Created pre-pull backup: {backup_path.name}")

        # Perform pull
        engine = SyncEngine(self.paths, self.output)
        count = engine.pull(diff_result)

        # Handle settings.json merge
        repo_settings_path = self.paths.repo_claude / "settings.json"
        if repo_settings_path.exists():
            with open(repo_settings_path) as f:
                repo_settings = json.load(f)
            home_settings_path = self.paths.home_claude / "settings.json"
            local_settings = {}
            if home_settings_path.exists():
                with open(home_settings_path) as f:
                    local_settings = json.load(f)
            merged = SettingsMerger.merge_for_pull(local_settings, repo_settings)
            with open(home_settings_path, "w") as f:
                json.dump(merged, f, indent=2, sort_keys=True)
                f.write("\n")
            self.output.detail("Merged portable settings into local")

        # Update manifest with pull provenance and history
        changed_paths = [c.path for c in diff_result.added + diff_result.modified]
        new_home_hashes = FileHasher.walk_directory(self.paths.home_claude)
        manifest.files = FileHasher.walk_directory(self.paths.repo_claude)
        manifest.update_provenance("pull")
        manifest.record_file_history(changed_paths, "pull", new_home_hashes)
        manifest.save(self.paths.manifest_path)

        # Reassemble derived artifacts after pull
        self._maybe_assemble_triggers(target="home")

        self.output.success(f"Pulled {count} file(s)")

        # Prune old backups
        pruned = backup_mgr.prune()
        if pruned:
            self.output.detail(f"Pruned {pruned} old backup(s)")

        if self.output.json_mode:
            self.output.set_json("status", "pulled")
            self.output.set_json("files_synced", count)
            self.output.set_json("changes", diff_result.to_dict())

        return ExitCode.OK

    def _cmd_diff(self) -> int:
        """Show unified text diff between home and repo."""
        if not self._require_init():
            return ExitCode.NOT_INITIALIZED

        direction = getattr(self.args, "direction", "push")
        specific_file = getattr(self.args, "file", None)

        self.output.header(f"Diff ({direction}: {'home -> repo' if direction == 'push' else 'repo -> home'})")

        home_hashes = FileHasher.walk_directory(self.paths.home_claude)
        repo_hashes = FileHasher.walk_directory(self.paths.repo_claude)
        diff_result = DiffEngine.compare(home_hashes, repo_hashes, direction)

        if not diff_result.has_changes:
            self.output.success("No differences found.")
            if self.output.json_mode:
                self.output.set_json("status", "clean")
                self.output.set_json("diffs", [])
            return ExitCode.OK

        diffs_output = []

        for change in diff_result.all_changes():
            if specific_file and change.path != specific_file:
                continue

            home_path = self.paths.relative_to_home(change.path)
            repo_path = self.paths.relative_to_repo(change.path)

            if change.change_type == "added":
                if direction == "push":
                    source_path = home_path
                else:
                    source_path = repo_path
                if source_path.exists():
                    try:
                        content = source_path.read_text(encoding="utf-8", errors="replace")
                        lines = content.splitlines(keepends=True)
                        diff_lines = list(difflib.unified_diff(
                            [], lines,
                            fromfile="/dev/null",
                            tofile=change.path,
                        ))
                        for line in diff_lines:
                            self.output.diff_line(line.rstrip("\n"))
                        if self.output.json_mode:
                            diffs_output.append({
                                "path": change.path,
                                "type": "added",
                                "diff": "".join(diff_lines),
                            })
                    except UnicodeDecodeError:
                        self.output.info(f"  [binary file: {change.path}]")

            elif change.change_type == "deleted":
                if direction == "push":
                    target_path = repo_path
                else:
                    target_path = home_path
                if target_path.exists():
                    try:
                        content = target_path.read_text(encoding="utf-8", errors="replace")
                        lines = content.splitlines(keepends=True)
                        diff_lines = list(difflib.unified_diff(
                            lines, [],
                            fromfile=change.path,
                            tofile="/dev/null",
                        ))
                        for line in diff_lines:
                            self.output.diff_line(line.rstrip("\n"))
                        if self.output.json_mode:
                            diffs_output.append({
                                "path": change.path,
                                "type": "deleted",
                                "diff": "".join(diff_lines),
                            })
                    except UnicodeDecodeError:
                        self.output.info(f"  [binary file: {change.path}]")

            elif change.change_type == "modified":
                try:
                    home_content = home_path.read_text(encoding="utf-8", errors="replace") if home_path.exists() else ""
                    repo_content = repo_path.read_text(encoding="utf-8", errors="replace") if repo_path.exists() else ""
                    home_lines = home_content.splitlines(keepends=True)
                    repo_lines = repo_content.splitlines(keepends=True)

                    if direction == "push":
                        diff_lines = list(difflib.unified_diff(
                            repo_lines, home_lines,
                            fromfile=f"repo/{change.path}",
                            tofile=f"home/{change.path}",
                        ))
                    else:
                        diff_lines = list(difflib.unified_diff(
                            home_lines, repo_lines,
                            fromfile=f"home/{change.path}",
                            tofile=f"repo/{change.path}",
                        ))

                    for line in diff_lines:
                        self.output.diff_line(line.rstrip("\n"))
                    if self.output.json_mode:
                        diffs_output.append({
                            "path": change.path,
                            "type": "modified",
                            "diff": "".join(diff_lines),
                        })
                except UnicodeDecodeError:
                    self.output.info(f"  [binary file: {change.path}]")

            if not self.output.json_mode:
                print()

        if self.output.json_mode:
            self.output.set_json("status", "dirty")
            self.output.set_json("direction", direction)
            self.output.set_json("diffs", diffs_output)
            self.output.set_json("summary", diff_result.to_dict())

        return ExitCode.DIRTY

    def _cmd_doctor(self) -> int:
        """Run health checks."""
        self.output.header("claude-sync doctor")

        doctor = Doctor(self.paths)
        checks = doctor.run_all()

        passed = 0
        failed = 0
        for check in checks:
            if check.passed:
                self.output.success(f"{check.name}: {check.message}")
                passed += 1
            else:
                self.output.error(f"{check.name}: {check.message}")
                if check.remediation:
                    self.output.info(f"    Fix: {check.remediation}")
                failed += 1

        self.output.info("")
        self.output.info(f"  {passed} passed, {failed} failed")

        if self.output.json_mode:
            self.output.set_json("checks", [c.to_dict() for c in checks])
            self.output.set_json("passed", passed)
            self.output.set_json("failed", failed)

        return ExitCode.OK if failed == 0 else ExitCode.ERROR

    def _cmd_backup(self) -> int:
        """Manage backups."""
        backup_cmd = getattr(self.args, "backup_command", None)
        backup_mgr = BackupManager()

        if backup_cmd == "create" or not backup_cmd:
            if not backup_cmd:
                return self._backup_list(backup_mgr)
            return self._backup_create(backup_mgr)
        elif backup_cmd == "list":
            return self._backup_list(backup_mgr)
        elif backup_cmd == "prune":
            return self._backup_prune(backup_mgr)
        else:
            return self._backup_list(backup_mgr)

    def _backup_create(self, mgr: BackupManager) -> int:
        self.output.header("Creating backup")
        backup_path = mgr.create_backup(self.paths.home_claude, "manual")
        self.output.success(f"Backup created: {backup_path.name}")
        if self.output.json_mode:
            self.output.set_json("status", "created")
            self.output.set_json("backup_path", str(backup_path))
        return ExitCode.OK

    def _backup_list(self, mgr: BackupManager) -> int:
        self.output.header("Available backups")
        backups = mgr.list_backups()
        if not backups:
            self.output.info("  No backups found.")
            if self.output.json_mode:
                self.output.set_json("backups", [])
            return ExitCode.OK
        for b in backups:
            label = f" ({b.get('label', '')})" if b.get("label") else ""
            count = b.get("file_count", "?")
            self.output.info(f"  {b['name']}{label} - {count} files")
        if self.output.json_mode:
            self.output.set_json("backups", backups)
        return ExitCode.OK

    def _backup_prune(self, mgr: BackupManager) -> int:
        keep = getattr(self.args, "keep", DEFAULT_BACKUP_RETENTION)
        self.output.header(f"Pruning backups (keeping {keep})")
        pruned = mgr.prune(keep)
        if pruned:
            self.output.success(f"Pruned {pruned} backup(s)")
        else:
            self.output.info("  Nothing to prune.")
        if self.output.json_mode:
            self.output.set_json("pruned", pruned)
            self.output.set_json("keep", keep)
        return ExitCode.OK

    def _cmd_restore(self) -> int:
        """Restore from backup."""
        backup_mgr = BackupManager()
        dry_run = getattr(self.args, "dry_run", False)
        yes = getattr(self.args, "yes", False)
        name = getattr(self.args, "name", None)

        self.output.header("Restore from backup")

        # Find backup
        if name:
            backup_path = backup_mgr.get_backup(name)
            if not backup_path:
                self.output.error(f"Backup not found: {name}")
                self.output.info("  Run 'claude-sync backup list' to see available backups.")
                return ExitCode.ERROR
        else:
            backups = backup_mgr.list_backups()
            if not backups:
                self.output.error("No backups available.")
                return ExitCode.ERROR
            backup_path = Path(backups[0]["path"])
            self.output.info(f"Using latest backup: {backup_path.name}")

        # Show what would be restored
        backup_hashes = FileHasher.walk_directory(backup_path)
        home_hashes = FileHasher.walk_directory(self.paths.home_claude)

        diff_result = DiffEngine.compare(backup_hashes, home_hashes, "push")

        if not diff_result.has_changes:
            self.output.success("Backup matches current state. Nothing to restore.")
            return ExitCode.OK

        self.output.print_changes(diff_result, "pull")

        if dry_run:
            self.output.info("\n  (dry run - no changes made)")
            if self.output.json_mode:
                self.output.set_json("status", "dry_run")
                self.output.set_json("changes", diff_result.to_dict())
            return ExitCode.OK

        # Confirm
        if not yes and not self.output.confirm("Proceed with restore?"):
            self.output.info("Restore cancelled.")
            return ExitCode.OK

        # Safety: backup current state before restoring
        pre_restore = backup_mgr.create_backup(self.paths.home_claude, "pre-restore")
        self.output.detail(f"Created safety backup: {pre_restore.name}")

        # Restore files
        count = 0
        for change in diff_result.added + diff_result.modified:
            src = backup_path / change.path
            dst = self.paths.home_claude / change.path
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(dst))
            if change.path.endswith(".sh") or change.path.endswith(".py"):
                SyncEngine._set_executable(dst)
            count += 1

        for change in diff_result.deleted:
            dst = self.paths.home_claude / change.path
            if dst.exists():
                dst.unlink()
                count += 1

        self.output.success(f"Restored {count} file(s) from {backup_path.name}")

        if self.output.json_mode:
            self.output.set_json("status", "restored")
            self.output.set_json("backup_used", str(backup_path))
            self.output.set_json("files_restored", count)
            self.output.set_json("safety_backup", str(pre_restore))

        return ExitCode.OK


    def _cmd_watch(self) -> int:
        """Watch for changes and auto-sync."""
        if not self._require_init():
            return ExitCode.NOT_INITIALIZED

        interval = getattr(self.args, "interval", DEFAULT_WATCH_INTERVAL)
        watcher = FileWatcher(self.paths, self.output, interval=interval)
        return watcher.watch()

    def _cmd_hooks(self) -> int:
        """Install/uninstall git hooks."""
        if not self.paths.repo_root:
            self.output.error("Not in a git repository.")
            return ExitCode.ERROR

        hooks_cmd = getattr(self.args, "hooks_command", None)
        mgr = GitHookManager(self.paths.repo_root, self.output)

        if hooks_cmd == "install":
            self.output.header("Installing git hooks")
            return mgr.install()
        elif hooks_cmd == "uninstall":
            self.output.header("Removing git hooks")
            return mgr.uninstall()
        else:
            self.output.header("Git hooks")
            self.output.info("  claude-sync hooks install    Install auto-sync hooks")
            self.output.info("  claude-sync hooks uninstall  Remove auto-sync hooks")
            return ExitCode.OK

    def _cmd_ecosystem(self) -> int:
        """Ecosystem analysis and management."""
        if not self._require_init():
            return ExitCode.NOT_INITIALIZED

        eco_cmd = getattr(self.args, "eco_command", None)

        if eco_cmd == "duplicates":
            return self._eco_duplicates()
        elif eco_cmd == "related":
            return self._eco_related()
        elif eco_cmd == "catalog":
            return self._eco_catalog()
        elif eco_cmd == "stats":
            return self._eco_stats()
        elif eco_cmd == "stale":
            return self._eco_stale()
        elif eco_cmd == "timeline":
            return self._eco_timeline()
        elif eco_cmd == "prune":
            return self._eco_prune()
        elif eco_cmd == "archive":
            return self._eco_archive()
        else:
            self.output.header("Ecosystem commands")
            self.output.info("  ecosystem duplicates  Find similar agents/skills")
            self.output.info("  ecosystem related     Find related files")
            self.output.info("  ecosystem catalog     List all agents/skills by category")
            self.output.info("  ecosystem stats       Show ecosystem statistics")
            self.output.info("  ecosystem stale       Find stale/unused files")
            self.output.info("  ecosystem timeline    Show evolution from git history")
            self.output.info("  ecosystem prune       Remove stale files")
            self.output.info("  ecosystem archive     Archive a file")
            return ExitCode.OK

    def _eco_duplicates(self) -> int:
        threshold = getattr(self.args, "threshold", 0.6)
        analyzer = EcosystemAnalyzer(self.paths.repo_claude)
        self.output.header(f"Duplicate detection (threshold: {threshold})")
        pairs = analyzer.find_duplicates(threshold)
        if not pairs:
            self.output.success("No duplicates found above threshold.")
        else:
            for pair in pairs:
                self.output.info(
                    f"  {self.output._color(f'{pair.score:.0%}', self.output.YELLOW)} "
                    f"{pair.path_a} <-> {pair.path_b}"
                )
                if self.output.verbose:
                    for signal, val in pair.breakdown.items():
                        self.output.detail(f"{signal}: {val:.0%}")
        if self.output.json_mode:
            self.output.set_json("duplicates", [p.to_dict() for p in pairs])
        return ExitCode.OK

    def _eco_related(self) -> int:
        target = getattr(self.args, "file", "")
        threshold = getattr(self.args, "threshold", 0.4)
        analyzer = EcosystemAnalyzer(self.paths.repo_claude)
        self.output.header(f"Related to: {target}")
        matches = analyzer.find_related(target, threshold)
        if not matches:
            self.output.info("  No related files found.")
        else:
            for pair in matches:
                other = pair.path_b if pair.path_a == target else pair.path_a
                self.output.info(
                    f"  {self.output._color(f'{pair.score:.0%}', self.output.YELLOW)} {other}"
                )
        if self.output.json_mode:
            self.output.set_json("related", [p.to_dict() for p in matches])
        return ExitCode.OK

    def _eco_catalog(self) -> int:
        analyzer = EcosystemAnalyzer(self.paths.repo_claude)
        self.output.header("Ecosystem catalog")
        categories = analyzer.categorize()
        for cat, files in sorted(categories.items()):
            self.output.info(f"\n  {self.output._color(cat, self.output.BOLD)} ({len(files)})")
            for f in files:
                meta = analyzer._get_metadata(f)
                desc = meta.get("description", "")
                name = meta.get("name", analyzer._slug_from_path(f))
                if desc:
                    self.output.info(f"    {name}: {desc[:60]}")
                else:
                    self.output.info(f"    {name}")
        if self.output.json_mode:
            self.output.set_json("catalog", categories)
        return ExitCode.OK

    def _eco_stats(self) -> int:
        analyzer = EcosystemAnalyzer(self.paths.repo_claude)
        self.output.header("Ecosystem statistics")
        s = analyzer.stats()
        self.output.info(f"  Total files: {s['total_files']}")
        self.output.info(f"  Total size:  {s['total_size_kb']} KB")
        for cat, count in sorted(s["categories"].items()):
            self.output.info(f"    {cat}: {count}")
        if self.output.json_mode:
            self.output.set_json("stats", s)
        return ExitCode.OK

    def _eco_stale(self) -> int:
        days = getattr(self.args, "days", 90)
        manifest = Manifest.load(self.paths.manifest_path)
        analyzer = EcosystemAnalyzer(self.paths.repo_claude)
        self.output.header(f"Stale files (>{days} days since last sync)")
        stale = analyzer.find_stale(manifest, days)
        if not stale:
            self.output.success("No stale files found.")
        else:
            for f in stale:
                self.output.info(f"  {self.output._color('STALE', self.output.DIM)} {f}")
            self.output.info(f"\n  {len(stale)} stale file(s)")
        if self.output.json_mode:
            self.output.set_json("stale", stale)
        return ExitCode.OK

    def _eco_timeline(self) -> int:
        """Show ecosystem evolution from git history."""
        import subprocess

        since = getattr(self.args, "since", None)
        self.output.header("Ecosystem timeline")

        cmd = ["git", "log", "--diff-filter=AMD", "--name-status",
               "--pretty=format:%H|%aI", "--", "claude/"]
        if since:
            cmd.insert(2, f"--since={since}")

        try:
            result = subprocess.run(
                cmd, cwd=str(self.paths.repo_root),
                capture_output=True, text=True, timeout=30,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            self.output.error("Could not run git log.")
            return ExitCode.ERROR

        if result.returncode != 0:
            self.output.error("git log failed.")
            return ExitCode.ERROR

        # Parse git log output into monthly buckets
        monthly: Dict[str, Dict[str, int]] = {}  # month -> {A/M/D -> count}
        monthly_by_cat: Dict[str, Dict[str, int]] = {}  # month -> {category -> count}

        for line in result.stdout.splitlines():
            if "|" in line and not line.startswith("\t"):
                # Commit header: hash|date
                _, _, date_str = line.partition("|")
                current_month = date_str[:7]  # YYYY-MM
            elif line and line[0] in "AMD":
                parts = line.split("\t", 1)
                if len(parts) == 2:
                    action, filepath = parts
                    if current_month not in monthly:
                        monthly[current_month] = {"A": 0, "M": 0, "D": 0}
                        monthly_by_cat[current_month] = {}
                    monthly[current_month][action] = monthly[current_month].get(action, 0) + 1
                    # Categorize
                    cat = filepath.split("/")[1] if "/" in filepath else "other"
                    monthly_by_cat[current_month][cat] = monthly_by_cat[current_month].get(cat, 0) + 1

        if not monthly:
            self.output.info("  No changes found in git history for claude/")
            return ExitCode.OK

        for month in sorted(monthly.keys(), reverse=True):
            counts = monthly[month]
            cats = monthly_by_cat.get(month, {})
            parts = []
            if counts.get("A", 0):
                parts.append(f"+{counts['A']} added")
            if counts.get("M", 0):
                parts.append(f"~{counts['M']} modified")
            if counts.get("D", 0):
                parts.append(f"-{counts['D']} deleted")
            cat_str = ", ".join(f"{k}: {v}" for k, v in sorted(cats.items()))
            self.output.info(f"  {month}: {', '.join(parts)}")
            if self.output.verbose and cat_str:
                self.output.detail(cat_str)

        if self.output.json_mode:
            self.output.set_json("timeline", monthly)
            self.output.set_json("timeline_by_category", monthly_by_cat)
        return ExitCode.OK

    def _eco_prune(self) -> int:
        """Remove stale files (dry-run by default)."""
        dry_run = getattr(self.args, "dry_run", False)
        days = getattr(self.args, "days", 90)
        manifest = Manifest.load(self.paths.manifest_path)
        analyzer = EcosystemAnalyzer(self.paths.repo_claude)
        stale = analyzer.find_stale(manifest, days)

        if not stale:
            self.output.success("No stale files to prune.")
            return ExitCode.OK

        self.output.header(f"Pruning {len(stale)} stale file(s)")
        for f in stale:
            if dry_run:
                self.output.info(f"  Would remove: {f}")
            else:
                fpath = self.paths.repo_claude / f
                if fpath.exists():
                    fpath.unlink()
                    self.output.success(f"  Removed: {f}")

        if dry_run:
            self.output.info("\n  (dry run - no changes made)")

        if self.output.json_mode:
            self.output.set_json("pruned", stale)
            self.output.set_json("dry_run", dry_run)
        return ExitCode.OK

    def _eco_archive(self) -> int:
        """Move a file to repo archive directory."""
        target = getattr(self.args, "file", "")
        if not target:
            self.output.error("No file specified.")
            return ExitCode.ERROR

        src = self.paths.repo_claude / target
        if not src.exists():
            self.output.error(f"File not found: {target}")
            return ExitCode.ERROR

        archive_dir = self.paths.repo_root / "claude" / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        dst = archive_dir / target
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        self.output.success(f"Archived {target} -> archive/{target}")

        if self.output.json_mode:
            self.output.set_json("archived", target)
            self.output.set_json("archive_path", str(dst))
        return ExitCode.OK

    def _cmd_drift(self) -> int:
        """Compare local state against known machine versions from file history."""
        if not self._require_init():
            return ExitCode.NOT_INITIALIZED

        manifest = Manifest.load(self.paths.manifest_path)
        if not manifest.file_history:
            self.output.info("No file history available. Sync at least once with schema v2.")
            return ExitCode.OK

        self.output.header("Version drift analysis")
        current_hashes = FileHasher.walk_directory(self.paths.repo_claude)

        # Group last-known hashes by machine
        machines: Dict[str, Dict[str, str]] = {}  # machine_id -> {path: hash}
        for path, entries in manifest.file_history.items():
            for entry in entries:
                mid = entry.get("machine_id", "unknown")
                hostname = entry.get("hostname", "unknown")
                key = f"{hostname} ({mid})"
                if key not in machines:
                    machines[key] = {}
                machines[key][path] = entry.get("hash", "")

        if not machines:
            self.output.info("  No machine data in history.")
            return ExitCode.OK

        for machine, file_hashes in sorted(machines.items()):
            drifted = []
            for path, h in file_hashes.items():
                current = current_hashes.get(path, "")
                if current != h:
                    drifted.append(path)
            if drifted:
                self.output.warning(f"  {machine}: {len(drifted)} file(s) drifted")
                for d in drifted[:5]:
                    self.output.info(f"    {d}")
                if len(drifted) > 5:
                    self.output.info(f"    ... and {len(drifted) - 5} more")
            else:
                self.output.success(f"  {machine}: in sync")

        if self.output.json_mode:
            self.output.set_json("machines", {k: len(v) for k, v in machines.items()})

        return ExitCode.OK

    def _cmd_tracker(self) -> int:
        """Manage tracker connections."""
        return handle_tracker_command(self.args)

    def _cmd_pair(self) -> int:
        """Pair with a remote device."""
        return handle_pair_command(self.args, self.output)

    def _cmd_resolve(self) -> int:
        """Show and resolve sync conflicts interactively."""
        if not self._require_init():
            return ExitCode.NOT_INITIALIZED

        ours = getattr(self.args, "ours", False)
        theirs = getattr(self.args, "theirs", False)

        manifest = Manifest.load(self.paths.manifest_path)
        base_hashes = manifest.files if manifest.files else None

        home_hashes = FileHasher.walk_directory(self.paths.home_claude)
        repo_hashes = FileHasher.walk_directory(self.paths.repo_claude)

        # Use push direction to detect conflicts (symmetric)
        diff_result = DiffEngine.compare(
            home_hashes, repo_hashes, "push", base_hashes=base_hashes
        )

        if not diff_result.has_conflicts:
            self.output.success("No conflicts detected.")
            if self.output.json_mode:
                self.output.set_json("status", "clean")
                self.output.set_json("conflicts", [])
            return ExitCode.OK

        self.output.header(f"Conflicts ({len(diff_result.conflicted)} files)")

        for conflict in diff_result.conflicted:
            self.output.file_conflicted(conflict.path)

            # Show side-by-side diff for text files
            home_path = self.paths.relative_to_home(conflict.path)
            repo_path = self.paths.relative_to_repo(conflict.path)

            try:
                home_content = home_path.read_text(encoding="utf-8", errors="replace") if home_path.exists() else ""
                repo_content = repo_path.read_text(encoding="utf-8", errors="replace") if repo_path.exists() else ""
                home_lines = home_content.splitlines(keepends=True)
                repo_lines = repo_content.splitlines(keepends=True)
                diff_lines = list(difflib.unified_diff(
                    repo_lines, home_lines,
                    fromfile=f"repo/{conflict.path}",
                    tofile=f"home/{conflict.path}",
                ))
                if diff_lines:
                    for line in diff_lines[:30]:  # limit preview
                        self.output.diff_line(line.rstrip("\n"))
                    if len(diff_lines) > 30:
                        self.output.info(f"    ... ({len(diff_lines) - 30} more lines)")
                    print()
            except UnicodeDecodeError:
                self.output.info(f"    [binary file]")

        # Batch resolution
        if ours:
            self.output.info("\nResolving all conflicts with --ours (home version)")
            self.output.info("Run 'claude-sync push --ours -y' to apply.")
        elif theirs:
            self.output.info("\nResolving all conflicts with --theirs (repo version)")
            self.output.info("Run 'claude-sync pull --theirs -y' to apply.")
        else:
            self.output.info("\nResolution options:")
            self.output.info("  claude-sync push --ours -y     Keep home version, push to repo")
            self.output.info("  claude-sync pull --theirs -y   Keep repo version, pull to home")
            self.output.info("  claude-sync push --force -y    Force push (home overwrites repo)")
            self.output.info("  claude-sync pull --force -y    Force pull (repo overwrites home)")

        if self.output.json_mode:
            self.output.set_json("status", "conflicts")
            self.output.set_json("conflicts", [c.to_dict() for c in diff_result.conflicted])

        return ExitCode.DIRTY

    def _cmd_history(self) -> int:
        """Show file sync history from manifest."""
        if not self._require_init():
            return ExitCode.NOT_INITIALIZED

        specific_file = getattr(self.args, "file", None)
        manifest = Manifest.load(self.paths.manifest_path)

        if not manifest.file_history:
            self.output.info("No file history recorded yet.")
            self.output.info("History is recorded starting with schema v2 syncs.")
            if self.output.json_mode:
                self.output.set_json("history", {})
            return ExitCode.OK

        if specific_file:
            self.output.header(f"History: {specific_file}")
            entries = manifest.file_history.get(specific_file, [])
            if not entries:
                self.output.info(f"  No history for {specific_file}")
                if self.output.json_mode:
                    self.output.set_json("file", specific_file)
                    self.output.set_json("entries", [])
                return ExitCode.OK

            for entry in reversed(entries):
                action_color = self.output.GREEN if entry.get("action") == "push" else self.output.BLUE
                action_str = self.output._color(entry.get("action", "?"), action_color)
                self.output.info(
                    f"  {entry.get('timestamp', '?')[:19]}  "
                    f"{action_str}  "
                    f"{entry.get('hostname', '?')}  "
                    f"{entry.get('hash', '?')[:12]}"
                )
            if self.output.json_mode:
                self.output.set_json("file", specific_file)
                self.output.set_json("entries", entries)
        else:
            self.output.header("File sync history")
            # Show summary: files with most history entries
            by_count = sorted(
                manifest.file_history.items(),
                key=lambda x: len(x[1]),
                reverse=True,
            )
            for path, entries in by_count[:20]:
                last = entries[-1] if entries else {}
                self.output.info(
                    f"  {path}  "
                    f"({len(entries)} syncs, last: {last.get('action', '?')} "
                    f"from {last.get('hostname', '?')} "
                    f"at {last.get('timestamp', '?')[:19]})"
                )
            if len(by_count) > 20:
                self.output.info(f"  ... and {len(by_count) - 20} more files")

            if self.output.json_mode:
                self.output.set_json("history", manifest.file_history)
                self.output.set_json("total_tracked_files", len(manifest.file_history))

        return ExitCode.OK


    # -------------------------------------------------------------------------
    # Genome commands
    # -------------------------------------------------------------------------

    def _cmd_genome(self) -> int:
        """Skill Genome: dependency management for the skill ecosystem."""
        sub = getattr(self.args, "genome_command", None)
        if not sub:
            self.output.error(
                "Usage: claude-sync genome "
                "{scan|health|graph|install|extract-triggers|assemble-triggers|package}"
            )
            return ExitCode.ERROR

        genome_handlers = {
            "scan": self._genome_scan,
            "health": self._genome_health,
            "graph": self._genome_graph,
            "install": self._genome_install,
            "extract-triggers": self._genome_extract_triggers,
            "assemble-triggers": self._genome_assemble_triggers,
            "package": self._genome_package,
        }
        handler = genome_handlers.get(sub)
        if handler:
            return handler()
        self.output.error(f"Unknown genome command: {sub}")
        return ExitCode.ERROR

    def _genome_scan(self) -> int:
        """Show all skills with their dependency declarations."""
        engine = SkillGenomeEngine(self.paths.home_claude, self.paths.repo_claude)
        genomes = engine.scan_all()

        if not genomes:
            self.output.warning("No skills found")
            return ExitCode.OK

        self.output.header(f"Skill Genome Scan ({len(genomes)} skills)")

        with_deps = {n: g for n, g in genomes.items() if g.requires.has_any}
        without_deps = {n: g for n, g in genomes.items() if not g.requires.has_any}

        if with_deps:
            self.output.info(f"\nSkills with dependencies ({len(with_deps)}):")
            for name, genome in sorted(with_deps.items()):
                version = f" v{genome.version}" if genome.version != "0.0.0" else ""
                self.output.info(f"  {name}{version}")
                r = genome.requires
                if r.skills:
                    self.output.detail(f"    skills: {', '.join(r.skills)}")
                if r.agents:
                    self.output.detail(f"    agents: {', '.join(r.agents)}")
                if r.mcp_servers:
                    self.output.detail(f"    mcp-servers: {', '.join(r.mcp_servers)}")
                if r.rules:
                    self.output.detail(f"    rules: {', '.join(r.rules)}")

        if without_deps:
            self.output.info(f"\nSkills without dependencies ({len(without_deps)}):")
            for name in sorted(without_deps):
                self.output.detail(f"  {name}")

        if self.output.json_mode:
            self.output.set_json("skills", {n: g.to_dict() for n, g in genomes.items()})
            self.output.set_json("total", len(genomes))
            self.output.set_json("with_deps", len(with_deps))

        return ExitCode.OK

    def _genome_health(self) -> int:
        """Check for missing deps, broken MCP refs, circular chains."""
        engine = SkillGenomeEngine(self.paths.home_claude, self.paths.repo_claude)
        genomes = engine.scan_all()
        issues = engine.check_health(genomes)

        self.output.header(f"Skill Genome Health ({len(genomes)} skills)")

        if not issues:
            self.output.success("All dependency checks passed")
        else:
            errors = [i for i in issues if i.severity == "error"]
            warnings = [i for i in issues if i.severity == "warning"]

            if errors:
                self.output.info(f"\nErrors ({len(errors)}):")
                for issue in errors:
                    self.output.error(f"  {issue.message}")
                    self.output.detail(f"    Affects: {issue.skill_name}")
                    self.output.detail(f"    Fix: {issue.remediation}")

            if warnings:
                self.output.info(f"\nWarnings ({len(warnings)}):")
                for issue in warnings:
                    self.output.warning(f"  {issue.message}")
                    self.output.detail(f"    Affects: {issue.skill_name}")
                    self.output.detail(f"    Fix: {issue.remediation}")

        if self.output.json_mode:
            self.output.set_json("issues", [i.to_dict() for i in issues])
            self.output.set_json("error_count", len([i for i in issues if i.severity == "error"]))
            self.output.set_json("warning_count", len([i for i in issues if i.severity == "warning"]))

        return ExitCode.OK if not any(i.severity == "error" for i in issues) else ExitCode.ERROR

    def _genome_graph(self) -> int:
        """Visualize dependency tree."""
        engine = SkillGenomeEngine(self.paths.home_claude, self.paths.repo_claude)
        genomes = engine.scan_all()
        skill_name = getattr(self.args, "skill", None)
        fmt = getattr(self.args, "format", "tree")

        if skill_name:
            if skill_name not in genomes:
                self.output.error(f"Skill '{skill_name}' not found")
                return ExitCode.ERROR

            self.output.header(f"Dependency tree: {skill_name}")

            if fmt == "tree":
                lines = engine.format_tree(skill_name, genomes)
                for line in lines:
                    self.output.info(line)
            elif fmt == "flat":
                order, cycles = engine.resolve_dependencies(skill_name, genomes)
                self.output.info("Install order (deps first):")
                for i, name in enumerate(order, 1):
                    self.output.info(f"  {i}. {name}")
                if cycles:
                    self.output.warning(f"Cycles detected: {', '.join(cycles)}")
            elif fmt == "dot":
                self.output.info("digraph genome {")
                self.output.info("  rankdir=LR;")
                order, _ = engine.resolve_dependencies(skill_name, genomes)
                for name in order:
                    g = genomes.get(name)
                    if g:
                        for dep in g.requires.skills:
                            self.output.info(f'  "{name}" -> "{dep}";')
                        for agent in g.requires.agents:
                            self.output.info(f'  "{name}" -> "{agent}" [style=dashed];')
                        for mcp in g.requires.mcp_servers:
                            self.output.info(f'  "{name}" -> "{mcp}" [style=dotted];')
                self.output.info("}")
        else:
            self.output.header("Full dependency graph")
            graph = engine.build_full_graph(genomes)
            for key, node in sorted(graph.items()):
                if node.dependencies or node.dependents:
                    deps = ", ".join(node.dependencies) if node.dependencies else "none"
                    status = "exists" if node.exists else "MISSING"
                    self.output.info(f"  {key} [{status}] -> {deps}")

        if self.output.json_mode:
            if skill_name:
                order, cycles = engine.resolve_dependencies(skill_name, genomes)
                self.output.set_json("skill", skill_name)
                self.output.set_json("install_order", order)
                self.output.set_json("cycles", cycles)
            else:
                graph = engine.build_full_graph(genomes)
                self.output.set_json("graph", {
                    k: {"name": v.name, "type": v.node_type, "exists": v.exists,
                        "deps": v.dependencies, "dependents": v.dependents}
                    for k, v in graph.items()
                })

        return ExitCode.OK

    def _genome_install(self) -> int:
        """Install skill with full dependency resolution."""
        if not self._require_init():
            return ExitCode.NOT_INITIALIZED

        skill_name = self.args.skill
        engine = SkillGenomeEngine(self.paths.home_claude, self.paths.repo_claude)

        # Scan from repo (that's where we install FROM)
        genomes = engine.scan_all(self.paths.repo_claude)
        if skill_name not in genomes:
            self.output.error(f"Skill '{skill_name}' not found in repo")
            return ExitCode.ERROR

        # Show install plan
        order, cycles = engine.resolve_dependencies(skill_name, genomes)
        if cycles:
            self.output.error(f"Circular dependencies: {', '.join(cycles)}")
            return ExitCode.ERROR

        self.output.header(f"Install plan for '{skill_name}'")
        self.output.info("\nDependency tree:")
        for line in engine.format_tree(skill_name, genomes):
            self.output.info(f"  {line}")

        self.output.info(f"\nWill install {len(order)} skill(s): {', '.join(order)}")

        genome = genomes[skill_name]
        if genome.requires.agents:
            self.output.info(f"Agents: {', '.join(genome.requires.agents)}")
        if genome.requires.rules:
            self.output.info(f"Rules: {', '.join(genome.requires.rules)}")
        if genome.requires.mcp_servers:
            self.output.info(f"MCP servers: {', '.join(genome.requires.mcp_servers)}")

        if not self.output.confirm("\nProceed with install?"):
            self.output.info("Cancelled")
            return ExitCode.OK

        try:
            installed, warnings = engine.install_skill(
                skill_name, self.paths.repo_claude, self.paths.home_claude, genomes
            )
        except (FileNotFoundError, ValueError) as e:
            self.output.error(str(e))
            return ExitCode.ERROR

        # Assemble triggers after install
        self._maybe_assemble_triggers(target="home")

        for path in installed:
            self.output.file_added(path)
        for warn in warnings:
            self.output.warning(warn)

        self.output.success(f"Installed {len(installed)} item(s)")

        if self.output.json_mode:
            self.output.set_json("installed", installed)
            self.output.set_json("warnings", warnings)

        return ExitCode.OK

    def _genome_extract_triggers(self) -> int:
        """One-time migration: split skill-rules.json into per-skill files."""
        engine = SkillGenomeEngine(self.paths.home_claude, self.paths.repo_claude)
        rules_path = self.paths.home_claude / "skills" / "skill-rules.json"

        if not rules_path.exists():
            self.output.error(f"skill-rules.json not found at {rules_path}")
            return ExitCode.ERROR

        self.output.header("Extract triggers from skill-rules.json")
        counts = engine.extract_triggers(rules_path)

        self.output.success(
            f"Extracted {counts['extracted']} trigger(s) to per-skill files"
        )
        if counts["skipped"]:
            self.output.warning(
                f"Skipped {counts['skipped']} skill(s) (no matching directory)"
            )
        if counts["agent_triggers"]:
            self.output.info(
                f"Wrote {counts['agent_triggers']} agent trigger(s) to _agent-triggers.json"
            )
        self.output.info(
            "\nskill-rules.json is now a derived artifact. "
            "It will be auto-assembled from per-skill triggers.json files on push/pull."
        )

        if self.output.json_mode:
            self.output.set_json("counts", counts)

        return ExitCode.OK

    def _genome_assemble_triggers(self) -> int:
        """Rebuild skill-rules.json from per-skill triggers.json files."""
        engine = SkillGenomeEngine(self.paths.home_claude, self.paths.repo_claude)
        skills_dir = self.paths.home_claude / "skills"

        if not engine.has_atomized_triggers(skills_dir):
            self.output.warning("No per-skill triggers.json files found")
            self.output.info("Run 'claude-sync genome extract-triggers' first")
            return ExitCode.ERROR

        assembled = engine.assemble_triggers(skills_dir)
        rules_path = skills_dir / "skill-rules.json"
        with open(rules_path, "w") as f:
            json.dump(assembled, f, indent=2, sort_keys=True)
            f.write("\n")

        skill_count = len(assembled.get("skills", {}))
        agent_count = len(assembled.get("agents", {}))
        self.output.success(
            f"Assembled skill-rules.json: {skill_count} skills, {agent_count} agents"
        )

        if self.output.json_mode:
            self.output.set_json("skills", skill_count)
            self.output.set_json("agents", agent_count)

        return ExitCode.OK

    def _genome_package(self) -> int:
        """Export skill + all deps as shareable tar.gz."""
        skill_name = self.args.skill
        engine = SkillGenomeEngine(self.paths.home_claude, self.paths.repo_claude)
        genomes = engine.scan_all()

        if skill_name not in genomes:
            self.output.error(f"Skill '{skill_name}' not found")
            return ExitCode.ERROR

        output_path = getattr(self.args, "output", None)
        if not output_path:
            output_path = f"{skill_name}.tar.gz"
        output_path = Path(output_path)

        self.output.header(f"Packaging '{skill_name}'")

        try:
            included = engine.package_skill(skill_name, genomes, output_path)
        except ValueError as e:
            self.output.error(str(e))
            return ExitCode.ERROR

        for path in included:
            self.output.detail(f"  + {path}")
        self.output.success(f"Packaged {len(included)} file(s) to {output_path}")

        if self.output.json_mode:
            self.output.set_json("output", str(output_path))
            self.output.set_json("files", included)

        return ExitCode.OK

    def _maybe_assemble_triggers(self, target: str = "both") -> None:
        """Reassemble skill-rules.json from per-skill triggers if they exist."""
        engine = SkillGenomeEngine(self.paths.home_claude, self.paths.repo_claude)

        if target in ("home", "both"):
            home_skills = self.paths.home_claude / "skills"
            if engine.has_atomized_triggers(home_skills):
                assembled = engine.assemble_triggers(home_skills)
                rules_path = home_skills / "skill-rules.json"
                with open(rules_path, "w") as f:
                    json.dump(assembled, f, indent=2, sort_keys=True)
                    f.write("\n")
                self.output.detail("Assembled skill-rules.json from per-skill triggers (home)")

        if target in ("repo", "both") and self.paths.repo_claude:
            repo_skills = self.paths.repo_claude / "skills"
            if engine.has_atomized_triggers(repo_skills):
                assembled = engine.assemble_triggers(repo_skills)
                rules_path = repo_skills / "skill-rules.json"
                with open(rules_path, "w") as f:
                    json.dump(assembled, f, indent=2, sort_keys=True)
                    f.write("\n")
                self.output.detail("Assembled skill-rules.json from per-skill triggers (repo)")

    def _filter_derived_artifacts(self, diff_result: DiffResult) -> bool:
        """Remove derived artifacts (skill-rules.json) from diff when atomized triggers exist.
        Returns True if filtering was applied."""
        engine = SkillGenomeEngine(self.paths.home_claude, self.paths.repo_claude)
        home_skills = self.paths.home_claude / "skills"
        repo_skills = self.paths.repo_claude / "skills" if self.paths.repo_claude else None

        has_atomized = engine.has_atomized_triggers(home_skills) or (
            repo_skills is not None and engine.has_atomized_triggers(repo_skills)
        )
        if not has_atomized:
            return False

        derived = "skills/skill-rules.json"
        diff_result.added = [c for c in diff_result.added if c.path != derived]
        diff_result.modified = [c for c in diff_result.modified if c.path != derived]
        diff_result.deleted = [c for c in diff_result.deleted if c.path != derived]
        diff_result.conflicted = [c for c in diff_result.conflicted if c.path != derived]
        return True


    # ---- Workset commands ----

    def _cmd_workset(self) -> int:
        """Manage agent/skill worksets."""
        sub = getattr(self.args, "workset_command", None)
        if not sub:
            # Default: show status + list
            return self._workset_status_and_list()

        workset_handlers = {
            "init": self._workset_init,
            "create": self._workset_create,
            "show": self._workset_show,
            "list": self._workset_list,
            "activate": self._workset_activate,
            "deactivate": self._workset_deactivate,
            "delete": self._workset_delete,
            "status": self._workset_status,
            "suggest": self._workset_suggest,
        }
        handler = workset_handlers.get(sub)
        if handler:
            return handler()
        self.output.error(f"Unknown workset command: {sub}")
        return ExitCode.ERROR

    def _workset_init(self) -> int:
        """Initialize the vault."""
        engine = WorksetEngine(self.paths.home_claude)

        # Check for interrupted activation
        if engine.recover_if_needed():
            self.output.warning("Recovered from interrupted activation. Full set restored.")

        self.output.header("Workset Vault Initialization")
        agent_count, skill_count = engine.init_vault()
        self.output.success(f"Vault initialized: {agent_count} agents, {skill_count} skills")
        self.output.info("  All agents and skills are active (full set).")
        self.output.info("  Use 'claude-sync workset create <name>' to define worksets.")

        if self.output.json_mode:
            self.output.set_json("vault", {
                "agents": agent_count, "skills": skill_count, "initialized": True
            })
        return ExitCode.OK

    def _workset_create(self) -> int:
        """Create a new workset definition."""
        engine = WorksetEngine(self.paths.home_claude)
        name = self.args.name

        # Parse comma-separated lists
        def csv(val):
            return [x.strip() for x in val.split(",") if x.strip()] if val else []

        ws = WorksetDefinition(
            name=name,
            description=getattr(self.args, "description", "") or "",
            tags=csv(getattr(self.args, "tags", None)),
            agents=csv(getattr(self.args, "agents", None)),
            skills=csv(getattr(self.args, "skills", None)),
            exclude_agents=csv(getattr(self.args, "exclude_agents", None)),
            exclude_skills=csv(getattr(self.args, "exclude_skills", None)),
            extends=csv(getattr(self.args, "extends", None)),
        )

        path = engine.save_definition(ws)
        self.output.success(f"Workset '{name}' created at {path}")

        # Show resolved preview
        agents, skills = engine.resolve_workset(name)
        self.output.info(f"  Resolves to: {len(agents)} agents, {len(skills)} skills")

        if self.output.json_mode:
            self.output.set_json("workset", ws.to_dict())
            self.output.set_json("resolved", {"agents": agents, "skills": skills})
        return ExitCode.OK

    def _workset_show(self) -> int:
        """Show a workset definition and resolved contents."""
        engine = WorksetEngine(self.paths.home_claude)
        name = self.args.name
        definitions = engine.load_definitions()

        if name not in definitions:
            self.output.error(f"Workset '{name}' not found.")
            return ExitCode.ERROR

        ws = definitions[name]
        self.output.header(f"Workset: {name}")
        if ws.description:
            self.output.info(f"  Description: {ws.description}")
        if ws.tags:
            self.output.info(f"  Tags: {', '.join(ws.tags)}")
        if ws.agents:
            self.output.info(f"  Agents: {', '.join(ws.agents)}")
        if ws.skills:
            self.output.info(f"  Skills: {', '.join(ws.skills)}")
        if ws.exclude_agents:
            self.output.info(f"  Exclude agents: {', '.join(ws.exclude_agents)}")
        if ws.exclude_skills:
            self.output.info(f"  Exclude skills: {', '.join(ws.exclude_skills)}")
        if ws.extends:
            self.output.info(f"  Extends: {', '.join(ws.extends)}")

        agents, skills = engine.resolve_workset(name, definitions)
        self.output.info(f"\n  Resolved: {len(agents)} agents, {len(skills)} skills")

        if getattr(self.args, "resolved", False):
            self.output.info("\n  Agents:")
            for a in agents:
                self.output.info(f"    {a}")
            self.output.info("\n  Skills:")
            for s in skills:
                self.output.info(f"    {s}")

        if self.output.json_mode:
            self.output.set_json("workset", ws.to_dict())
            self.output.set_json("resolved", {"agents": agents, "skills": skills})
        return ExitCode.OK

    def _workset_list(self) -> int:
        """List all defined worksets."""
        engine = WorksetEngine(self.paths.home_claude)
        definitions = engine.load_definitions()
        state = engine.load_state()

        if not definitions:
            self.output.warning("No worksets defined.")
            self.output.info("  Use 'claude-sync workset create <name>' to create one.")
            return ExitCode.OK

        self.output.header(f"Worksets ({len(definitions)})")
        for name, ws in definitions.items():
            active = " [ACTIVE]" if state.active_workset == name else ""
            agents, skills = engine.resolve_workset(name, definitions)
            desc = f" - {ws.description}" if ws.description else ""
            self.output.info(f"  {name}{active}{desc} ({len(agents)} agents, {len(skills)} skills)")

        if self.output.json_mode:
            self.output.set_json("worksets", {n: w.to_dict() for n, w in definitions.items()})
            self.output.set_json("active", state.active_workset)
        return ExitCode.OK

    def _workset_activate(self) -> int:
        """Activate a workset."""
        engine = WorksetEngine(self.paths.home_claude)
        name = self.args.name

        state = engine.load_state()
        if not state.vault_initialized:
            self.output.error("Vault not initialized. Run 'claude-sync workset init' first.")
            return ExitCode.ERROR

        try:
            agent_count, skill_count = engine.activate(name)
        except (ValueError, RuntimeError) as e:
            self.output.error(str(e))
            return ExitCode.ERROR

        self.output.success(f"Workset '{name}' activated: {agent_count} agents, {skill_count} skills")
        vault_ac, vault_sc = engine._count_vault()
        self.output.info(f"  (vault total: {vault_ac} agents, {vault_sc} skills)")

        if self.output.json_mode:
            self.output.set_json("activated", {
                "workset": name, "agents": agent_count, "skills": skill_count
            })
        return ExitCode.OK

    def _workset_deactivate(self) -> int:
        """Deactivate workset, restore full set."""
        engine = WorksetEngine(self.paths.home_claude)
        state = engine.load_state()

        if not state.vault_initialized:
            self.output.error("Vault not initialized.")
            return ExitCode.ERROR

        if not state.active_workset:
            self.output.info("No workset is currently active (full set already loaded).")
            return ExitCode.OK

        agent_count, skill_count = engine.deactivate()
        self.output.success(f"Deactivated. Full set restored: {agent_count} agents, {skill_count} skills")

        if self.output.json_mode:
            self.output.set_json("deactivated", {
                "agents": agent_count, "skills": skill_count
            })
        return ExitCode.OK

    def _workset_delete(self) -> int:
        """Delete a workset definition."""
        engine = WorksetEngine(self.paths.home_claude)
        name = self.args.name

        state = engine.load_state()
        if state.active_workset == name:
            self.output.error(f"Cannot delete active workset '{name}'. Deactivate first.")
            return ExitCode.ERROR

        if engine.delete_definition(name):
            self.output.success(f"Workset '{name}' deleted.")
        else:
            self.output.error(f"Workset '{name}' not found.")
            return ExitCode.ERROR
        return ExitCode.OK

    def _workset_status(self) -> int:
        """Show current workset activation state."""
        engine = WorksetEngine(self.paths.home_claude)
        state = engine.load_state()

        self.output.header("Workset Status")
        self.output.info(f"  Vault initialized: {'yes' if state.vault_initialized else 'no'}")

        if state.vault_initialized:
            vault_ac, vault_sc = engine._count_vault()
            self.output.info(f"  Vault: {vault_ac} agents, {vault_sc} skills")

        if state.active_workset:
            self.output.info(f"  Active workset: {state.active_workset}")
            self.output.info(f"  Activated at: {state.activated_at}")
            active_ac, active_sc = engine._count_active()
            self.output.info(f"  Active: {active_ac} agents, {active_sc} skills")
        else:
            active_ac, active_sc = engine._count_active()
            self.output.info(f"  Active workset: none (full set)")
            self.output.info(f"  Active: {active_ac} agents, {active_sc} skills")

        if self.output.json_mode:
            self.output.set_json("state", state.to_dict())
        return ExitCode.OK

    def _workset_status_and_list(self) -> int:
        """Default: show status + list."""
        self._workset_status()
        engine = WorksetEngine(self.paths.home_claude)
        definitions = engine.load_definitions()
        if definitions:
            self.output.info("")
            state = engine.load_state()
            for name, ws in definitions.items():
                active = " [ACTIVE]" if state.active_workset == name else ""
                agents, skills = engine.resolve_workset(name, definitions)
                desc = f" - {ws.description}" if ws.description else ""
                self.output.info(f"  {name}{active}{desc} ({len(agents)} agents, {len(skills)} skills)")
        return ExitCode.OK

    def _workset_suggest(self) -> int:
        """Suggest a workset based on project context."""
        engine = WorksetEngine(self.paths.home_claude)
        state = engine.load_state()

        if not state.vault_initialized:
            self.output.error("Vault not initialized.")
            return ExitCode.ERROR

        result = engine.suggest_workset()
        if not result:
            self.output.info("No suggestion available. Use more worksets to build affinity data.")
            return ExitCode.OK

        name, confidence, reason = result
        pct = int(confidence * 100)
        self.output.header(f"Suggested workset: {name} ({pct}% confidence)")
        self.output.info(f"  Reason: {reason}")

        auto = getattr(self.args, "auto", False)
        if auto and confidence >= 0.8:
            try:
                agent_count, skill_count = engine.activate(name)
                self.output.success(f"Auto-activated '{name}': {agent_count} agents, {skill_count} skills")
            except (ValueError, RuntimeError) as e:
                self.output.error(f"Auto-activation failed: {e}")
                return ExitCode.ERROR
        elif auto:
            self.output.info(f"  Confidence too low for auto-activation ({pct}% < 80%). Use manually:")
            self.output.info(f"    claude-sync workset activate {name}")
        else:
            self.output.info(f"  To activate: claude-sync workset activate {name}")

        if self.output.json_mode:
            self.output.set_json("suggestion", {
                "workset": name, "confidence": confidence, "reason": reason
            })
        return ExitCode.OK


# =============================================================================
# Entry point
# =============================================================================

def main() -> int:
    app = ClaudeSync()
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
