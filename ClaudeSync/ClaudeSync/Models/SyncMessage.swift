// SyncMessage.swift
// ClaudeSync
//
// Protocol message types for peer-to-peer communication per PROTOCOL.md.
// All messages are flat JSON objects discriminated by the `type` field,
// serialized with snake_case keys on the wire.
//
// Message flow (PROTOCOL.md Section 5):
//   Client                      Server
//   ──────                      ──────
//   hello        ──────────>
//                <──────────    hello
//   [if fingerprints match: sync_not_needed, disconnect]
//   manifest_request ────────>
//                <──────────    manifest
//   sync_request  ──────────>
//                <──────────    sync_ack
//   file          ──────────>  (per file, if push)
//                <──────────    file_ack (per file)
//   sync_complete ──────────>

import Foundation

// MARK: - SyncMessage Enum

/// Top-level protocol message. Discriminated by the `type` field in flat JSON.
/// Each case encodes/decodes as a flat JSON object with `type` + payload fields.
enum SyncMessage: Codable, Sendable {
    case hello(HelloMessage)
    case syncNotNeeded(SyncNotNeededMessage)
    case manifestRequest
    case manifest(ManifestMessage)
    case syncRequest(SyncRequestMessage)
    case syncAck(SyncAckMessage)
    case file(FileMessage)
    case fileAck(FileAckMessage)
    case syncComplete(SyncCompleteMessage)
    case statusRequest
    case status(StatusMessage)
    case error(ErrorMessage)

    // MARK: - v2 Auto-Sync Messages

    /// Subscribe to real-time file change notifications from a peer.
    case subscribe(SubscribeMessage)

    /// Acknowledgment of a subscribe request.
    case subscribeAck(SubscribeAckMessage)

    /// Notification that a file has changed on the sender.
    case fileChanged(FileChangedMessage)

    /// Acknowledgment of a file_changed notification.
    case fileChangedAck(FileChangedAckMessage)

    /// Keepalive ping to maintain persistent connections.
    case keepalive(KeepaliveMessage)

    // MARK: - Type Discriminator

    /// Used to peek at the `type` field during decoding.
    private struct TypePeek: Decodable {
        let type: String
    }

    /// Used to encode messages with only a `type` field.
    private enum TypeOnlyKeys: String, CodingKey {
        case type
    }

    // MARK: - Codable

    init(from decoder: Decoder) throws {
        let peek = try TypePeek(from: decoder)

        switch peek.type {
        case "hello":
            self = .hello(try HelloMessage(from: decoder))
        case "sync_not_needed":
            self = .syncNotNeeded(try SyncNotNeededMessage(from: decoder))
        case "manifest_request":
            self = .manifestRequest
        case "manifest":
            self = .manifest(try ManifestMessage(from: decoder))
        case "sync_request":
            self = .syncRequest(try SyncRequestMessage(from: decoder))
        case "sync_ack":
            self = .syncAck(try SyncAckMessage(from: decoder))
        case "file":
            self = .file(try FileMessage(from: decoder))
        case "file_ack":
            self = .fileAck(try FileAckMessage(from: decoder))
        case "sync_complete":
            self = .syncComplete(try SyncCompleteMessage(from: decoder))
        case "status_request":
            self = .statusRequest
        case "status":
            self = .status(try StatusMessage(from: decoder))
        case "error":
            self = .error(try ErrorMessage(from: decoder))
        case "subscribe":
            self = .subscribe(try SubscribeMessage(from: decoder))
        case "subscribe_ack":
            self = .subscribeAck(try SubscribeAckMessage(from: decoder))
        case "file_changed":
            self = .fileChanged(try FileChangedMessage(from: decoder))
        case "file_changed_ack":
            self = .fileChangedAck(try FileChangedAckMessage(from: decoder))
        case "keepalive":
            self = .keepalive(try KeepaliveMessage(from: decoder))
        default:
            // Forward compatibility: unknown types per spec.
            throw DecodingError.dataCorruptedError(
                forKey: TypeOnlyKeys.type,
                in: try decoder.container(keyedBy: TypeOnlyKeys.self),
                debugDescription: "Unknown message type: \(peek.type)"
            )
        }
    }

