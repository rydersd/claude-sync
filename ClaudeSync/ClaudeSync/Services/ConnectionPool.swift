// ConnectionPool.swift
// ClaudeSync
//
// Manages persistent TCP connections to peers for live auto-sync.
// Sends keepalive messages every 15 seconds, marks peers dead after 45 seconds
// of silence, and auto-reconnects with exponential backoff (2s -> 4s -> 8s -> 16s -> 30s cap).
// After 1 hour offline, stops retrying and waits for mDNS re-discovery.
//
// All mutable state is actor-isolated for thread safety.

import Foundation
import Network
import os

/// Manages a pool of persistent peer connections for live auto-sync.
/// Handles keepalive heartbeats, dead peer detection, and reconnection with
/// exponential backoff. Thread-safe via actor isolation.
actor ConnectionPool {

    // MARK: - Types

    /// Tracks the state of a single persistent peer connection.
    struct PeerConnection: Sendable {
        /// The unique peer device ID.
        let peerId: String

        /// The underlying sync connection to the peer.
        let connection: SyncConnection

        /// Timestamp of the last message received from this peer (any type).
        var lastMessageReceived: Date

        /// Whether this peer has subscribed to our file change notifications.
        var isSubscribed: Bool

        /// Current reconnection backoff delay in seconds.
        var reconnectDelay: TimeInterval

        /// Timestamp when the peer first went offline (for 1-hour timeout).
        var offlineSince: Date?

        /// The endpoint used to reconnect to this peer.
        var endpoint: NWEndpoint?
    }

    // MARK: - Configuration

    /// Interval between keepalive messages.
    static let keepaliveInterval: TimeInterval = 15.0

    /// Duration of silence before marking a peer as dead.
    static let deadPeerTimeout: TimeInterval = 45.0

    /// Initial reconnection delay.
    static let initialReconnectDelay: TimeInterval = 2.0

    /// Maximum reconnection delay (cap).
    static let maxReconnectDelay: TimeInterval = 30.0

    /// Maximum time to retry reconnection before giving up (waits for mDNS re-discovery).
    static let maxOfflineDuration: TimeInterval = 3600.0 // 1 hour

    // MARK: - Properties

    /// Active peer connections, keyed by peer device ID.
    private var connections: [String: PeerConnection] = [:]

    /// The keepalive timer task that runs while connections exist.
    private var keepaliveTask: Task<Void, Never>?

    /// Callback invoked when a peer is detected as dead (no messages within timeout).
    /// The caller should handle reconnection logic.
    private var onPeerDead: (@Sendable (String) async -> Void)?

    /// Callback invoked when a message is received from a peer connection.
    private var onMessageReceived: (@Sendable (SyncMessage, String) async -> Void)?

    /// Logger for connection pool events.
    private let logger = Logger(subsystem: "com.claudesync", category: "ConnectionPool")

    // MARK: - Callback Configuration

    /// Sets the callback invoked when a peer is detected as dead.
    func setOnPeerDead(_ handler: @escaping @Sendable (String) async -> Void) {
        self.onPeerDead = handler
    }

    /// Sets the callback invoked when a message is received from a peer.
    func setOnMessageReceived(_ handler: @escaping @Sendable (SyncMessage, String) async -> Void) {
        self.onMessageReceived = handler
    }

    // MARK: - Connection Management

    /// Adds a connection to the pool and starts monitoring it.
    /// If a connection for this peer already exists, the old one is replaced.
    /// - Parameters:
    ///   - peerId: The unique peer device ID.
    ///   - connection: The established SyncConnection.
    ///   - endpoint: The peer's endpoint for future reconnection attempts.
    func addConnection(peerId: String, connection: SyncConnection, endpoint: NWEndpoint? = nil) {
        // Remove any existing connection for this peer.
        if let existing = connections[peerId] {
            existing.connection.cancel()
        }

        let peerConn = PeerConnection(
            peerId: peerId,
            connection: connection,
            lastMessageReceived: Date(),
            isSubscribed: false,
            reconnectDelay: Self.initialReconnectDelay,
            offlineSince: nil,
            endpoint: endpoint
        )

        connections[peerId] = peerConn
        logger.info("Added connection to pool for peer \(peerId.prefix(8))")

        // Start keepalive if this is the first connection.
        if connections.count == 1 {
            startKeepalive()
        }
    }

    /// Removes a connection from the pool and cancels it.
    /// - Parameter peerId: The peer device ID to remove.
    func removeConnection(peerId: String) {
        if let conn = connections.removeValue(forKey: peerId) {
            conn.connection.cancel()
            logger.info("Removed connection from pool for peer \(peerId.prefix(8))")
        }

        // Stop keepalive if no connections remain.
        if connections.isEmpty {
            stopKeepalive()
        }
    }

    /// Marks a peer as subscribed to file change notifications.
    /// - Parameter peerId: The peer device ID to mark as subscribed.
    func markSubscribed(peerId: String) {
        connections[peerId]?.isSubscribed = true
        logger.info("Peer \(peerId.prefix(8)) marked as subscribed")
    }

    /// Records that a message was received from a peer, updating the last-seen timestamp.
    /// Also resets the reconnect delay since the peer is alive.
    /// - Parameter peerId: The peer device ID that sent the message.
    func markMessageReceived(from peerId: String) {
        connections[peerId]?.lastMessageReceived = Date()
        connections[peerId]?.offlineSince = nil
        connections[peerId]?.reconnectDelay = Self.initialReconnectDelay
    }

    /// Returns the list of device IDs for all active connections.
    func getActiveConnections() -> [String] {
        return Array(connections.keys)
    }

    /// Returns the list of device IDs for subscribed peers.
    func getSubscribedPeers() -> [String] {
        return connections.filter { $0.value.isSubscribed }.map { $0.key }
    }

    /// Returns the SyncConnection for a specific peer, if one exists in the pool.
    func connection(for peerId: String) -> SyncConnection? {
        return connections[peerId]?.connection
    }

    /// Returns whether the pool has a connection for the given peer.
    func hasConnection(for peerId: String) -> Bool {
        return connections[peerId] != nil
    }

    // MARK: - Broadcasting

    /// Sends a message to all subscribed peers.
    /// Failures on individual connections are logged but do not stop the broadcast.
    /// - Parameter message: The SyncMessage to broadcast.
    func broadcast(_ message: SyncMessage) async {
        let subscribedConns = connections.filter { $0.value.isSubscribed }

        for (peerId, peerConn) in subscribedConns {
            do {
                try await peerConn.connection.send(message)
            } catch {
                logger.error("Failed to broadcast to \(peerId.prefix(8)): \(error.localizedDescription)")
            }
        }
    }

    /// Sends a message to a specific peer.
    /// - Parameters:
    ///   - message: The SyncMessage to send.
    ///   - peerId: The target peer device ID.
    /// - Throws: ConnectionPoolError if the peer is not in the pool or the send fails.
    func send(_ message: SyncMessage, to peerId: String) async throws {
        guard let peerConn = connections[peerId] else {
            throw ConnectionPoolError.peerNotFound(peerId)
        }

        try await peerConn.connection.send(message)
    }

    // MARK: - Keepalive

    /// Starts the periodic keepalive timer.
    /// Sends keepalive to all connections and checks for dead peers.
    func startKeepalive() {
        guard keepaliveTask == nil else { return }

        keepaliveTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: UInt64(Self.keepaliveInterval * 1_000_000_000))
                guard !Task.isCancelled else { break }
                await self?.performKeepaliveCycle()
            }
        }

        logger.info("Keepalive timer started (interval: \(Self.keepaliveInterval)s)")
    }

    /// Stops the periodic keepalive timer.
    func stopKeepalive() {
        keepaliveTask?.cancel()
        keepaliveTask = nil
        logger.info("Keepalive timer stopped")
    }

    /// Performs one keepalive cycle: sends keepalive messages and checks for dead peers.
    private func performKeepaliveCycle() async {
        let keepaliveMsg = SyncProtocolCoder.makeKeepalive()
        let now = Date()
        var deadPeers: [String] = []

        for (peerId, peerConn) in connections {
            // Check if peer has timed out.
            let silenceDuration = now.timeIntervalSince(peerConn.lastMessageReceived)

            if silenceDuration > Self.deadPeerTimeout {
                deadPeers.append(peerId)
                continue
            }

            // Send keepalive to alive peers.
            do {
                try await peerConn.connection.send(keepaliveMsg)
            } catch {
                logger.warning("Failed to send keepalive to \(peerId.prefix(8)): \(error.localizedDescription)")
                // Don't immediately mark dead -- wait for the timeout threshold.
            }
        }

        // Handle dead peers.
        for peerId in deadPeers {
            logger.warning("Peer \(peerId.prefix(8)) is dead (no message for >\(Self.deadPeerTimeout)s)")

            if let peerConn = connections[peerId] {
                // Check if we should still try to reconnect.
                let offlineSince = peerConn.offlineSince ?? now
                let totalOffline = now.timeIntervalSince(offlineSince)

                if totalOffline < Self.maxOfflineDuration {
                    // Record when the peer first went offline and schedule reconnection.
                    connections[peerId]?.offlineSince = offlineSince == now ? now : peerConn.offlineSince
                    let currentDelay = peerConn.reconnectDelay

                    // Increase the backoff for next attempt.
                    let nextDelay = min(currentDelay * 2.0, Self.maxReconnectDelay)
                    connections[peerId]?.reconnectDelay = nextDelay

                    logger.info("Will attempt reconnect to \(peerId.prefix(8)) in \(currentDelay)s")

                    // Notify the caller to handle reconnection.
                    if let callback = onPeerDead {
                        await callback(peerId)
                    }
                } else {
                    // Exceeded 1-hour offline threshold -- give up and remove.
                    logger.info("Peer \(peerId.prefix(8)) offline for >\(Self.maxOfflineDuration)s, removing from pool")
                    removeConnection(peerId: peerId)
                }
            }
        }
    }

    /// Returns the current reconnect delay for a peer, or the initial delay if unknown.
    func reconnectDelay(for peerId: String) -> TimeInterval {
        return connections[peerId]?.reconnectDelay ?? Self.initialReconnectDelay
    }

    /// Removes all connections and stops keepalive.
    func removeAll() {
        for (_, conn) in connections {
            conn.connection.cancel()
        }
        connections.removeAll()
        stopKeepalive()
        logger.info("All connections removed from pool")
    }
}

// MARK: - Error Types

/// Errors specific to ConnectionPool operations.
enum ConnectionPoolError: LocalizedError {
    case peerNotFound(String)
    case sendFailed(String, Error)

    var errorDescription: String? {
        switch self {
        case .peerNotFound(let peerId):
            return "No connection in pool for peer: \(peerId.prefix(8))"
        case .sendFailed(let peerId, let underlying):
            return "Failed to send to peer \(peerId.prefix(8)): \(underlying.localizedDescription)"
        }
    }
}
