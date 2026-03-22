// ==========================================================================
// Sync engine - orchestrates push and pull operations with a remote peer.
// Connects to the peer via TCP, exchanges manifests, computes diffs,
// and transfers files based on the selected direction.
// Message types and wire format per PROTOCOL.md.
// ==========================================================================

use base64::Engine as _;
use base64::engine::general_purpose::STANDARD as BASE64;
use std::collections::HashMap;
use std::fs;
use std::time::SystemTime;

use crate::config_scanner;
use crate::conflict_resolver::{self, ConflictResolution};
use crate::connection::FramedConnection;
use crate::device_identity;
use crate::diff_engine;
use crate::protocol::{
    DiffResult, FileEntry, PeerInfo, SyncMessage, SyncResult, PROTOCOL_VERSION,
};

/// Execute a push operation: send local config files to a remote peer.
///
/// 1. Connect to the peer
/// 2. Exchange Hello messages
/// 3. Request the peer's manifest
/// 4. Compute diff (local vs remote)
/// 5. Send files that are new or modified locally
pub async fn push_to_peer(peer: &PeerInfo) -> Result<SyncResult, String> {
    let mut conn = FramedConnection::connect(&peer.address)
        .await
        .map_err(|e| format!("Failed to connect to {}: {}", peer.address, e))?;

    // Send Hello
    let local_files = config_scanner::scan_config_dir();
    let fingerprint = config_scanner::compute_fingerprint(&local_files);

    let hello = SyncMessage::Hello {
        device_id: device_identity::get_or_create_device_id(),
        name: device_identity::get_hostname(),
        protocol_version: PROTOCOL_VERSION,
        fingerprint: fingerprint.clone(),
        platform: device_identity::get_platform(),
        file_count: local_files.len() as u32,
        capabilities: Some(vec!["file_watch".to_string(), "keepalive".to_string()]),
    };

    conn.send(&hello)
        .await
        .map_err(|e| format!("Failed to send Hello: {}", e))?;

    // Wait for peer's Hello response
    let peer_hello = conn
        .receive()
        .await
        .map_err(|e| format!("Failed to receive Hello: {}", e))?;

    match &peer_hello {
        SyncMessage::Hello { protocol_version, fingerprint: peer_fp, .. } => {
            if *protocol_version != PROTOCOL_VERSION {
                return Err(format!(
                    "Protocol version mismatch: local={}, remote={}",
                    PROTOCOL_VERSION, protocol_version
                ));
            }
            // Quick sync check: if fingerprints match, no sync needed
            if *peer_fp == fingerprint {
                conn.send(&SyncMessage::SyncNotNeeded {
                    fingerprint: fingerprint.clone(),
                })
                .await
                .map_err(|e| format!("Failed to send SyncNotNeeded: {}", e))?;

                let _ = conn.shutdown().await;
                return Ok(SyncResult {
                    success: true,
                    files_transferred: 0,
                    direction: "push".to_string(),
                    error: None,
                });
            }
        }
        SyncMessage::Error { code, message } => {
            return Err(format!("Peer error ({}): {}", code, message));
        }
        _ => {
            return Err("Expected Hello response from peer".to_string());
        }
    }

    // Request manifest
    conn.send(&SyncMessage::ManifestRequest)
        .await
        .map_err(|e| format!("Failed to request manifest: {}", e))?;

    // Receive manifest
    let remote_manifest = conn
        .receive()
        .await
        .map_err(|e| format!("Failed to receive manifest: {}", e))?;

    let remote_files: HashMap<String, FileEntry> = match remote_manifest {
        SyncMessage::Manifest { files } => {
            // Convert ManifestFileEntry to FileEntry for diff engine
            files
                .into_iter()
                .map(|f| {
                    (
                        f.path.clone(),
                        FileEntry {
                            path: f.path,
                            sha256: f.sha256,
                            size: f.size,
                            executable: false,
                            mtime_epoch: f.mtime_epoch,
                        },
                    )
                })
                .collect()
        }
        SyncMessage::Error { code, message } => {
            return Err(format!("Peer error ({}): {}", code, message));
        }
        _ => {
            return Err("Expected Manifest response from peer".to_string());
        }
    };

    // Compute diff: what files do we have that the peer doesn't or differs
    let diff = diff_engine::compare_trees(&remote_files, &local_files);

    // Collect paths of files to send (added + modified from local perspective)
    let files_to_send: Vec<String> = diff
        .added
        .iter()
        .chain(diff.modified.iter())
        .map(|d| d.path.clone())
        .collect();

    if files_to_send.is_empty() {
        // Nothing to push
        conn.send(&SyncMessage::SyncComplete {
            files_transferred: 0,
            direction: "push".to_string(),
        })
        .await
        .map_err(|e| format!("Failed to send SyncComplete: {}", e))?;

        let _ = conn.shutdown().await;
        return Ok(SyncResult {
            success: true,
            files_transferred: 0,
            direction: "push".to_string(),
            error: None,
        });
    }

    // Request sync
    conn.send(&SyncMessage::SyncRequest {
        direction: "push".to_string(),
        files: files_to_send.clone(),
    })
    .await
    .map_err(|e| format!("Failed to send SyncRequest: {}", e))?;

    // Wait for acknowledgment
    let ack = conn
        .receive()
        .await
        .map_err(|e| format!("Failed to receive SyncAck: {}", e))?;

    match ack {
        SyncMessage::SyncAck { accepted, reason } => {
            if !accepted {
                let _ = conn.shutdown().await;
                return Err(format!(
                    "Peer rejected sync: {}",
                    reason.unwrap_or_else(|| "no reason given".to_string())
                ));
            }
        }
        _ => {
            let _ = conn.shutdown().await;
            return Err("Expected SyncAck from peer".to_string());
        }
    }

    // Transfer files
    let claude_home = config_scanner::claude_home_dir();
    let mut transferred: u32 = 0;

    for path in &files_to_send {
        let file_path = claude_home.join(path);
        if let Ok(content) = fs::read(&file_path) {
            let entry = &local_files[path];
            let content_base64 = BASE64.encode(&content);

            conn.send(&SyncMessage::File {
                path: path.clone(),
                content_base64,
                sha256: entry.sha256.clone(),
                size: entry.size,
                executable: entry.executable,
            })
            .await
            .map_err(|e| format!("Failed to send file {}: {}", path, e))?;

            // Wait for file acknowledgment
            let file_ack = conn
                .receive()
                .await
                .map_err(|e| format!("Failed to receive FileAck for {}: {}", path, e))?;

            match file_ack {
                SyncMessage::FileAck { success, error, .. } => {
                    if success {
                        transferred += 1;
                    } else {
                        log::warn!(
                            "Peer failed to write {}: {}",
                            path,
                            error.unwrap_or_default()
                        );
                    }
                }
                _ => {
                    log::warn!("Unexpected response for FileAck on {}", path);
                }
            }
        }
    }

    // Send completion
    conn.send(&SyncMessage::SyncComplete {
        files_transferred: transferred,
        direction: "push".to_string(),
    })
    .await
    .map_err(|e| format!("Failed to send SyncComplete: {}", e))?;

    let _ = conn.shutdown().await;

    Ok(SyncResult {
        success: true,
        files_transferred: transferred,
        direction: "push".to_string(),
        error: None,
    })
}

