// ==========================================================================
// Config scanner - walks ~/.claude/ and computes file hashes
// Matches the same sync paths and exclusions as the Python claude-sync.py tool.
// ==========================================================================

use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use walkdir::WalkDir;

use crate::protocol::FileEntry;

/// Paths to sync (relative to ~/.claude/). Matches Python tool's SYNC_PATHS.
const SYNC_PATHS: &[&str] = &[
    "CLAUDE.md",
    "agents/",
    "skills/",
    "rules/",
    "hooks/",
    "scripts/",
];

/// Paths that are always excluded from syncing. Matches Python tool's EXCLUDE_PATHS.
const EXCLUDE_PATHS: &[&str] = &[
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
];

/// Patterns for files/directories to skip during tree walking.
const WALK_EXCLUDE_PATTERNS: &[&str] = &[
    "node_modules",
    "__pycache__",
    ".pyc",
    ".DS_Store",
    ".swp",
    ".swo",
];

/// Get the path to the ~/.claude/ directory.
pub fn claude_home_dir() -> PathBuf {
    dirs::home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".claude")
}

/// Scan the ~/.claude/ directory and return a map of relative_path -> SHA-256 hash
/// for all syncable files.
pub fn scan_config_dir() -> HashMap<String, FileEntry> {
    let base_dir = claude_home_dir();
    scan_directory(&base_dir)
}

/// Scan an arbitrary directory (used for both local and backup dirs)
/// and return file entries keyed by relative path.
pub fn scan_directory(base_dir: &Path) -> HashMap<String, FileEntry> {
    let mut result = HashMap::new();

    if !base_dir.exists() {
        return result;
    }

    let walker = WalkDir::new(base_dir)
        .follow_links(false)
        .into_iter()
        .filter_entry(|entry| !should_exclude_walk_entry(entry, base_dir));

    for entry in walker.flatten() {
        if !entry.file_type().is_file() {
            continue;
        }

        let abs_path = entry.path();
        let rel_path = match abs_path.strip_prefix(base_dir) {
            Ok(p) => p.to_string_lossy().to_string(),
            Err(_) => continue,
        };

        // Check if this file falls under a syncable path prefix
        if !is_syncable(&rel_path) {
            continue;
        }

        // Check explicit exclusions
        if is_excluded(&rel_path) {
            continue;
        }

        // Compute SHA-256 hash
        let hash = match hash_file(abs_path) {
            Ok(h) => h,
            Err(_) => continue,
        };

        // Get file size
        let size = fs::metadata(abs_path)
            .map(|m| m.len())
            .unwrap_or(0);

        // Check executable bit (Unix-only)
        let executable = is_executable(abs_path);

        // Normalize path separators to forward slashes for cross-platform compat
        let normalized_path = rel_path.replace('\\', "/");

        result.insert(
            normalized_path.clone(),
            FileEntry {
                path: normalized_path,
                sha256: hash,
                size,
                executable,
            },
        );
    }

    result
}

/// Compute the SHA-256 hash of a file, reading in 64KB chunks.
/// Returns the hex-encoded hash string.
pub fn hash_file(path: &Path) -> Result<String, std::io::Error> {
    let data = fs::read(path)?;
    let mut hasher = Sha256::new();
    hasher.update(&data);
    let result = hasher.finalize();
    Ok(hex_encode(&result))
}

/// Compute a combined fingerprint hash from a set of file entries.
/// This creates a deterministic fingerprint by sorting all file hashes
/// alphabetically by path and hashing the concatenation.
pub fn compute_fingerprint(files: &HashMap<String, FileEntry>) -> String {
    let mut hasher = Sha256::new();

    // Sort by path for deterministic ordering
    let mut paths: Vec<&String> = files.keys().collect();
    paths.sort();

    for path in paths {
        if let Some(entry) = files.get(path) {
            hasher.update(path.as_bytes());
            hasher.update(b":");
            hasher.update(entry.sha256.as_bytes());
            hasher.update(b"\n");
        }
    }

    let result = hasher.finalize();
    hex_encode(&result)
}