    func encode(to encoder: Encoder) throws {
        switch self {
        case .hello(let msg):
            try msg.encode(to: encoder)
        case .syncNotNeeded(let msg):
            try msg.encode(to: encoder)
        case .manifestRequest:
            var container = encoder.container(keyedBy: TypeOnlyKeys.self)
            try container.encode("manifest_request", forKey: .type)
        case .manifest(let msg):
            try msg.encode(to: encoder)
        case .syncRequest(let msg):
            try msg.encode(to: encoder)
        case .syncAck(let msg):
            try msg.encode(to: encoder)
        case .file(let msg):
            try msg.encode(to: encoder)
        case .fileAck(let msg):
            try msg.encode(to: encoder)
        case .syncComplete(let msg):
            try msg.encode(to: encoder)
        case .statusRequest:
            var container = encoder.container(keyedBy: TypeOnlyKeys.self)
            try container.encode("status_request", forKey: .type)
        case .status(let msg):
            try msg.encode(to: encoder)
        case .error(let msg):
            try msg.encode(to: encoder)
        case .subscribe(let msg):
            try msg.encode(to: encoder)
        case .subscribeAck(let msg):
            try msg.encode(to: encoder)
        case .fileChanged(let msg):
            try msg.encode(to: encoder)
        case .fileChangedAck(let msg):
            try msg.encode(to: encoder)
        case .keepalive(let msg):
            try msg.encode(to: encoder)
        }
    }
}

// MARK: - Hello Message

/// Handshake message sent by both peers after TCP connection (PROTOCOL.md Section 4.1).
/// The client sends first; the server responds with its own hello.
struct HelloMessage: Codable, Sendable {
    let deviceId: String
    let name: String
    let protocolVersion: Int
    let fingerprint: String
    let platform: String
    let fileCount: Int

    /// Optional v2 capabilities advertised by this peer (e.g. ["auto_sync", "persistent"]).
    /// Nil for v1 peers that do not support capabilities negotiation.
    let capabilities: [String]?

    private enum CodingKeys: String, CodingKey {
        case type
        case deviceId = "device_id"
        case name
        case protocolVersion = "protocol_version"
        case fingerprint
        case platform
        case fileCount = "file_count"
        case capabilities
    }

    init(deviceId: String, name: String, protocolVersion: Int = 1,
         fingerprint: String, platform: String, fileCount: Int,
         capabilities: [String]? = nil) {
        self.deviceId = deviceId
        self.name = name
        self.protocolVersion = protocolVersion
        self.fingerprint = fingerprint
        self.platform = platform
        self.fileCount = fileCount
        self.capabilities = capabilities
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        deviceId = try container.decode(String.self, forKey: .deviceId)
        name = try container.decode(String.self, forKey: .name)
        protocolVersion = try container.decode(Int.self, forKey: .protocolVersion)
        fingerprint = try container.decode(String.self, forKey: .fingerprint)
        platform = try container.decode(String.self, forKey: .platform)
        fileCount = try container.decode(Int.self, forKey: .fileCount)
        // Backward compatible: v1 peers omit this field entirely.
        capabilities = try container.decodeIfPresent([String].self, forKey: .capabilities)
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode("hello", forKey: .type)
        try container.encode(deviceId, forKey: .deviceId)
        try container.encode(name, forKey: .name)
        try container.encode(protocolVersion, forKey: .protocolVersion)
        try container.encode(fingerprint, forKey: .fingerprint)
        try container.encode(platform, forKey: .platform)
        try container.encode(fileCount, forKey: .fileCount)
        // Only encode capabilities when present, so v1 peers never see the field.
        try container.encodeIfPresent(capabilities, forKey: .capabilities)
    }
}

// MARK: - Sync Not Needed

/// Sent when fingerprints match — configs are already in sync (PROTOCOL.md Section 4.1).
struct SyncNotNeededMessage: Codable, Sendable {
    let fingerprint: String

    private enum CodingKeys: String, CodingKey {
        case type
        case fingerprint
    }

    init(fingerprint: String) {
        self.fingerprint = fingerprint
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        fingerprint = try container.decode(String.self, forKey: .fingerprint)
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode("sync_not_needed", forKey: .type)
        try container.encode(fingerprint, forKey: .fingerprint)
    }
}

// MARK: - Manifest

/// A single entry in the file manifest (PROTOCOL.md Section 4.2).
struct ManifestFileEntry: Codable, Sendable {
    let path: String
    let sha256: String
    let size: Int
    let mtimeEpoch: Int