/// Execute a pull operation: receive config files from a remote peer.
///
/// 1. Connect to the peer
/// 2. Exchange Hello messages
/// 3. Request the peer's manifest
/// 4. Compute diff (remote vs local)
/// 5. Request files that are new or modified on the remote
pub async fn pull_from_peer(peer: &PeerInfo) -> Result<SyncResult, String> {
    let mut conn = FramedConnection::connect(&peer.address)
        .await
        .map_err(|e| format!("Failed to connect to {}: {}", peer.address, e))?;

    // Send Hello
    let local_files = config_scanner::scan_config_dir();
    let fingerprint = config_scanner::compute_fingerprint(&local_files);

    let hello = SyncMessage::Hello {
        device_id: device_identity::get_or_create_device_id(),
        name: device_identity::get_hostname(),
        protocol_version: PROTOCOL_VERSION,
        fingerprint: fingerprint.clone(),
        platform: device_identity::get_platform(),
        file_count: local_files.len() as u32,
        capabilities: Some(vec!["file_watch".to_string(), "keepalive".to_string()]),
    };

    conn.send(&hello)
        .await
        .map_err(|e| format!("Failed to send Hello: {}", e))?;

    // Wait for peer's Hello
    let peer_hello = conn
        .receive()
        .await
        .map_err(|e| format!("Failed to receive Hello: {}", e))?;

    match &peer_hello {
        SyncMessage::Hello { protocol_version, fingerprint: peer_fp, .. } => {
            if *protocol_version != PROTOCOL_VERSION {
                return Err(format!(
                    "Protocol version mismatch: local={}, remote={}",
                    PROTOCOL_VERSION, protocol_version
                ));
            }
            // Quick sync check: if fingerprints match, no sync needed
            if *peer_fp == fingerprint {
                conn.send(&SyncMessage::SyncNotNeeded {
                    fingerprint: fingerprint.clone(),
                })
                .await
                .map_err(|e| format!("Failed to send SyncNotNeeded: {}", e))?;

                let _ = conn.shutdown().await;
                return Ok(SyncResult {
                    success: true,
                    files_transferred: 0,
                    direction: "pull".to_string(),
                    error: None,
                });
            }
        }
        SyncMessage::Error { code, message } => {
            return Err(format!("Peer error ({}): {}", code, message));
        }
        _ => {
            return Err("Expected Hello response from peer".to_string());
        }
    }

    // Request manifest
    conn.send(&SyncMessage::ManifestRequest)
        .await
        .map_err(|e| format!("Failed to request manifest: {}", e))?;

    // Receive manifest
    let remote_manifest = conn
        .receive()
        .await
        .map_err(|e| format!("Failed to receive manifest: {}", e))?;

    let remote_files: HashMap<String, FileEntry> = match remote_manifest {
        SyncMessage::Manifest { files } => {
            // Convert ManifestFileEntry to FileEntry for diff engine
            files
                .into_iter()
                .map(|f| {
                    (
                        f.path.clone(),
                        FileEntry {
                            path: f.path,
                            sha256: f.sha256,
                            size: f.size,
                            executable: false,
                            mtime_epoch: f.mtime_epoch,
                        },
                    )
                })
                .collect()
        }
        SyncMessage::Error { code, message } => {
            return Err(format!("Peer error ({}): {}", code, message));
        }
        _ => {
            return Err("Expected Manifest response from peer".to_string());
        }
    };

    // Compute diff: what does the remote have that we don't
    let diff = diff_engine::compare_trees(&local_files, &remote_files);

    // Files we want to pull (added + modified from remote perspective)
    let files_to_pull: Vec<String> = diff
        .added
        .iter()
        .chain(diff.modified.iter())
        .map(|d| d.path.clone())
        .collect();

    if files_to_pull.is_empty() {
        conn.send(&SyncMessage::SyncComplete {
            files_transferred: 0,
            direction: "pull".to_string(),
        })
        .await
        .map_err(|e| format!("Failed to send SyncComplete: {}", e))?;

        let _ = conn.shutdown().await;
        return Ok(SyncResult {
            success: true,
            files_transferred: 0,
            direction: "pull".to_string(),
            error: None,
        });
    }

    // Request sync (pull = we ask them to send us files)
    conn.send(&SyncMessage::SyncRequest {
        direction: "pull".to_string(),
        files: files_to_pull.clone(),
    })
    .await
    .map_err(|e| format!("Failed to send SyncRequest: {}", e))?;

    // Wait for acknowledgment
    let ack = conn
        .receive()
        .await
        .map_err(|e| format!("Failed to receive SyncAck: {}", e))?;

    match ack {
        SyncMessage::SyncAck { accepted, reason } => {
            if !accepted {
                let _ = conn.shutdown().await;
                return Err(format!(
                    "Peer rejected sync: {}",
                    reason.unwrap_or_else(|| "no reason given".to_string())
                ));
            }
        }
        _ => {
            let _ = conn.shutdown().await;
            return Err("Expected SyncAck from peer".to_string());
        }
    }

    // Receive files
    let claude_home = config_scanner::claude_home_dir();
    let mut transferred: u32 = 0;

    for _ in 0..files_to_pull.len() {
        let msg = conn
            .receive()
            .await
            .map_err(|e| format!("Failed to receive file: {}", e))?;

        match msg {
            SyncMessage::File {
                path,
                content_base64,
                sha256,
                size,
                executable,
            } => {
                // Decode the file content
                let content = BASE64
                    .decode(&content_base64)
                    .map_err(|e| format!("Failed to decode base64 for {}: {}", path, e))?;

                // Verify size per PROTOCOL.md Section 4.3
                if content.len() as u64 != size {
                    log::warn!(
                        "Size mismatch for {}: expected {}, got {}",
                        path,
                        size,
                        content.len()
                    );
                    conn.send(&SyncMessage::FileAck {
                        path: path.clone(),
                        success: false,
                        error: Some("size_mismatch".to_string()),
                    })
                    .await
                    .map_err(|e| format!("Failed to send FileAck: {}", e))?;
                    continue;
                }

                // Verify hash per PROTOCOL.md Section 4.3
                let actual_hash = compute_sha256(&content);
                if actual_hash != sha256 {
                    log::warn!(
                        "Hash mismatch for {}: expected {}, got {}",
                        path,
                        sha256,
                        actual_hash
                    );
                    conn.send(&SyncMessage::FileAck {
                        path: path.clone(),
                        success: false,
                        error: Some("checksum_mismatch".to_string()),
                    })
                    .await
                    .map_err(|e| format!("Failed to send FileAck: {}", e))?;
                    continue;
                }

                // Atomic write per PROTOCOL.md Section 5.6:
                // Write to temp file, then rename to final path.
                let file_path = claude_home.join(&path);
                if let Some(parent) = file_path.parent() {
                    let _ = fs::create_dir_all(parent);
                }

                // Write to temp file in the same directory
                let tmp_path = file_path.with_extension(format!(
                    "tmp.{}",
                    std::process::id()
                ));
                let write_result = fs::write(&tmp_path, &content)
                    .and_then(|_| fs::rename(&tmp_path, &file_path));

                match write_result {
                    Ok(_) => {
                        // Set executable permission if needed (Unix only)
                        #[cfg(unix)]
                        if executable {
                            use std::os::unix::fs::PermissionsExt;
                            let _ = fs::set_permissions(
                                &file_path,
                                fs::Permissions::from_mode(0o755),
                            );
                        }

                        transferred += 1;
                        conn.send(&SyncMessage::FileAck {
                            path,
                            success: true,
                            error: None,
                        })
                        .await
                        .map_err(|e| format!("Failed to send FileAck: {}", e))?;
                    }
                    Err(e) => {
                        // Clean up temp file if rename failed
                        let _ = fs::remove_file(&tmp_path);
                        conn.send(&SyncMessage::FileAck {
                            path,
                            success: false,
                            error: Some(e.to_string()),
                        })
                        .await
                        .map_err(|e| format!("Failed to send FileAck: {}", e))?;
                    }
                }
            }
            SyncMessage::SyncComplete { .. } => {
                // Peer finished sending early
                break;
            }
            SyncMessage::Error { code, message } => {
                let _ = conn.shutdown().await;
                return Err(format!("Peer error during transfer ({}): {}", code, message));
            }
            _ => {
                log::warn!("Unexpected message during file transfer");
            }
        }
    }

    // Send our completion
    conn.send(&SyncMessage::SyncComplete {
        files_transferred: transferred,
        direction: "pull".to_string(),
    })
    .await
    .map_err(|e| format!("Failed to send SyncComplete: {}", e))?;

    let _ = conn.shutdown().await;

    Ok(SyncResult {
        success: true,
        files_transferred: transferred,
        direction: "pull".to_string(),
        error: None,
    })
}