/// Check if a relative path falls under one of the syncable path prefixes.
fn is_syncable(rel_path: &str) -> bool {
    for sync_path in SYNC_PATHS {
        if sync_path.ends_with('/') {
            // Directory prefix match
            let prefix = sync_path.trim_end_matches('/');
            if rel_path.starts_with(prefix) && (rel_path.len() == prefix.len() || rel_path.as_bytes()[prefix.len()] == b'/') {
                return true;
            }
        } else {
            // Exact file match
            if rel_path == *sync_path {
                return true;
            }
        }
    }

    // Also allow settings.json (for partial sync)
    if rel_path == "settings.json" {
        return true;
    }

    false
}

/// Check if a relative path should be excluded from syncing.
fn is_excluded(rel_path: &str) -> bool {
    for excl in EXCLUDE_PATHS {
        if excl.ends_with('/') {
            // Directory exclusion
            let dir_name = excl.trim_end_matches('/');
            if rel_path == dir_name || rel_path.starts_with(&format!("{}/", dir_name)) {
                return true;
            }
        } else {
            // File exclusion
            if rel_path == *excl {
                return true;
            }
        }
    }
    false
}

/// Check if a walkdir entry should be excluded based on filename patterns.
/// Returns true if the entry should be skipped.
fn should_exclude_walk_entry(entry: &walkdir::DirEntry, base_dir: &Path) -> bool {
    let name = entry.file_name().to_string_lossy();

    // Check walk exclusion patterns
    for pattern in WALK_EXCLUDE_PATTERNS {
        if name.contains(pattern) {
            return true;
        }
    }

    // For directories, also check the exclude paths list
    if entry.file_type().is_dir() {
        if let Ok(rel) = entry.path().strip_prefix(base_dir) {
            let rel_str = rel.to_string_lossy().to_string();
            for excl in EXCLUDE_PATHS {
                if excl.ends_with('/') {
                    let dir_name = excl.trim_end_matches('/');
                    if rel_str == dir_name || rel_str.starts_with(&format!("{}/", dir_name)) {
                        return true;
                    }
                }
            }
        }
    }

    false
}

/// Check if a file has the executable bit set (Unix).
#[cfg(unix)]
fn is_executable(path: &Path) -> bool {
    fs::metadata(path)
        .map(|m| m.permissions().mode() & 0o111 != 0)
        .unwrap_or(false)
}

/// On non-Unix platforms, check file extension for executability.
#[cfg(not(unix))]
fn is_executable(path: &Path) -> bool {
    path.extension()
        .and_then(|ext| ext.to_str())
        .map(|ext| matches!(ext, "sh" | "py" | "bash" | "zsh"))
        .unwrap_or(false)
}

/// Encode bytes as a lowercase hex string.
fn hex_encode(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{:02x}", b)).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_is_syncable() {
        assert!(is_syncable("CLAUDE.md"));
        assert!(is_syncable("agents/test.md"));
        assert!(is_syncable("rules/git-commits.md"));
        assert!(is_syncable("skills/sync/SKILL.md"));
        assert!(is_syncable("hooks/session-start.sh"));
        assert!(is_syncable("scripts/tool.py"));
        assert!(is_syncable("settings.json"));

        // These should NOT be syncable
        assert!(!is_syncable("random-file.txt"));
        assert!(!is_syncable("cache/data.json"));
        assert!(!is_syncable("history.jsonl"));
    }

    #[test]
    fn test_is_excluded() {
        assert!(is_excluded(".env"));
        assert!(is_excluded("mcp_config.json"));
        assert!(is_excluded("cache/data"));
        assert!(is_excluded("telemetry/events.json"));

        // These should NOT be excluded
        assert!(!is_excluded("CLAUDE.md"));
        assert!(!is_excluded("agents/test.md"));
    }

    #[test]
    fn test_hex_encode() {
        assert_eq!(hex_encode(&[0xde, 0xad, 0xbe, 0xef]), "deadbeef");
        assert_eq!(hex_encode(&[0x00, 0xff]), "00ff");
    }
}
