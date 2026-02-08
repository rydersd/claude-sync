// ==========================================================================
// Sync engine - orchestrates push and pull operations with a remote peer.
// Connects to the peer via TCP, exchanges manifests, computes diffs,
// and transfers files based on the selected direction.
// ==========================================================================

use base64::Engine as _;
use base64::engine::general_purpose::STANDARD as BASE64;
use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;

use crate::config_scanner;
use crate::connection::{ConnectionError, FramedConnection};
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
        fingerprint,
        platform: device_identity::get_platform(),
        file_count: local_files.len() as u32,
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
        SyncMessage::Hello { protocol_version, .. } => {
            if *protocol_version != PROTOCOL_VERSION {
                return Err(format!(
                    "Protocol version mismatch: local={}, remote={}",
                    PROTOCOL_VERSION, protocol_version
                ));
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
            files.into_iter().map(|f| (f.path.clone(), f)).collect()
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

            conn.send(&SyncMessage::FileTransfer {
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
        fingerprint,
        platform: device_identity::get_platform(),
        file_count: local_files.len() as u32,
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
        SyncMessage::Hello { protocol_version, .. } => {
            if *protocol_version != PROTOCOL_VERSION {
                return Err(format!(
                    "Protocol version mismatch: local={}, remote={}",
                    PROTOCOL_VERSION, protocol_version
                ));
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
            files.into_iter().map(|f| (f.path.clone(), f)).collect()
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
            SyncMessage::FileTransfer {
                path,
                content_base64,
                sha256,
                size: _,
                executable,
            } => {
                // Decode the file content
                let content = BASE64
                    .decode(&content_base64)
                    .map_err(|e| format!("Failed to decode base64 for {}: {}", path, e))?;

                // Verify hash
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
                        error: Some("Hash mismatch".to_string()),
                    })
                    .await
                    .map_err(|e| format!("Failed to send FileAck: {}", e))?;
                    continue;
                }

                // Write the file
                let file_path = claude_home.join(&path);
                if let Some(parent) = file_path.parent() {
                    let _ = fs::create_dir_all(parent);
                }

                match fs::write(&file_path, &content) {
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
            files.into_iter().map(|f| (f.path.clone(), f)).collect()
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