/// Compute the diff between local configs and a remote peer's configs.
/// Connects to the peer, exchanges manifests, and returns the diff.
pub async fn compute_peer_diff(peer: &PeerInfo) -> Result<DiffResult, String> {
    let mut conn = FramedConnection::connect(&peer.address)
        .await
        .map_err(|e| format!("Failed to connect to {}: {}", peer.address, e))?;

    // Send Hello
    let local_files = config_scanner::scan_config_dir();
    let fingerprint = config_scanner::compute_fingerprint(&local_files);

    conn.send(&SyncMessage::Hello {
        device_id: device_identity::get_or_create_device_id(),
        name: device_identity::get_hostname(),
        protocol_version: PROTOCOL_VERSION,
        fingerprint,
        platform: device_identity::get_platform(),
        file_count: local_files.len() as u32,
        capabilities: Some(vec!["file_watch".to_string(), "keepalive".to_string()]),
    })
    .await
    .map_err(|e| format!("Failed to send Hello: {}", e))?;

    // Receive peer Hello
    let _peer_hello = conn
        .receive()
        .await
        .map_err(|e| format!("Failed to receive Hello: {}", e))?;

    // Request manifest
    conn.send(&SyncMessage::ManifestRequest)
        .await
        .map_err(|e| format!("Failed to request manifest: {}", e))?;

    // Receive manifest
    let remote_manifest = conn
        .receive()
        .await
        .map_err(|e| format!("Failed to receive manifest: {}", e))?;

    let remote_files: HashMap<String, FileEntry> = match remote_manifest {
        SyncMessage::Manifest { files } => {
            // Convert ManifestFileEntry to FileEntry for diff engine
            files
                .into_iter()
                .map(|f| {
                    (
                        f.path.clone(),
                        FileEntry {
                            path: f.path,
                            sha256: f.sha256,
                            size: f.size,
                            executable: false,
                            mtime_epoch: f.mtime_epoch,
                        },
                    )
                })
                .collect()
        }
        _ => {
            return Err("Expected Manifest response from peer".to_string());
        }
    };

    let _ = conn.shutdown().await;

    // Compute and return the diff
    Ok(diff_engine::compare_trees(&local_files, &remote_files))
}