    private enum CodingKeys: String, CodingKey {
        case path
        case sha256
        case size
        case mtimeEpoch = "mtime_epoch"
    }
}

/// Response to a manifest_request containing the complete file list (PROTOCOL.md Section 4.2).
struct ManifestMessage: Codable, Sendable {
    let files: [ManifestFileEntry]

    private enum CodingKeys: String, CodingKey {
        case type
        case files
    }

    init(files: [ManifestFileEntry]) {
        self.files = files
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        files = try container.decode([ManifestFileEntry].self, forKey: .files)
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode("manifest", forKey: .type)
        try container.encode(files, forKey: .files)
    }
}

// MARK: - Sync Request

/// Request to begin a sync operation — push or pull (PROTOCOL.md Section 4.3).
struct SyncRequestMessage: Codable, Sendable {
    let direction: String
    let files: [String]

    private enum CodingKeys: String, CodingKey {
        case type
        case direction
        case files
    }

    init(direction: String, files: [String]) {
        self.direction = direction
        self.files = files
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        direction = try container.decode(String.self, forKey: .direction)
        files = try container.decode([String].self, forKey: .files)
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode("sync_request", forKey: .type)
        try container.encode(direction, forKey: .direction)
        try container.encode(files, forKey: .files)
    }
}

// MARK: - Sync Ack

/// Response to a sync_request (PROTOCOL.md Section 4.3).
struct SyncAckMessage: Codable, Sendable {
    let accepted: Bool
    let reason: String?

    private enum CodingKeys: String, CodingKey {
        case type
        case accepted
        case reason
    }

    init(accepted: Bool, reason: String? = nil) {
        self.accepted = accepted
        self.reason = reason
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        accepted = try container.decode(Bool.self, forKey: .accepted)
        reason = try container.decodeIfPresent(String.self, forKey: .reason)
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode("sync_ack", forKey: .type)
        try container.encode(accepted, forKey: .accepted)
        try container.encodeIfPresent(reason, forKey: .reason)
    }
}

// MARK: - File Transfer

/// Transfers a single file with base64-encoded contents (PROTOCOL.md Section 4.3).
struct FileMessage: Codable, Sendable {
    let path: String
    let contentBase64: String
    let sha256: String
    let size: Int
    let executable: Bool

    private enum CodingKeys: String, CodingKey {
        case type
        case path
        case contentBase64 = "content_base64"
        case sha256
        case size
        case executable
    }

    init(path: String, contentBase64: String, sha256: String, size: Int, executable: Bool = false) {
        self.path = path
        self.contentBase64 = contentBase64
        self.sha256 = sha256
        self.size = size
        self.executable = executable
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        path = try container.decode(String.self, forKey: .path)
        contentBase64 = try container.decode(String.self, forKey: .contentBase64)
        sha256 = try container.decode(String.self, forKey: .sha256)
        size = try container.decode(Int.self, forKey: .size)
        executable = try container.decode(Bool.self, forKey: .executable)
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode("file", forKey: .type)
        try container.encode(path, forKey: .path)
        try container.encode(contentBase64, forKey: .contentBase64)
        try container.encode(sha256, forKey: .sha256)
        try container.encode(size, forKey: .size)
        try container.encode(executable, forKey: .executable)
    }
}

// MARK: - File Ack

/// Acknowledgment for each received file (PROTOCOL.md Section 4.3).
struct FileAckMessage: Codable, Sendable {
    let path: String
    let success: Bool
    let error: String?

    private enum CodingKeys: String, CodingKey {
        case type
        case path
        case success
        case error
    }

    init(path: String, success: Bool, error: String? = nil) {
        self.path = path
        self.success = success
        self.error = error
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        path = try container.decode(String.self, forKey: .path)
        success = try container.decode(Bool.self, forKey: .success)
        error = try container.decodeIfPresent(String.self, forKey: .error)
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode("file_ack", forKey: .type)
        try container.encode(path, forKey: .path)
        try container.encode(success, forKey: .success)
        try container.encodeIfPresent(error, forKey: .error)
    }
}

// MARK: - Sync Complete

/// Sent after all files transferred (PROTOCOL.md Section 4.3).
struct SyncCompleteMessage: Codable, Sendable {
    let filesTransferred: Int
    let direction: String

    private enum CodingKeys: String, CodingKey {
        case type
        case filesTransferred = "files_transferred"
        case direction
    }

