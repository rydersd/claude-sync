// SyncProtocol.swift
// ClaudeSync
//
// JSON encoding/decoding utilities for SyncMessage.
// Provides a single point for serialization configuration
// (date formatting, key strategy, etc.) used by SyncConnection.

import Foundation

/// Handles encoding and decoding of SyncMessage instances to/from JSON Data.
/// All message serialization goes through this enum to ensure consistent configuration.
enum SyncProtocolCoder {

    // MARK: - Shared Encoder/Decoder Configuration

    /// JSON encoder configured for the ClaudeSync protocol.
    /// Uses camelCase keys and ISO 8601 dates.
    static let encoder: JSONEncoder = {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        encoder.outputFormatting = [.sortedKeys]
        return encoder
    }()

    /// JSON decoder configured for the ClaudeSync protocol.
    static let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return decoder
    }()

    // MARK: - Encode

    /// Encodes a SyncMessage to JSON Data for transmission.
    /// - Parameter message: The message to encode.
    /// - Returns: JSON-encoded Data.
    /// - Throws: EncodingError if the message cannot be serialized.
    static func encode(_ message: SyncMessage) throws -> Data {
        return try encoder.encode(message)
    }

    // MARK: - Decode

    /// Decodes JSON Data into a SyncMessage.
    /// - Parameter data: The raw JSON data received from the network.
    /// - Returns: The decoded SyncMessage.
    /// - Throws: DecodingError if the data is not valid JSON or does not match any known message type.
    static func decode(_ data: Data) throws -> SyncMessage {
        return try decoder.decode(SyncMessage.self, from: data)
    }

    // MARK: - Helpers

    /// Generates a unique request/sync ID for correlation.
    /// Uses UUID for uniqueness within a session.
    static func generateId() -> String {
        UUID().uuidString.lowercased()
    }

    /// Generates an ISO 8601 timestamp string for the current moment.
    static func currentTimestamp() -> String {
        ISO8601DateFormatter().string(from: Date())
    }

    /// Creates a HelloPayload from the local device identity and config state.
    static func makeHello(
        deviceId: String,
        deviceName: String,
        configCount: Int,
        fingerprint: String
    ) -> SyncMessage {
        let payload = HelloPayload(
            deviceId: deviceId,
            deviceName: deviceName,
            platform: "macOS",
            protocolVersion: "1",
            configCount: configCount,
            fingerprint: fingerprint
        )
        return .hello(payload)
    }

    /// Creates a ManifestRequestPayload.
    static func makeManifestRequest() -> SyncMessage {
        let payload = ManifestRequestPayload(requestId: generateId())
        return .manifestRequest(payload)
    }

    /// Creates a ManifestPayload from a file hash dictionary.
    static func makeManifest(requestId: String, files: [String: String]) -> SyncMessage {
        let payload = ManifestPayload(
            requestId: requestId,
            files: files,
            timestamp: currentTimestamp()
        )
        return .manifest(payload)
    }

    /// Creates an ErrorPayload message.
    static func makeError(code: String, message: String, context: String? = nil) -> SyncMessage {
        let payload = ErrorPayload(code: code, message: message, context: context)
        return .error(payload)
    }
}
