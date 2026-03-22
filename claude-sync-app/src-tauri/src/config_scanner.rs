// ==========================================================================
// Config scanner - walks ~/.claude/ and computes file hashes
// Matches the same sync paths and exclusions as the Python claude-sync.py tool.
// Fingerprint algorithm per PROTOCOL.md Section 2.3.
// ==========================================================================

use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::time::SystemTime;
use walkdir::WalkDir;

use crate::protocol::{FileEntry, ManifestFileEntry};

/// Paths to sync (relative to ~/.claude/). Matches Python tool's SYNC_PATHS.
/// memory/ included so the entire "second brain" ecosystem syncs across machines
/// (voice profiles, story banks, feedback, project context, etc.)
const SYNC_PATHS: &[&str] = &[
    "CLAUDE.md",
    "agents/",
    "skills/",
    "rules/",
    "hooks/",
    "scripts/",
    "memory/",
    "worksets/",
    "plugins/",
    "keybindings.json",
    ".claude-sync-capabilities.json",
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
    "shell-snapshots/",
    "paste-cache/",
    "file-history/",
    "debug/",
    "statsig/",
    ".workset-vault/",
    "worksets/_state.json",
    "worksets/_affinity.json",
    "teams/",
    "tasks/",
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

/// Scan the ~/.claude/ directory and return a map of relative_path -> FileEntry
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

        // Get file metadata
        let metadata = match fs::metadata(abs_path) {
            Ok(m) => m,
            Err(_) => continue,
        };

        let size = metadata.len();

        // Get modification time as Unix epoch seconds
        let mtime_epoch = metadata
            .modified()
            .ok()
            .and_then(|t| t.duration_since(SystemTime::UNIX_EPOCH).ok())
            .map(|d| d.as_secs() as i64)
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
                mtime_epoch,
            },
        );
    }

    result
}

/// Convert internal FileEntry map to ManifestFileEntry list for the wire protocol.
pub fn to_manifest_entries(files: &HashMap<String, FileEntry>) -> Vec<ManifestFileEntry> {
    files
        .values()
        .map(|f| ManifestFileEntry {
            path: f.path.clone(),
            sha256: f.sha256.clone(),
            size: f.size,
            mtime_epoch: f.mtime_epoch,
        })
        .collect()
}

/// Compute the SHA-256 hash of a file, reading in one pass.
/// Returns the hex-encoded hash string.
pub fn hash_file(path: &Path) -> Result<String, std::io::Error> {
    let data = fs::read(path)?;
    let mut hasher = Sha256::new();
    hasher.update(&data);
    let result = hasher.finalize();
    Ok(hex_encode(&result))
}

/// Compute a combined fingerprint hash from a set of file entries
/// per PROTOCOL.md Section 2.3.
///
/// Algorithm:
/// 1. Create "path:sha256" strings for each file
/// 2. Sort lexicographically
/// 3. Join with "\n" (no trailing newline)
/// 4. SHA-256 the joined string
/// 5. Return the first 16 hex characters
pub fn compute_fingerprint(files: &HashMap<String, FileEntry>) -> String {
    if files.is_empty() {
        return String::new();
    }

    // Build sorted list of "path:hash" entries
    let mut entries: Vec<String> = files
        .iter()
        .map(|(path, entry)| format!("{}:{}", path, entry.sha256))
        .collect();
    entries.sort();

    // Join with newline (no trailing newline per spec)
    let joined = entries.join("\n");

    // SHA-256 the joined string
    let mut hasher = Sha256::new();
    hasher.update(joined.as_bytes());
    let result = hasher.finalize();
    let full_hash = hex_encode(&result);

    // Return first 16 characters per PROTOCOL.md Section 2.3
    full_hash[..16].to_string()
}

/// Check if a relative path falls under one of the syncable path prefixes.
fn is_syncable(rel_path: &str) -> bool {
    for sync_path in SYNC_PATHS {
        if sync_path.ends_with('/') {
            // Directory prefix match
            let prefix = sync_path.trim_end_matches('/');
            if rel_path.starts_with(prefix)
                && (rel_path.len() == prefix.len()
                    || rel_path.as_bytes()[prefix.len()] == b'/')
            {
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

    #[test]
    fn test_compute_fingerprint_returns_16_chars() {
        let mut files = HashMap::new();
        files.insert(
            "CLAUDE.md".to_string(),
            FileEntry {
                path: "CLAUDE.md".to_string(),
                sha256: "abc123".to_string(),
                size: 100,
                executable: false,
                mtime_epoch: 0,
            },
        );
        let fp = compute_fingerprint(&files);
        assert_eq!(fp.len(), 16, "Fingerprint should be 16 chars, got: {}", fp);
    }

    #[test]
    fn test_compute_fingerprint_empty_returns_empty() {
        let files = HashMap::new();
        let fp = compute_fingerprint(&files);
        assert!(fp.is_empty(), "Empty files should produce empty fingerprint");
    }

    #[test]
    fn test_compute_fingerprint_deterministic() {
        let mut files = HashMap::new();
        files.insert(
            "a.md".to_string(),
            FileEntry {
                path: "a.md".to_string(),
                sha256: "hash_a".to_string(),
                size: 10,
                executable: false,
                mtime_epoch: 0,
            },
        );
        files.insert(
            "b.md".to_string(),
            FileEntry {
                path: "b.md".to_string(),
                sha256: "hash_b".to_string(),
                size: 20,
                executable: false,
                mtime_epoch: 0,
            },
        );
        let fp1 = compute_fingerprint(&files);
        let fp2 = compute_fingerprint(&files);
        assert_eq!(fp1, fp2, "Fingerprint should be deterministic");
    }
}
