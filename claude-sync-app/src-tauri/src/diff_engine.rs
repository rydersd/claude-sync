// ==========================================================================
// Diff engine - compares local and remote file trees
// Produces a DiffResult listing added, modified, and deleted files.
// ==========================================================================

use std::collections::{HashMap, HashSet};

use crate::protocol::{DiffResult, FileEntry, FileDiff};

/// Compare two file trees and produce a diff result.
///
/// `local_files` is the local config tree (this machine).
/// `remote_files` is the remote config tree (from a peer).
///
/// The diff is always from the perspective of what the remote has
/// that differs from local:
///   - added: files that exist on remote but not locally
///   - modified: files that exist on both but have different hashes
///   - deleted: files that exist locally but not on remote
pub fn compare_trees(
    local_files: &HashMap<String, FileEntry>,
    remote_files: &HashMap<String, FileEntry>,
) -> DiffResult {
    let local_paths: HashSet<&String> = local_files.keys().collect();
    let remote_paths: HashSet<&String> = remote_files.keys().collect();

    let mut added: Vec<FileDiff> = Vec::new();
    let mut modified: Vec<FileDiff> = Vec::new();
    let mut deleted: Vec<FileDiff> = Vec::new();

    // Files on remote but not locally -> "added" (would be new if pulled)
    let mut added_paths: Vec<&&String> = remote_paths.difference(&local_paths).collect();
    added_paths.sort();
    for path in added_paths {
        let remote_entry = &remote_files[*path];
        added.push(FileDiff {
            path: (**path).clone(),
            change_type: "added".to_string(),
            local_hash: None,
            remote_hash: Some(remote_entry.sha256.clone()),
            local_size: None,
            remote_size: Some(remote_entry.size),
        });
    }

    // Files on both but with different hashes -> "modified"
    let mut common_paths: Vec<&&String> = local_paths.intersection(&remote_paths).collect();
    common_paths.sort();
    for path in common_paths {
        let local_entry = &local_files[*path];
        let remote_entry = &remote_files[*path];

        if local_entry.sha256 != remote_entry.sha256 {
            modified.push(FileDiff {
                path: (**path).clone(),
                change_type: "modified".to_string(),
                local_hash: Some(local_entry.sha256.clone()),
                remote_hash: Some(remote_entry.sha256.clone()),
                local_size: Some(local_entry.size),
                remote_size: Some(remote_entry.size),
            });
        }
    }

    // Files locally but not on remote -> "deleted" (exist locally but remote doesn't have them)
    let mut deleted_paths: Vec<&&String> = local_paths.difference(&remote_paths).collect();
    deleted_paths.sort();
    for path in deleted_paths {
        let local_entry = &local_files[*path];
        deleted.push(FileDiff {
            path: (**path).clone(),
            change_type: "deleted".to_string(),
            local_hash: Some(local_entry.sha256.clone()),
            remote_hash: None,
            local_size: Some(local_entry.size),
            remote_size: None,
        });
    }

    let total_changes = added.len() + modified.len() + deleted.len();

    DiffResult {
        added,
        modified,
        deleted,
        total_changes,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_entry(path: &str, hash: &str, size: u64) -> (String, FileEntry) {
        (
            path.to_string(),
            FileEntry {
                path: path.to_string(),
                sha256: hash.to_string(),
                size,
                executable: false,
                mtime_epoch: 0,
            },
        )
    }

    #[test]
    fn test_identical_trees_produce_no_diff() {
        let mut local = HashMap::new();
        let mut remote = HashMap::new();

        local.insert("CLAUDE.md".to_string(), FileEntry {
            path: "CLAUDE.md".to_string(),
            sha256: "abc123".to_string(),
            size: 100,
            executable: false,
            mtime_epoch: 0,
        });
        remote.insert("CLAUDE.md".to_string(), FileEntry {
            path: "CLAUDE.md".to_string(),
            sha256: "abc123".to_string(),
            size: 100,
            executable: false,
            mtime_epoch: 0,
        });

        let diff = compare_trees(&local, &remote);
        assert_eq!(diff.total_changes, 0);
        assert!(diff.added.is_empty());
        assert!(diff.modified.is_empty());
        assert!(diff.deleted.is_empty());
    }

    #[test]
    fn test_added_files_detected() {
        let local: HashMap<String, FileEntry> = HashMap::new();
        let remote: HashMap<String, FileEntry> = vec![
            make_entry("agents/new.md", "def456", 200),
        ].into_iter().collect();

        let diff = compare_trees(&local, &remote);
        assert_eq!(diff.added.len(), 1);
        assert_eq!(diff.added[0].path, "agents/new.md");
        assert_eq!(diff.modified.len(), 0);
        assert_eq!(diff.deleted.len(), 0);
    }

    #[test]
    fn test_modified_files_detected() {
        let local: HashMap<String, FileEntry> = vec![
            make_entry("CLAUDE.md", "old_hash", 100),
        ].into_iter().collect();
        let remote: HashMap<String, FileEntry> = vec![
            make_entry("CLAUDE.md", "new_hash", 150),
        ].into_iter().collect();

        let diff = compare_trees(&local, &remote);
        assert_eq!(diff.modified.len(), 1);
        assert_eq!(diff.modified[0].path, "CLAUDE.md");
        assert_eq!(diff.added.len(), 0);
        assert_eq!(diff.deleted.len(), 0);
    }

    #[test]
    fn test_deleted_files_detected() {
        let local: HashMap<String, FileEntry> = vec![
            make_entry("rules/old.md", "abc", 50),
        ].into_iter().collect();
        let remote: HashMap<String, FileEntry> = HashMap::new();

        let diff = compare_trees(&local, &remote);
        assert_eq!(diff.deleted.len(), 1);
        assert_eq!(diff.deleted[0].path, "rules/old.md");
        assert_eq!(diff.added.len(), 0);
        assert_eq!(diff.modified.len(), 0);
    }
}