/// Compute SHA-256 hash of raw bytes (used for verifying received files).
fn compute_sha256(data: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    let mut hasher = Sha256::new();
    hasher.update(data);
    let result = hasher.finalize();
    result.iter().map(|b| format!("{:02x}", b)).collect()
}

// ==========================================================================
// v2: Real-time file change handling
// ==========================================================================

/// Create a FileChanged message for a local file that was modified.
/// Reads the file, computes its hash, and optionally includes the content.
///
/// `previous_sha256` is the hash we last knew the file had (from config_scanner
/// state before the change). This lets the receiver detect conflicts.
pub fn create_file_changed_message(
    path: &str,
    change: &str,
    previous_sha256: Option<String>,
) -> Result<SyncMessage, String> {
    let claude_home = config_scanner::claude_home_dir();
    let file_path = claude_home.join(path);

    match change {
        "deleted" => {
            let now_ms = chrono::Utc::now().timestamp_millis();
            Ok(SyncMessage::FileChanged {
                path: path.to_string(),
                change: "deleted".to_string(),
                sha256: None,
                size: None,
                mtime_epoch: now_ms / 1000,
                change_epoch_ms: now_ms,
                previous_sha256,
                content_base64: None,
                executable: None,
            })
        }
        "modified" | "created" => {
            let content = fs::read(&file_path)
                .map_err(|e| format!("Failed to read {}: {}", path, e))?;

            let sha256 = compute_sha256(&content);
            let size = content.len() as u64;

            let mtime_epoch = fs::metadata(&file_path)
                .ok()
                .and_then(|m| m.modified().ok())
                .and_then(|t| t.duration_since(SystemTime::UNIX_EPOCH).ok())
                .map(|d| d.as_secs() as i64)
                .unwrap_or(0);

            let now_ms = chrono::Utc::now().timestamp_millis();

            let content_base64 = BASE64.encode(&content);

            // Check executable bit (Unix)
            #[cfg(unix)]
            let executable = {
                use std::os::unix::fs::PermissionsExt;
                fs::metadata(&file_path)
                    .map(|m| m.permissions().mode() & 0o111 != 0)
                    .unwrap_or(false)
            };
            #[cfg(not(unix))]
            let executable = false;

            Ok(SyncMessage::FileChanged {
                path: path.to_string(),
                change: change.to_string(),
                sha256: Some(sha256),
                size: Some(size),
                mtime_epoch,
                change_epoch_ms: now_ms,
                previous_sha256,
                content_base64: Some(content_base64),
                executable: Some(executable),
            })
        }
        _ => Err(format!("Unknown change type: {}", change)),
    }
}

