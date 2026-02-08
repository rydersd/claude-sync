// ==========================================================================
// Sync protocol message types
// All messages are serialized as JSON with a 4-byte big-endian length prefix.
// These types must match the macOS SwiftUI app's protocol for interoperability.
// ==========================================================================

use serde::{Deserialize, Serialize};

/// Protocol version for compatibility checking between peers.
/// Both sides must agree on the same major version.
pub const PROTOCOL_VERSION: u32 = 1;

/// All possible messages exchanged during a sync session.
/// The `type` field discriminates the variant when serialized to JSON
/// using serde's internally-tagged representation.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type")]
pub enum SyncMessage {
    /// Initial handshake message sent by the connecting peer.
    Hello {
        device_id: String,
        name: String,
        protocol_version: u32,
        fingerprint: String,
        platform: String,
        file_count: u32,
    },

    /// Request the remote peer's file manifest.
    ManifestRequest,

    /// Response containing the peer's complete file manifest.
    Manifest { files: Vec<FileEntry> },

    /// Request to sync specific files in a given direction.
    SyncRequest {
        direction: String,
        files: Vec<String>,
    },

    /// Acknowledgment of a sync request (accept or reject).
    SyncAck {
        accepted: bool,
        reason: Option<String>,
    },

    /// Transfer a single file's content (base64-encoded).
    FileTransfer {
        path: String,
        content_base64: String,
        sha256: String,
        size: u64,
        executable: bool,
    },

    /// Acknowledgment that a file was received and written.
    FileAck {
        path: String,
        success: bool,
        error: Option<String>,
    },

    /// Signals that the sync operation is complete.
    SyncComplete {
        files_transferred: u32,
        direction: String,
    },

    /// Protocol-level error message.
    Error { code: String, message: String },
}

/// A single file entry in a config manifest.
/// Mirrors the Python tool's FileChange and the macOS app's ManifestEntry.
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
