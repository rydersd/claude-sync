// ==========================================================================
// Sync protocol message types
// All messages are serialized as JSON with a 4-byte big-endian length prefix.
// These types must match PROTOCOL.md for interoperability with the macOS app.
// Uses serde internally-tagged enum with snake_case type discriminators.
// ==========================================================================

use serde::{Deserialize, Serialize};

/// Protocol version for compatibility checking between peers.
/// Both sides must agree on the same major version.
pub const PROTOCOL_VERSION: u32 = 1;

/// All possible messages exchanged during a sync session.
/// The `type` field discriminates the variant when serialized to JSON
/// using serde's internally-tagged representation.
/// Each variant is renamed to its snake_case wire format per PROTOCOL.md.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type")]
pub enum SyncMessage {
    /// Initial handshake message sent by both sides (client first, server responds).
    /// The optional `capabilities` field advertises v2 features (e.g., "file_watch", "keepalive").
    #[serde(rename = "hello")]
    Hello {
        device_id: String,
        name: String,
        protocol_version: u32,
        fingerprint: String,
        platform: String,
        file_count: u32,
        #[serde(skip_serializing_if = "Option::is_none")]
        capabilities: Option<Vec<String>>,
    },

    /// Sent after handshake when fingerprints match — configs are already in sync.
    #[serde(rename = "sync_not_needed")]
    SyncNotNeeded { fingerprint: String },

    /// Request the remote peer's file manifest.
    #[serde(rename = "manifest_request")]
    ManifestRequest,

    /// Response containing the peer's complete file manifest.
    #[serde(rename = "manifest")]
    Manifest { files: Vec<ManifestFileEntry> },

    /// Request to sync specific files in a given direction.
    #[serde(rename = "sync_request")]
    SyncRequest {
        direction: String,
        files: Vec<String>,
    },

    /// Acknowledgment of a sync request (accept or reject).
    #[serde(rename = "sync_ack")]
    SyncAck {
        accepted: bool,
        #[serde(skip_serializing_if = "Option::is_none")]
        reason: Option<String>,
    },

    /// Transfer a single file's content (base64-encoded).
    #[serde(rename = "file")]
    File {
        path: String,
        content_base64: String,
        sha256: String,
        size: u64,
        executable: bool,
    },

    /// Acknowledgment that a file was received and written.
    #[serde(rename = "file_ack")]
    FileAck {
        path: String,
        success: bool,
        #[serde(skip_serializing_if = "Option::is_none")]
        error: Option<String>,
    },

    /// Signals that the sync operation is complete.
    #[serde(rename = "sync_complete")]
    SyncComplete {
        files_transferred: u32,
        direction: String,
    },

    /// Request the current status of a peer.
    #[serde(rename = "status_request")]
    StatusRequest,

    /// Response to a status_request with device state.
    #[serde(rename = "status")]
    Status {
        device_id: String,
        name: String,
        uptime_seconds: u64,
        last_sync_timestamp: u64,
        file_count: u32,
        fingerprint: String,
    },

    /// Protocol-level error message.
    #[serde(rename = "error")]
    Error { code: String, message: String },

    // -- v2 message types (additive, backwards-compatible) --------------------

    /// Opt-in to real-time file change notifications for specific paths.
    /// Sent after a successful Hello exchange to enable live auto-sync.
    #[serde(rename = "subscribe")]
    Subscribe {
        paths: Vec<String>,
    },

    /// Acknowledgment of a subscription request.
    /// Returns the subset of paths the peer accepted for monitoring.
    #[serde(rename = "subscribe_ack")]
    SubscribeAck {
        accepted: bool,
        subscribed_paths: Vec<String>,
    },

    /// Push a changed file to subscribed peers (real-time notification).
    /// Contains the file metadata and optionally the content itself.
    /// `previous_sha256` enables conflict detection on the receiver.
    #[serde(rename = "file_changed")]
    FileChanged {
        path: String,
        /// One of: "modified", "created", "deleted"
        change: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        sha256: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        size: Option<u64>,
        mtime_epoch: i64,
        change_epoch_ms: i64,
        #[serde(skip_serializing_if = "Option::is_none")]
        previous_sha256: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        content_base64: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        executable: Option<bool>,
    },

    /// Acknowledge receipt of a file change notification.
    /// `conflict` is true if the receiver detected a hash mismatch
    /// and resolved the conflict locally.
    #[serde(rename = "file_changed_ack")]
    FileChangedAck {
        path: String,
        accepted: bool,
        conflict: bool,
    },

    /// Keep a persistent connection alive. Sent every 15 seconds.
    /// Peers that do not receive a keepalive within 45 seconds
    /// should consider the connection dead and reconnect.
    #[serde(rename = "keepalive")]
    Keepalive {
        timestamp: i64,
    },
}

/// A single file entry in a manifest message per PROTOCOL.md Section 4.2.
/// Used on the wire in the `manifest` message type.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ManifestFileEntry {
    /// Path relative to ~/.claude/ (e.g., "rules/git-commits.md")
    pub path: String,
    /// SHA-256 hex digest of the file content (64 characters)
    pub sha256: String,
    /// File size in bytes
    pub size: u64,
    /// Last modification time as Unix epoch seconds (UTC)
    pub mtime_epoch: i64,
}

/// Internal file entry used for scanning and diff operations.
/// Separate from ManifestFileEntry since internal operations
/// need the executable bit but not mtime_epoch on the wire.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FileEntry {
    /// Path relative to ~/.claude/ (e.g., "rules/git-commits.md")
    pub path: String,
    /// SHA-256 hex digest of the file content
    pub sha256: String,
    /// File size in bytes
    pub size: u64,
    /// Whether the file has the executable bit set
    pub executable: bool,
    /// Last modification time as Unix epoch seconds (UTC)
    pub mtime_epoch: i64,
}

/// Represents a single file difference between local and remote.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FileDiff {
    pub path: String,
    pub change_type: String,
    pub local_hash: Option<String>,
    pub remote_hash: Option<String>,
    pub local_size: Option<u64>,
    pub remote_size: Option<u64>,
}

/// Result of comparing local config tree with a remote peer.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DiffResult {
    pub added: Vec<FileDiff>,
    pub modified: Vec<FileDiff>,
    pub deleted: Vec<FileDiff>,
    pub total_changes: usize,
}

/// Result of a sync operation.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SyncResult {
    pub success: bool,
    pub files_transferred: u32,
    pub direction: String,
    pub error: Option<String>,
}

/// Information about a discovered peer on the LAN.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PeerInfo {
    pub device_id: String,
    pub name: String,
    pub address: String,
    pub platform: String,
    pub file_count: u32,
    pub fingerprint: String,
    pub protocol_version: u32,
}

/// Information about this device, sent to the frontend.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DeviceInfo {
    pub device_id: String,
    pub name: String,
    pub platform: String,
    pub file_count: u32,
    pub fingerprint: String,
}

/// Config tree with file entries and metadata.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ConfigTree {
    pub files: Vec<FileEntry>,
    pub fingerprint: String,
    pub file_count: u32,
}