/// Handle an incoming FileChanged message from a remote peer.
/// Checks for conflicts using the previous_sha256 field, resolves them
/// using the conflict resolver, and writes the file atomically.
///
/// Returns a FileChangedAck message indicating whether the change was accepted.
pub fn handle_incoming_file_changed(
    msg: &SyncMessage,
    remote_device_id: &str,
) -> Result<SyncMessage, String> {
    let (path, change, sha256, size, _mtime_epoch, change_epoch_ms, previous_sha256, content_base64, executable) =
        match msg {
            SyncMessage::FileChanged {
                path,
                change,
                sha256,
                size,
                mtime_epoch,
                change_epoch_ms,
                previous_sha256,
                content_base64,
                executable,
            } => (
                path, change, sha256, size, mtime_epoch, change_epoch_ms,
                previous_sha256, content_base64, executable,
            ),
            _ => return Err("Expected FileChanged message".to_string()),
        };

    let claude_home = config_scanner::claude_home_dir();
    let file_path = claude_home.join(path);
    let local_device_id = device_identity::get_or_create_device_id();

    // Handle deletion
    if change == "deleted" {
        if file_path.exists() {
            fs::remove_file(&file_path)
                .map_err(|e| format!("Failed to delete {}: {}", path, e))?;
            log::info!("Deleted file via file_changed: {}", path);
        }
        return Ok(SyncMessage::FileChangedAck {
            path: path.clone(),
            accepted: true,
            conflict: false,
        });
    }

    // For create/modify, we need the content
    let content_b64 = content_base64.as_ref().ok_or_else(|| {
        format!("FileChanged for '{}' is missing content_base64", path)
    })?;
    let remote_content = BASE64.decode(content_b64)
        .map_err(|e| format!("Failed to decode base64 for {}: {}", path, e))?;

    // Verify size if provided
    if let Some(expected_size) = size {
        if remote_content.len() as u64 != *expected_size {
            return Ok(SyncMessage::FileChangedAck {
                path: path.clone(),
                accepted: false,
                conflict: false,
            });
        }
    }

    // Verify hash if provided
    if let Some(expected_hash) = sha256 {
        let actual_hash = compute_sha256(&remote_content);
        if actual_hash != *expected_hash {
            return Ok(SyncMessage::FileChangedAck {
                path: path.clone(),
                accepted: false,
                conflict: false,
            });
        }
    }

    // Check for conflict: does the local file's current hash match the expected previous_sha256?
    let mut conflict_detected = false;
    let final_content = if file_path.exists() {
        let local_content = fs::read(&file_path)
            .map_err(|e| format!("Failed to read local {}: {}", path, e))?;
        let local_hash = compute_sha256(&local_content);

        if let Some(prev_hash) = previous_sha256 {
            if local_hash != *prev_hash {
                // Conflict: local file has changed since the remote last saw it
                conflict_detected = true;

                let local_mtime_ms = fs::metadata(&file_path)
                    .ok()
                    .and_then(|m| m.modified().ok())
                    .and_then(|t| t.duration_since(SystemTime::UNIX_EPOCH).ok())
                    .map(|d| d.as_millis() as i64)
                    .unwrap_or(0);

                match conflict_resolver::resolve_conflict(
                    path,
                    &local_content,
                    &remote_content,
                    local_mtime_ms,
                    *change_epoch_ms,
                    &local_device_id,
                    remote_device_id,
                ) {
                    ConflictResolution::AcceptRemote(data) => data,
                    ConflictResolution::KeepLocal => {
                        return Ok(SyncMessage::FileChangedAck {
                            path: path.clone(),
                            accepted: false,
                            conflict: true,
                        });
                    }
                    ConflictResolution::Merge(data) => data,
                }
            } else {
                // No conflict: local matches expected previous state
                remote_content
            }
        } else {
            // No previous_sha256 provided; accept remote unconditionally
            remote_content
        }
    } else {
        // File doesn't exist locally; just accept the remote content
        remote_content
    };

    // Atomic write: temp file + rename (per PROTOCOL.md Section 5.6)
    if let Some(parent) = file_path.parent() {
        let _ = fs::create_dir_all(parent);
    }

    let tmp_path = file_path.with_extension(format!("tmp.{}", std::process::id()));
    fs::write(&tmp_path, &final_content)
        .and_then(|_| fs::rename(&tmp_path, &file_path))
        .map_err(|e| {
            let _ = fs::remove_file(&tmp_path);
            format!("Failed to write {}: {}", path, e)
        })?;

    // Set executable permission if requested (Unix only)
    #[cfg(unix)]
    if let Some(true) = executable {
        use std::os::unix::fs::PermissionsExt;
        let _ = fs::set_permissions(&file_path, fs::Permissions::from_mode(0o755));
    }

    log::info!(
        "Applied file_changed for '{}' (change={}, conflict={})",
        path,
        change,
        conflict_detected
    );

    Ok(SyncMessage::FileChangedAck {
        path: path.clone(),
        accepted: true,
        conflict: conflict_detected,
    })
}