    init(filesTransferred: Int, direction: String) {
        self.filesTransferred = filesTransferred
        self.direction = direction
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        filesTransferred = try container.decode(Int.self, forKey: .filesTransferred)
        direction = try container.decode(String.self, forKey: .direction)
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode("sync_complete", forKey: .type)
        try container.encode(filesTransferred, forKey: .filesTransferred)
        try container.encode(direction, forKey: .direction)
    }
}

// MARK: - Status

/// Response to a status_request (PROTOCOL.md Section 4.4).
struct StatusMessage: Codable, Sendable {
    let deviceId: String
    let name: String
    let uptimeSeconds: Int
    let lastSyncTimestamp: Int
    let fileCount: Int
    let fingerprint: String

    private enum CodingKeys: String, CodingKey {
        case type
        case deviceId = "device_id"
        case name
        case uptimeSeconds = "uptime_seconds"
        case lastSyncTimestamp = "last_sync_timestamp"
        case fileCount = "file_count"
        case fingerprint
    }

    init(deviceId: String, name: String, uptimeSeconds: Int,
         lastSyncTimestamp: Int, fileCount: Int, fingerprint: String) {
        self.deviceId = deviceId
        self.name = name
        self.uptimeSeconds = uptimeSeconds
        self.lastSyncTimestamp = lastSyncTimestamp
        self.fileCount = fileCount
        self.fingerprint = fingerprint
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        deviceId = try container.decode(String.self, forKey: .deviceId)
        name = try container.decode(String.self, forKey: .name)
        uptimeSeconds = try container.decode(Int.self, forKey: .uptimeSeconds)
        lastSyncTimestamp = try container.decode(Int.self, forKey: .lastSyncTimestamp)
        fileCount = try container.decode(Int.self, forKey: .fileCount)
        fingerprint = try container.decode(String.self, forKey: .fingerprint)
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode("status", forKey: .type)
        try container.encode(deviceId, forKey: .deviceId)
        try container.encode(name, forKey: .name)
        try container.encode(uptimeSeconds, forKey: .uptimeSeconds)
        try container.encode(lastSyncTimestamp, forKey: .lastSyncTimestamp)
        try container.encode(fileCount, forKey: .fileCount)
        try container.encode(fingerprint, forKey: .fingerprint)
    }
}

// MARK: - Error

/// Protocol-level error message (PROTOCOL.md Section 4.5).
struct ErrorMessage: Codable, Sendable {
    let code: String
    let message: String

    private enum CodingKeys: String, CodingKey {
        case type
        case code
        case message
    }

    init(code: String, message: String) {
        self.code = code
        self.message = message
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        code = try container.decode(String.self, forKey: .code)
        message = try container.decode(String.self, forKey: .message)
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode("error", forKey: .type)
        try container.encode(code, forKey: .code)
        try container.encode(message, forKey: .message)
    }
}

// MARK: - v2 Auto-Sync Messages

// MARK: - Subscribe

/// Sent by a peer to subscribe to real-time file change notifications.
/// Paths use glob patterns; ["*"] means subscribe to all syncable files.
struct SubscribeMessage: Codable, Sendable {
    let paths: [String]

    private enum CodingKeys: String, CodingKey {
        case type
        case paths
    }

    init(paths: [String] = ["*"]) {
        self.paths = paths
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        paths = try container.decode([String].self, forKey: .paths)
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode("subscribe", forKey: .type)
        try container.encode(paths, forKey: .paths)
    }
}

// MARK: - Subscribe Ack

/// Acknowledgment of a subscribe request indicating whether the subscription was accepted.
struct SubscribeAckMessage: Codable, Sendable {
    let accepted: Bool
    let subscribedPaths: [String]

    private enum CodingKeys: String, CodingKey {
        case type
        case accepted
        case subscribedPaths = "subscribed_paths"
    }

    init(accepted: Bool, subscribedPaths: [String]) {
        self.accepted = accepted
        self.subscribedPaths = subscribedPaths
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        accepted = try container.decode(Bool.self, forKey: .accepted)
        subscribedPaths = try container.decode([String].self, forKey: .subscribedPaths)
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode("subscribe_ack", forKey: .type)
        try container.encode(accepted, forKey: .accepted)
        try container.encode(subscribedPaths, forKey: .subscribedPaths)
    }
}

// MARK: - File Changed

