// SyncMessage.swift
// ClaudeSync
//
// Protocol message types for peer-to-peer communication.
// All messages are Codable for JSON serialization and sent
// via the length-prefixed framing protocol (MessageFramer).
//
// Message flow:
//   Initiator                 Responder
//   ─────────                 ─────────
//   hello        ──────────>
//                <──────────  hello
//   manifestRequest ───────>
//                <──────────  manifest
//   syncRequest  ──────────>
//                <──────────  syncAck
//   fileTransfer ──────────>  (repeated per file)
//                <──────────  fileAck (per file)
//   syncComplete ──────────>

import Foundation

/// Top-level message envelope. Discriminated by the `type` field when encoded.
enum SyncMessage: Codable, Sendable {
    case hello(HelloPayload)
    case manifestRequest(ManifestRequestPayload)
    case manifest(ManifestPayload)
    case syncRequest(SyncRequestPayload)
    case syncAck(SyncAckPayload)
    case fileTransfer(FileTransferPayload)
    case fileAck(FileAckPayload)
    case syncComplete(SyncCompletePayload)
    case error(ErrorPayload)

    // MARK: - Coding Keys

    private enum CodingKeys: String, CodingKey {
        case type
        case payload
    }

    private enum MessageType: String, Codable {
        case hello
        case manifestRequest
        case manifest
        case syncRequest
        case syncAck
        case fileTransfer
        case fileAck
        case syncComplete
        case error
    }

    // MARK: - Codable

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let type = try container.decode(MessageType.self, forKey: .type)

        switch type {
        case .hello:
            let payload = try container.decode(HelloPayload.self, forKey: .payload)
            self = .hello(payload)
        case .manifestRequest:
            let payload = try container.decode(ManifestRequestPayload.self, forKey: .payload)
            self = .manifestRequest(payload)
        case .manifest:
            let payload = try container.decode(ManifestPayload.self, forKey: .payload)
            self = .manifest(payload)
        case .syncRequest:
            let payload = try container.decode(SyncRequestPayload.self, forKey: .payload)
            self = .syncRequest(payload)
        case .syncAck:
            let payload = try container.decode(SyncAckPayload.self, forKey: .payload)
            self = .syncAck(payload)
        case .fileTransfer:
            let payload = try container.decode(FileTransferPayload.self, forKey: .payload)
            self = .fileTransfer(payload)
        case .fileAck:
            let payload = try container.decode(FileAckPayload.self, forKey: .payload)
            self = .fileAck(payload)
        case .syncComplete:
            let payload = try container.decode(SyncCompletePayload.self, forKey: .payload)
            self = .syncComplete(payload)
        case .error:
            let payload = try container.decode(ErrorPayload.self, forKey: .payload)
            self = .error(payload)
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)

        switch self {
        case .hello(let payload):
            try container.encode(MessageType.hello, forKey: .type)
            try container.encode(payload, forKey: .payload)
        case .manifestRequest(let payload):
            try container.encode(MessageType.manifestRequest, forKey: .type)
            try container.encode(payload, forKey: .payload)
        case .manifest(let payload):
            try container.encode(MessageType.manifest, forKey: .type)
            try container.encode(payload, forKey: .payload)
        case .syncRequest(let payload):
            try container.encode(MessageType.syncRequest, forKey: .type)
            try container.encode(payload, forKey: .payload)
        case .syncAck(let payload):
            try container.encode(MessageType.syncAck, forKey: .type)
            try container.encode(payload, forKey: .payload)
        case .fileTransfer(let payload):
            try container.encode(MessageType.fileTransfer, forKey: .type)
            try container.encode(payload, forKey: .payload)
        case .fileAck(let payload):
            try container.encode(MessageType.fileAck, forKey: .type)
            try container.encode(payload, forKey: .payload)
        case .syncComplete(let payload):
            try container.encode(MessageType.syncComplete, forKey: .type)
            try container.encode(payload, forKey: .payload)
        case .error(let payload):
            try container.encode(MessageType.error, forKey: .type)
            try container.encode(payload, forKey: .payload)
        }
    }
}

// MARK: - Payload Types

/// Initial handshake. Both peers send this after connection establishment.
/// Contains identity info and a quick fingerprint for fast "in sync?" check.
struct HelloPayload: Codable, Sendable {
    /// Persistent device UUID from UserDefaults.
    let deviceId: String

    /// Human-readable device name (usually the hostname).
    let deviceName: String

    /// Platform identifier (e.g. "macOS").
    let platform: String

    /// Protocol version for compatibility. Currently "1".
    let protocolVersion: String

    /// Number of syncable config files on this device.
    let configCount: Int

    /// Hash of all file hashes, for quick equality comparison.
    let fingerprint: String
}

/// Request the peer's full file manifest (path -> hash mapping).
struct ManifestRequestPayload: Codable, Sendable {
    /// Timestamp of the request for correlation.
    let requestId: String
}

/// The peer's complete file manifest.
struct ManifestPayload: Codable, Sendable {
    /// Correlation ID matching the ManifestRequestPayload.
    let requestId: String

    /// Dictionary of relative_path -> SHA-256 hash for all syncable files.
    let files: [String: String]

    /// When this manifest was computed.
    let timestamp: String
}

/// Request to sync specific files from the peer.
struct SyncRequestPayload: Codable, Sendable {
    /// Unique identifier for this sync session.
    let syncId: String

    /// Direction of sync from the requester's perspective.
    /// "pull" means the requester wants files FROM the peer.
    /// "push" means the requester wants to send files TO the peer.
    let direction: String

    /// List of relative paths to transfer.
    let files: [String]
}

/// Acknowledgment of a sync request, indicating readiness to proceed.
struct SyncAckPayload: Codable, Sendable {
    /// Matching sync ID from the request.
    let syncId: String

    /// Whether the peer accepted the sync request.
    let accepted: Bool

    /// Optional reason if not accepted.
    let reason: String?
}

/// A single file being transferred. Contents are base64-encoded.
struct FileTransferPayload: Codable, Sendable {
    /// Matching sync ID.
    let syncId: String

    /// Relative path of the file (e.g. "rules/my-rule.md").
    let relativePath: String

    /// Base64-encoded file contents.
    let contentBase64: String

    /// SHA-256 hash of the original (non-encoded) content for verification.
    let hash: String

    /// 1-based index of this file in the transfer sequence.
    let index: Int

    /// Total number of files in this transfer.
    let totalFiles: Int
}

/// Acknowledgment of receiving a single file.
struct FileAckPayload: Codable, Sendable {
    /// Matching sync ID.
    let syncId: String

    /// Relative path of the acknowledged file.
    let relativePath: String

    /// Whether the file was written successfully.
    let success: Bool

    /// Error description if write failed.
    let error: String?
}

/// Signals that all files in a sync session have been transferred.
struct SyncCompletePayload: Codable, Sendable {
    /// Matching sync ID.
    let syncId: String

    /// Total number of files transferred.
    let filesTransferred: Int

    /// Whether the overall sync was successful.
    let success: Bool

    /// Summary message.
    let message: String
}

/// Error message for protocol-level errors.
struct ErrorPayload: Codable, Sendable {
    /// Error code for programmatic handling.
    let code: String

    /// Human-readable error description.
    let message: String

    /// Optional context about what operation failed.
    let context: String?
}
