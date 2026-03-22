// RelayConnection.swift
// ClaudeSync
//
// Wraps a tracker relay channel as a SyncMessage send/receive interface.
// All sync protocol messages are serialized to JSON, base64-encoded,
// and transported as relay_data payloads through the tracker.
//
// This allows WAN peers to exchange SyncMessages transparently -- the
// higher layers (SyncEngine, auto-sync) don't need to know whether the
// transport is a direct TCP connection or a relay.

import Foundation
import os

/// Wraps a tracker relay channel to provide SyncMessage send/receive.
/// Actor-isolated for thread safety. Messages arrive via the TrackerClient's
/// onRelayData callback and are buffered for consumption by receive().
actor RelayConnection {

    // MARK: - Properties

    /// The tracker client used to send relay data.
    private let trackerClient: TrackerClient

    /// The relay channel ID assigned by the tracker.
    let relayId: String

    /// The remote peer's device ID.
    let remoteDeviceId: String

    /// Buffer of received SyncMessages waiting to be consumed.
    private var messageBuffer: [SyncMessage] = []

    /// Continuations waiting for the next message (FIFO).
    private var waitingReceivers: [CheckedContinuation<SyncMessage, Error>] = []

    /// Whether this relay connection has been closed.
    private var isClosed: Bool = false

    /// Logger for relay connection events.
    private let logger = Logger(subsystem: "com.claudesync", category: "RelayConnection")

    // MARK: - Initialization

    /// Creates a relay connection for exchanging SyncMessages via the tracker.
    /// - Parameters:
    ///   - trackerClient: The tracker client to send relay data through.
    ///   - relayId: The relay channel ID from the tracker.
    ///   - remoteDeviceId: The remote peer's device ID.
    init(trackerClient: TrackerClient, relayId: String, remoteDeviceId: String) {
        self.trackerClient = trackerClient
        self.relayId = relayId
        self.remoteDeviceId = remoteDeviceId
    }

    // MARK: - Sending

    /// Sends a SyncMessage through the relay.
    /// The message is JSON-encoded, then base64-encoded, then sent as relay_data.
    func send(_ message: SyncMessage) async throws {
        guard !isClosed else {
            throw RelayConnectionError.connectionClosed
        }

        // Encode the SyncMessage to JSON.
        let jsonData = try SyncProtocolCoder.encode(message)

        // Base64-encode the JSON for transport.
        let payloadBase64 = jsonData.base64EncodedString()

        // Send through the tracker relay.
        try await trackerClient.sendRelayData(
            relayId: relayId,
            targetDeviceId: remoteDeviceId,
            payloadBase64: payloadBase64
        )
    }

    // MARK: - Receiving

    /// Receives the next SyncMessage from the relay.
    /// Blocks until a message arrives or the connection is closed.
    func receive() async throws -> SyncMessage {
        guard !isClosed else {
            throw RelayConnectionError.connectionClosed
        }

        // If there's a buffered message, return it immediately.
        if !messageBuffer.isEmpty {
            return messageBuffer.removeFirst()
        }

        // Otherwise, suspend until a message arrives.
        return try await withCheckedThrowingContinuation { continuation in
            if isClosed {
                continuation.resume(throwing: RelayConnectionError.connectionClosed)
            } else {
                waitingReceivers.append(continuation)
            }
        }
    }

    // MARK: - Incoming Data

    /// Called by the NetworkManager when relay data arrives for this connection.
    /// Decodes the base64 payload into a SyncMessage and either delivers it to
    /// a waiting receiver or buffers it.
    func handleIncomingRelayData(payloadBase64: String) {
        guard !isClosed else { return }

        // Decode the base64 payload.
        guard let jsonData = Data(base64Encoded: payloadBase64) else {
            logger.error("Invalid base64 in relay data from \(self.remoteDeviceId.prefix(8))")
            return
        }

        // Decode the SyncMessage.
        let message: SyncMessage
        do {
            message = try SyncProtocolCoder.decode(jsonData)
        } catch {
            logger.error("Failed to decode relay SyncMessage: \(error.localizedDescription)")
            return
        }

        // If a receiver is waiting, deliver directly. Otherwise, buffer.
        if !waitingReceivers.isEmpty {
            let continuation = waitingReceivers.removeFirst()
            continuation.resume(returning: message)
        } else {
            messageBuffer.append(message)
        }
    }

    // MARK: - Lifecycle

    /// Closes the relay connection and cancels any waiting receivers.
    func close() {
        guard !isClosed else { return }
        isClosed = true

        // Cancel all waiting receivers.
        for continuation in waitingReceivers {
            continuation.resume(throwing: RelayConnectionError.connectionClosed)
        }
        waitingReceivers.removeAll()
        messageBuffer.removeAll()

        logger.info("Relay connection closed (relay: \(self.relayId.prefix(8)), peer: \(self.remoteDeviceId.prefix(8)))")
    }

    /// Whether the relay connection is still open.
    var isOpen: Bool {
        return !isClosed
    }
}

// MARK: - Error Types

/// Errors specific to relay connection operations.
enum RelayConnectionError: LocalizedError {
    case connectionClosed
    case decodingFailed(Error)
    case encodingFailed(Error)

    var errorDescription: String? {
        switch self {
        case .connectionClosed:
            return "Relay connection has been closed"
        case .decodingFailed(let error):
            return "Failed to decode relay message: \(error.localizedDescription)"
        case .encodingFailed(let error):
            return "Failed to encode relay message: \(error.localizedDescription)"
        }
    }
}
