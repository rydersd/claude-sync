// SyncConnection.swift
// ClaudeSync
//
// Manages a single NWConnection to a peer. Handles sending and receiving
// framed SyncMessage instances over the connection. Provides async/await
// APIs for message exchange and tracks connection lifecycle state.

import Foundation
import Network
import os

/// Manages a single peer-to-peer connection for sync message exchange.
/// Each SyncConnection wraps an NWConnection and provides typed send/receive.
final class SyncConnection: @unchecked Sendable {

    // MARK: - Properties

    /// The underlying Network.framework connection.
    let connection: NWConnection

    /// Identifier for logging and correlation.
    let id: String

    /// Logger for this connection's lifecycle and message events.
    private let logger = Logger(subsystem: "com.claudesync", category: "SyncConnection")

    /// Callback invoked when the connection state changes.
    /// Called on the connection's queue.
    var onStateChange: ((NWConnection.State) -> Void)?

    /// Callback invoked when a complete message is received.
    /// Called on the connection's queue.
    var onMessage: ((SyncMessage) -> Void)?

    /// Callback invoked when the connection encounters an error.
    var onError: ((Error) -> Void)?

    /// The dispatch queue for connection events.
    private let queue: DispatchQueue

    /// Whether the receive loop is currently active.
    private var isReceiving = false

    // MARK: - Initialization

    /// Creates a SyncConnection wrapping an existing NWConnection.
    /// Used for incoming connections from the listener.
    init(connection: NWConnection, id: String = UUID().uuidString) {
        self.connection = connection
        self.id = id
        self.queue = DispatchQueue(label: "com.claudesync.connection.\(id)")
    }

    /// Creates a SyncConnection by initiating a new outbound connection to a peer endpoint.
    init(to endpoint: NWEndpoint, id: String = UUID().uuidString) {
        self.id = id
        self.queue = DispatchQueue(label: "com.claudesync.connection.\(id)")
        self.connection = NWConnection(to: endpoint, using: .claudeSync)
    }

    // MARK: - Lifecycle

    /// Starts the connection and begins monitoring state changes.
    /// Also starts the receive loop to handle incoming messages.
    func start() {
        connection.stateUpdateHandler = { [weak self] state in
            guard let self = self else { return }
            self.logger.debug("Connection \(self.id) state: \(String(describing: state))")
            self.onStateChange?(state)

            switch state {
            case .ready:
                // Connection is established; start receiving messages.
                self.startReceiving()
            case .failed(let error):
                self.logger.error("Connection \(self.id) failed: \(error.localizedDescription)")
                self.onError?(error)
            case .cancelled:
                self.logger.info("Connection \(self.id) cancelled")
            default:
                break
            }
        }

        connection.start(queue: queue)
    }

    /// Cancels the connection. Safe to call multiple times.
    func cancel() {
        connection.cancel()
    }

    // MARK: - Sending

    /// Sends a SyncMessage over the connection.
    /// - Parameter message: The message to send.
    /// - Throws: If encoding fails or the send encounters an error.
    func send(_ message: SyncMessage) async throws {
        let data = try SyncProtocolCoder.encode(message)

        return try await withCheckedThrowingContinuation { continuation in
            // Create a framer message to go through the ClaudeSyncProtocol framer.
            let framerMessage = NWProtocolFramer.Message(claudeSyncMessage: message)

            // Create a context that references the framer protocol so the
            // framing layer adds the length prefix automatically.
            let context = NWConnection.ContentContext(
                identifier: "SyncMessage",
                metadata: [framerMessage]
            )

            connection.send(
                content: data,
                contentContext: context,
                isComplete: true,
                completion: .contentProcessed { error in
                    if let error = error {
                        continuation.resume(throwing: error)
                    } else {
                        continuation.resume()
                    }
                }
            )
        }
    }

    /// Sends a SyncMessage using a completion handler instead of async/await.
    func send(_ message: SyncMessage, completion: @escaping (Error?) -> Void) {
        do {
            let data = try SyncProtocolCoder.encode(message)
            let framerMessage = NWProtocolFramer.Message(claudeSyncMessage: message)
            let context = NWConnection.ContentContext(
                identifier: "SyncMessage",
                metadata: [framerMessage]
            )

            connection.send(
                content: data,
                contentContext: context,
                isComplete: true,
                completion: .contentProcessed { error in
                    completion(error)
                }
            )
        } catch {
            completion(error)
        }
    }

    // MARK: - Receiving

    /// Waits for and returns the next SyncMessage from the connection.
    /// - Returns: The received SyncMessage.
    /// - Throws: If the connection is closed or the message cannot be decoded.
    func receiveMessage() async throws -> SyncMessage {
        return try await withCheckedThrowingContinuation { continuation in
            connection.receiveMessage { [weak self] content, context, isComplete, error in
                guard let self = self else {
                    continuation.resume(throwing: SyncConnectionError.connectionDeallocated)
                    return
                }

                if let error = error {
                    continuation.resume(throwing: error)
                    return
                }

                guard let data = content, !data.isEmpty else {
                    continuation.resume(throwing: SyncConnectionError.emptyMessage)
                    return
                }

                do {
                    let message = try SyncProtocolCoder.decode(data)
                    continuation.resume(returning: message)
                } catch {
                    self.logger.error("Failed to decode message: \(error.localizedDescription)")
                    continuation.resume(throwing: SyncConnectionError.decodingFailed(error))
                }
            }
        }
    }

    /// Starts a continuous receive loop that delivers messages via the onMessage callback.
    /// Each received message triggers the callback and then schedules the next receive.
    private func startReceiving() {
        guard !isReceiving else { return }
        isReceiving = true
        scheduleReceive()
    }

    /// Schedules a single receive operation, then recursively schedules the next one.
    private func scheduleReceive() {
        connection.receiveMessage { [weak self] content, context, isComplete, error in
            guard let self = self else { return }

            if let error = error {
                self.logger.error("Receive error on \(self.id): \(error.localizedDescription)")
                self.onError?(error)
                self.isReceiving = false
                return
            }

            if let data = content, !data.isEmpty {
                do {
                    let message = try SyncProtocolCoder.decode(data)
                    self.onMessage?(message)
                } catch {
                    self.logger.error("Decode error on \(self.id): \(error.localizedDescription)")
                    // Send an error message back to the peer.
                    let errorMsg = SyncProtocolCoder.makeError(
                        code: "DECODE_ERROR",
                        message: "Failed to decode message: \(error.localizedDescription)"
                    )
                    self.send(errorMsg) { _ in }
                }
            }

            // If the connection is still ready, schedule the next receive.
            if self.connection.state == .ready {
                self.scheduleReceive()
            } else {
                self.isReceiving = false
            }
        }
    }
}

// MARK: - Error Types

/// Errors specific to SyncConnection operations.
enum SyncConnectionError: LocalizedError {
    case connectionDeallocated
    case emptyMessage
    case decodingFailed(Error)
    case sendFailed(Error)
    case connectionNotReady

    var errorDescription: String? {
        switch self {
        case .connectionDeallocated:
            return "Connection was deallocated during operation"
        case .emptyMessage:
            return "Received empty message from peer"
        case .decodingFailed(let underlying):
            return "Failed to decode message: \(underlying.localizedDescription)"
        case .sendFailed(let underlying):
            return "Failed to send message: \(underlying.localizedDescription)"
        case .connectionNotReady:
            return "Connection is not in the ready state"
        }
    }
}