/// The type of change that occurred on a file.
enum FileChangeType: String, Codable, Sendable {
    case modified
    case created
    case deleted
}

/// Notification that a file has changed on the sender.
/// For deleted files, contentBase64, sha256, and size are nil.
/// For files >1MB, contentBase64 is nil and the receiver should pull via sync_request.
struct FileChangedMessage: Codable, Sendable {
    let path: String
    let change: FileChangeType
    let sha256: String?
    let size: Int?
    let mtimeEpoch: Int?
    let changeEpochMs: Int64
    let previousSha256: String?
    let contentBase64: String?
    let executable: Bool

    private enum CodingKeys: String, CodingKey {
        case type
        case path
        case change
        case sha256
        case size
        case mtimeEpoch = "mtime_epoch"
        case changeEpochMs = "change_epoch_ms"
        case previousSha256 = "previous_sha256"
        case contentBase64 = "content_base64"
        case executable
    }

    init(path: String, change: FileChangeType, sha256: String?, size: Int?,
         mtimeEpoch: Int?, changeEpochMs: Int64, previousSha256: String?,
         contentBase64: String?, executable: Bool = false) {
        self.path = path
        self.change = change
        self.sha256 = sha256
        self.size = size
        self.mtimeEpoch = mtimeEpoch
        self.changeEpochMs = changeEpochMs
        self.previousSha256 = previousSha256
        self.contentBase64 = contentBase64
        self.executable = executable
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        path = try container.decode(String.self, forKey: .path)
        change = try container.decode(FileChangeType.self, forKey: .change)
        sha256 = try container.decodeIfPresent(String.self, forKey: .sha256)
        size = try container.decodeIfPresent(Int.self, forKey: .size)
        mtimeEpoch = try container.decodeIfPresent(Int.self, forKey: .mtimeEpoch)
        changeEpochMs = try container.decode(Int64.self, forKey: .changeEpochMs)
        previousSha256 = try container.decodeIfPresent(String.self, forKey: .previousSha256)
        contentBase64 = try container.decodeIfPresent(String.self, forKey: .contentBase64)
        executable = try container.decodeIfPresent(Bool.self, forKey: .executable) ?? false
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode("file_changed", forKey: .type)
        try container.encode(path, forKey: .path)
        try container.encode(change, forKey: .change)
        try container.encodeIfPresent(sha256, forKey: .sha256)
        try container.encodeIfPresent(size, forKey: .size)
        try container.encodeIfPresent(mtimeEpoch, forKey: .mtimeEpoch)
        try container.encode(changeEpochMs, forKey: .changeEpochMs)
        try container.encodeIfPresent(previousSha256, forKey: .previousSha256)
        try container.encodeIfPresent(contentBase64, forKey: .contentBase64)
        try container.encode(executable, forKey: .executable)
    }
}

// MARK: - File Changed Ack

/// Acknowledgment of a file_changed notification, indicating whether the change was applied.
struct FileChangedAckMessage: Codable, Sendable {
    let path: String
    let accepted: Bool
    let conflict: Bool

    private enum CodingKeys: String, CodingKey {
        case type
        case path
        case accepted
        case conflict
    }

    init(path: String, accepted: Bool, conflict: Bool = false) {
        self.path = path
        self.accepted = accepted
        self.conflict = conflict
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        path = try container.decode(String.self, forKey: .path)
        accepted = try container.decode(Bool.self, forKey: .accepted)
        conflict = try container.decodeIfPresent(Bool.self, forKey: .conflict) ?? false
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode("file_changed_ack", forKey: .type)
        try container.encode(path, forKey: .path)
        try container.encode(accepted, forKey: .accepted)
        try container.encode(conflict, forKey: .conflict)
    }
}

// MARK: - Keepalive

/// Periodic keepalive message to maintain persistent connections.
/// Sent every 15 seconds; if no message received within 45 seconds,
/// the peer is considered dead.
struct KeepaliveMessage: Codable, Sendable {
    let timestamp: Int

    private enum CodingKeys: String, CodingKey {
        case type
        case timestamp
    }

    init(timestamp: Int = Int(Date().timeIntervalSince1970)) {
        self.timestamp = timestamp
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        timestamp = try container.decode(Int.self, forKey: .timestamp)
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode("keepalive", forKey: .type)
        try container.encode(timestamp, forKey: .timestamp)
    }
}
