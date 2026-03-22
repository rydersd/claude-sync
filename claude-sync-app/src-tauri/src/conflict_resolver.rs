// ==========================================================================
// Conflict resolver for v2 real-time sync.
// When a file_changed message arrives with a previous_sha256 that doesn't
// match the local file's current hash, we have a conflict.
//
// Resolution strategy:
//   1. Newer timestamp wins (compare change_epoch_ms)
//   2. If timestamps are within 1000ms, lower device_id (lexicographic) wins
//   3. For memory/ paths, append-merge with \n---\n separator
// ==========================================================================

/// Result of resolving a file conflict.
#[derive(Debug, Clone)]
pub enum ConflictResolution {
    /// Accept the remote content and overwrite local.
    AcceptRemote(Vec<u8>),
    /// Keep the local content, reject the remote change.
    KeepLocal,
    /// Merge both versions (used for memory/ paths).
    Merge(Vec<u8>),
}

/// Timestamp proximity threshold (ms) below which we fall back to device_id tiebreaker.
/// If both changes happened within this window, timestamps are considered "simultaneous".
const TIMESTAMP_PROXIMITY_MS: i64 = 1000;

/// Separator used when append-merging memory files.
const MERGE_SEPARATOR: &[u8] = b"\n---\n";

/// Resolve a conflict when a remote file_changed arrives with a mismatched previous_sha256.
///
/// # Arguments
/// * `path` - Relative path (e.g., "rules/git-commits.md" or "memory/writing/voice-profile.md")
/// * `local_data` - Current local file content
/// * `remote_data` - Incoming remote file content
/// * `local_timestamp_ms` - Local file's last modification time as epoch milliseconds
/// * `remote_timestamp_ms` - Remote change_epoch_ms from the FileChanged message
/// * `local_device_id` - This device's UUID
/// * `remote_device_id` - The sending device's UUID
pub fn resolve_conflict(
    path: &str,
    local_data: &[u8],
    remote_data: &[u8],
    local_timestamp_ms: i64,
    remote_timestamp_ms: i64,
    local_device_id: &str,
    remote_device_id: &str,
) -> ConflictResolution {
    // Special case: memory/ paths use append-merge to avoid data loss.
    // These files accumulate knowledge (voice profiles, story banks, feedback)
    // and both sides may have valuable additions.
    if path.starts_with("memory/") {
        return merge_memory_files(local_data, remote_data);
    }

    // Compare timestamps: newer wins
    let time_diff = remote_timestamp_ms - local_timestamp_ms;

    if time_diff.abs() <= TIMESTAMP_PROXIMITY_MS {
        // Timestamps are too close to call — use deterministic tiebreaker.
        // Lower device_id (lexicographic ordering) wins so both sides
        // reach the same conclusion independently.
        if remote_device_id < local_device_id {
            log::info!(
                "Conflict on '{}': timestamps within {}ms, remote device_id wins (tiebreaker)",
                path,
                TIMESTAMP_PROXIMITY_MS
            );
            ConflictResolution::AcceptRemote(remote_data.to_vec())
        } else {
            log::info!(
                "Conflict on '{}': timestamps within {}ms, local device_id wins (tiebreaker)",
                path,
                TIMESTAMP_PROXIMITY_MS
            );
            ConflictResolution::KeepLocal
        }
    } else if time_diff > 0 {
        // Remote is newer
        log::info!(
            "Conflict on '{}': remote is {}ms newer, accepting remote",
            path,
            time_diff
        );
        ConflictResolution::AcceptRemote(remote_data.to_vec())
    } else {
        // Local is newer
        log::info!(
            "Conflict on '{}': local is {}ms newer, keeping local",
            path,
            time_diff.abs()
        );
        ConflictResolution::KeepLocal
    }
}

/// Merge two versions of a memory file by appending with a separator.
/// Deduplicates if the content is identical.
fn merge_memory_files(local_data: &[u8], remote_data: &[u8]) -> ConflictResolution {
    // If content is identical, no merge needed
    if local_data == remote_data {
        return ConflictResolution::KeepLocal;
    }

    // Append remote content after local with a separator
    let mut merged = Vec::with_capacity(local_data.len() + MERGE_SEPARATOR.len() + remote_data.len());
    merged.extend_from_slice(local_data);

    // Ensure there's a trailing newline before the separator
    if !local_data.is_empty() && !local_data.ends_with(b"\n") {
        merged.push(b'\n');
    }

    merged.extend_from_slice(MERGE_SEPARATOR);
    merged.extend_from_slice(remote_data);

    log::info!("Memory file conflict resolved via append-merge ({} + {} bytes)", local_data.len(), remote_data.len());
    ConflictResolution::Merge(merged)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_newer_remote_wins() {
        let result = resolve_conflict(
            "rules/test.md",
            b"local content",
            b"remote content",
            1000,  // local older
            5000,  // remote newer
            "device-aaa",
            "device-bbb",
        );
        match result {
            ConflictResolution::AcceptRemote(data) => {
                assert_eq!(data, b"remote content");
            }
            _ => panic!("Expected AcceptRemote"),
        }
    }

    #[test]
    fn test_newer_local_wins() {
        let result = resolve_conflict(
            "rules/test.md",
            b"local content",
            b"remote content",
            5000,  // local newer
            1000,  // remote older
            "device-aaa",
            "device-bbb",
        );
        assert!(matches!(result, ConflictResolution::KeepLocal));
    }

    #[test]
    fn test_simultaneous_lower_device_id_wins() {
        // Remote device_id is "aaa" (lower), local is "bbb"
        let result = resolve_conflict(
            "rules/test.md",
            b"local content",
            b"remote content",
            1000,
            1500,  // within 1000ms
            "device-bbb",  // local
            "device-aaa",  // remote (lower, should win)
        );
        match result {
            ConflictResolution::AcceptRemote(data) => {
                assert_eq!(data, b"remote content");
            }
            _ => panic!("Expected AcceptRemote (lower device_id wins)"),
        }
    }

    #[test]
    fn test_simultaneous_higher_device_id_loses() {
        // Remote device_id is "zzz" (higher), local is "aaa"
        let result = resolve_conflict(
            "rules/test.md",
            b"local content",
            b"remote content",
            1000,
            1500,  // within 1000ms
            "device-aaa",  // local (lower, should win)
            "device-zzz",  // remote (higher)
        );
        assert!(matches!(result, ConflictResolution::KeepLocal));
    }

    #[test]
    fn test_memory_files_append_merge() {
        let result = resolve_conflict(
            "memory/writing/voice-profile.md",
            b"local voice data",
            b"remote voice data",
            1000,
            5000,
            "device-aaa",
            "device-bbb",
        );
        match result {
            ConflictResolution::Merge(data) => {
                let merged = String::from_utf8(data).unwrap();
                assert!(merged.contains("local voice data"));
                assert!(merged.contains("remote voice data"));
                assert!(merged.contains("---"));
            }
            _ => panic!("Expected Merge for memory/ path"),
        }
    }

    #[test]
    fn test_memory_files_identical_keeps_local() {
        let result = resolve_conflict(
            "memory/writing/voice-profile.md",
            b"same content",
            b"same content",
            1000,
            5000,
            "device-aaa",
            "device-bbb",
        );
        assert!(matches!(result, ConflictResolution::KeepLocal));
    }
}
