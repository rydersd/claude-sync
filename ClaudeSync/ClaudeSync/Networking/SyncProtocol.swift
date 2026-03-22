// SyncProtocol.swift
// ClaudeSync
//
// JSON encoding/decoding utilities for SyncMessage.
// Provides a single point for serialization configuration
// and factory methods for creating protocol messages.

import Foundation

/// Handles encoding and decoding of SyncMessage instances to/from JSON Data.
/// All message serialization goes through this enum to ensure consistent configuration.
enum SyncProtocolCoder {

    // MARK: - Shared Encoder/Decoder Configuration

    /// JSON encoder configured for the ClaudeSync protocol.
    /// Keys are handled by each message type's CodingKeys (snake_case raw values).
    static let encoder: JSONEncoder = {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        return encoder
    }()

    /// JSON decoder configured for the ClaudeSync protocol.
    static let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        return decoder
    }()

    // MARK: - Encode

    /// Encodes a SyncMessage to JSON Data for transmission.
    static func encode(_ message: SyncMessage) throws -> Data {
        return try encoder.encode(message)
    }

    // MARK: - Decode

    /// Decodes JSON Data into a SyncMessage.
    static func decode(_ data: Data) throws -> SyncMessage {
        return try decoder.decode(SyncMessage.self, from: data)
    }

    // MARK: - Factory Methods

    /// Creates a hello message from local device identity and config state.
    /// - Parameter capabilities: Optional v2 capabilities (e.g. ["auto_sync", "persistent"]).
    ///   Pass nil for v1-only hello messages (backward compatible).
    static func makeHello(
        deviceId: String,
        deviceName: String,
        fileCount: Int,
        fingerprint: String,
        capabilities: [String]? = nil
    ) -> SyncMessage {
        let msg = HelloMessage(
            deviceId: deviceId,
            name: deviceName,
            protocolVersion: 1,
            fingerprint: fingerprint,
            platform: DeviceIdentity.platform,
            fileCount: fileCount,
            capabilities: capabilities
        )
        return .hello(msg)
    }

    /// Creates a sync_not_needed message.
    static func makeSyncNotNeeded(fingerprint: String) -> SyncMessage {
        return .syncNotNeeded(SyncNotNeededMessage(fingerprint: fingerprint))
    }

    /// Creates a manifest_request message.
    static func makeManifestRequest() -> SyncMessage {
        return .manifestRequest
    }

    /// Creates a manifest message from file entries.
    static func makeManifest(files: [ManifestFileEntry]) -> SyncMessage {
        return .manifest(ManifestMessage(files: files))
    }

    /// Creates a sync_request message.
    static func makeSyncRequest(direction: String, files: [String]) -> SyncMessage {
        return .syncRequest(SyncRequestMessage(direction: direction, files: files))
    }

    /// Creates a sync_ack message.
    static func makeSyncAck(accepted: Bool, reason: String? = nil) -> SyncMessage {
        return .syncAck(SyncAckMessage(accepted: accepted, reason: reason))
    }

    /// Creates a file message from a path and data.
    static func makeFile(path: String, data: Data, sha256: String, executable: Bool = false) -> SyncMessage {
        let msg = FileMessage(
            path: path,
            contentBase64: data.base64EncodedString(),
            sha256: sha256,
            size: data.count,
            executable: executable
        )
        return .file(msg)
    }

    /// Creates a file_ack message.
    static func makeFileAck(path: String, success: Bool, error: String? = nil) -> SyncMessage {
        return .fileAck(FileAckMessage(path: path, success: success, error: error))
    }

    /// Creates a sync_complete message.
    static func makeSyncComplete(filesTransferred: Int, direction: String) -> SyncMessage {
        return .syncComplete(SyncCompleteMessage(filesTransferred: filesTransferred, direction: direction))
    }

    /// Creates an error message.
    static func makeError(code: String, message: String) -> SyncMessage {
        return .error(ErrorMessage(code: code, message: message))
    }

    /// Creates a status message.
    static func makeStatus(
        deviceId: String,
        name: String,
        uptimeSeconds: Int,
        lastSyncTimestamp: Int,
        fileCount: Int,
        fingerprint: String
    ) -> SyncMessage {
        let msg = StatusMessage(
            deviceId: deviceId,
            name: name,
            uptimeSeconds: uptimeSeconds,
            lastSyncTimestamp: lastSyncTimestamp,
            fileCount: fileCount,
            fingerprint: fingerprint
        )
        return .status(msg)
    }

    // MARK: - v2 Auto-Sync Factory Methods

    /// Creates a subscribe message to request real-time file change notifications.
    static func makeSubscribe(paths: [String] = ["*"]) -> SyncMessage {
        return .subscribe(SubscribeMessage(paths: paths))
    }

    /// Creates a subscribe_ack message.
    static func makeSubscribeAck(accepted: Bool, subscribedPaths: [String]) -> SyncMessage {
        return .subscribeAck(SubscribeAckMessage(accepted: accepted, subscribedPaths: subscribedPaths))
    }

    /// Creates a file_changed message for a modified or created file.
    /// For files >1MB, pass nil for contentBase64 so the receiver pulls via sync_request.
    static func makeFileChanged(
        path: String,
        change: FileChangeType,
        sha256: String?,
        size: Int?,
        mtimeEpoch: Int?,
        changeEpochMs: Int64,
        previousSha256: String?,
        contentBase64: String?,
        executable: Bool = false
    ) -> SyncMessage {
        let msg = FileChangedMessage(
            path: path,
            change: change,
            sha256: sha256,
            size: size,
            mtimeEpoch: mtimeEpoch,
            changeEpochMs: changeEpochMs,
            previousSha256: previousSha256,
            contentBase64: contentBase64,
            executable: executable
        )
        return .fileChanged(msg)
    }

    /// Creates a file_changed_ack message.
    static func makeFileChangedAck(path: String, accepted: Bool, conflict: Bool = false) -> SyncMessage {
        return .fileChangedAck(FileChangedAckMessage(path: path, accepted: accepted, conflict: conflict))
    }

    /// Creates a keepalive message with the current timestamp.
    static func makeKeepalive() -> SyncMessage {
        return .keepalive(KeepaliveMessage())
    }
}
