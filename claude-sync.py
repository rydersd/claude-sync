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
    python claude-sync.py doctor        Run health checks
    python claude-sync.py backup        Manage backups
    python claude-sync.py restore       Restore from backup

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
import stat
import sys
import uuid
from dataclasses import asdict, dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


# =============================================================================
# Constants
# =============================================================================

MANIFEST_SCHEMA_VERSION = 1
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
    change_type: str  # "added", "modified", "deleted"
    home_hash: Optional[str] = None
    repo_hash: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "change_type": self.change_type,
            "home_hash": self.home_hash,
            "repo_hash": self.repo_hash,
        }


@dataclass
class DiffResult:
    """Result of comparing home and repo file trees."""
    added: List[FileChange] = field(default_factory=list)
    modified: List[FileChange] = field(default_factory=list)
    deleted: List[FileChange] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.modified or self.deleted)

    @property
    def total_changes(self) -> int:
        return len(self.added) + len(self.modified) + len(self.deleted)

    def all_changes(self) -> List[FileChange]:
        return self.added + self.modified + self.deleted

    def to_dict(self) -> dict:
        return {
            "added": [c.to_dict() for c in self.added],
            "modified": [c.to_dict() for c in self.modified],
            "deleted": [c.to_dict() for c in self.deleted],
            "total_changes": self.total_changes,
        }


class DiffEngine:
    """Set-based hash comparison between home and repo."""

    @staticmethod
    def compare(home_hashes: Dict[str, str], repo_hashes: Dict[str, str],
                direction: str = "push") -> DiffResult:
        """
        Compare file trees.
        direction='push': home is source, repo is target (what's new/changed in home)
        direction='pull': repo is source, home is target (what's new/changed in repo)
        """
        result = DiffResult()
        if direction == "push":
            source, target = home_hashes, repo_hashes
        else:
            source, target = repo_hashes, home_hashes

        source_paths = set(source.keys())
        target_paths = set(target.keys())

        # Files in source but not in target -> added
        for path in sorted(source_paths - target_paths):
            result.added.append(FileChange(
                path=path,
                change_type="added",
                home_hash=home_hashes.get(path),
                repo_hash=repo_hashes.get(path),
            ))

        # Files in both but with different hashes -> modified
        for path in sorted(source_paths & target_paths):
            if source[path] != target[path]:
                result.modified.append(FileChange(
                    path=path,
                    change_type="modified",
                    home_hash=home_hashes.get(path),
                    repo_hash=repo_hashes.get(path),
                ))

        # Files in target but not in source -> deleted
        for path in sorted(target_paths - source_paths):
            result.deleted.append(FileChange(
                path=path,
                change_type="deleted",
                home_hash=home_hashes.get(path),
                repo_hash=repo_hashes.get(path),
            ))

        return result


@dataclass
class Manifest:
    """Manifest.json lifecycle: schema v1, file hashes, push provenance."""

    schema_version: int = MANIFEST_SCHEMA_VERSION
    files: Dict[str, str] = field(default_factory=dict)
    last_push: Optional[Dict[str, Any]] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        """Load manifest from disk."""
        if not path.exists():
            return cls()
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(
            schema_version=data.get("schema_version", MANIFEST_SCHEMA_VERSION),
            files=data.get("files", {}),
            last_push=data.get("last_push"),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
        )

    def save(self, path: Path) -> None:
        """Save manifest to disk."""
        now = datetime.datetime.utcnow().isoformat() + "Z"
        if not self.created_at:
            self.created_at = now
        self.updated_at = now
        data = {
            "schema_version": self.schema_version,
            "files": self.files,
            "last_push": self.last_push,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")

    def update_provenance(self) -> None:
        """Update push provenance with current machine info."""
        self.last_push = {
            "machine_id": str(uuid.getnode()),
            "hostname": platform.node(),
            "platform": platform.system(),
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "python_version": platform.python_version(),
        }


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

        summary_parts = []
        if diff_result.added:
            summary_parts.append(f"{len(diff_result.added)} added")
        if diff_result.modified:
            summary_parts.append(f"{len(diff_result.modified)} modified")
        if diff_result.deleted:
            summary_parts.append(f"{len(diff_result.deleted)} deleted")
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
            if manifest.schema_version != MANIFEST_SCHEMA_VERSION:
                return HealthCheck(
                    "manifest", False,
                    f"Schema version mismatch: {manifest.schema_version} (expected {MANIFEST_SCHEMA_VERSION})",
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
        push_p.add_argument("--force", action="store_true", help="Push even if secrets detected")

        # pull
        pull_p = subparsers.add_parser("pull", help="Pull repo/claude -> ~/.claude")
        pull_p.add_argument("--dry-run", action="store_true", help="Show what would happen")
        pull_p.add_argument("--yes", "-y", action="store_true", help="Skip confirmation")

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
        """Push ~/.claude -> repo/claude."""
        if not self._require_init():
            return ExitCode.NOT_INITIALIZED

        dry_run = getattr(self.args, "dry_run", False)
        force = getattr(self.args, "force", False)
        yes = getattr(self.args, "yes", False)

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

        # Compute diff
        home_hashes = FileHasher.walk_directory(self.paths.home_claude)
        repo_hashes = FileHasher.walk_directory(self.paths.repo_claude)
        diff_result = DiffEngine.compare(home_hashes, repo_hashes, "push")

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

        # Update manifest
        manifest = Manifest.load(self.paths.manifest_path)
        manifest.files = FileHasher.walk_directory(self.paths.repo_claude)
        manifest.update_provenance()
        manifest.save(self.paths.manifest_path)

        self.output.success(f"Pushed {count} file(s)")

        if self.output.json_mode:
            self.output.set_json("status", "pushed")
            self.output.set_json("files_synced", count)
            self.output.set_json("changes", diff_result.to_dict())

        return ExitCode.OK

    def _cmd_pull(self) -> int:
        """Pull repo/claude -> ~/.claude."""
        if not self._require_init():
            return ExitCode.NOT_INITIALIZED

        dry_run = getattr(self.args, "dry_run", False)
        yes = getattr(self.args, "yes", False)

        self.output.header("Pull: repo/claude -> ~/.claude")

        # Compute diff
        home_hashes = FileHasher.walk_directory(self.paths.home_claude)
        repo_hashes = FileHasher.walk_directory(self.paths.repo_claude)
        diff_result = DiffEngine.compare(home_hashes, repo_hashes, "pull")

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


# =============================================================================
# Entry point
# =============================================================================

def main() -> int:
    app = ClaudeSync()
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
