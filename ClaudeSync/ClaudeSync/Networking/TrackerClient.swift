// TrackerClient.swift
// ClaudeSync
//
// Connects to a tracker server via WebSocket for WAN peer discovery.
// Handles device registration, heartbeat keepalive, peer list queries,
// and relay channel negotiation. Uses the same flat JSON message pattern
// as SyncMessage for wire encoding.
//
// Tracker protocol messages are discriminated by a `type` field:
//   register, register_ack, heartbeat, peer_list_request, peer_list_response,
//   peer_online, peer_offline, relay_request, relay_ack, relay_data

import Foundation
import os

// MARK: - Tracker State

/// The connection state of the tracker client.
enum TrackerState: Sendable {
    case disconnected
    case connecting
    case connected
    case registered
}

// MARK: - Tracker Client

/// Manages a WebSocket connection to a tracker server for WAN peer discovery.
/// Actor-isolated for thread safety. Sends periodic heartbeats and receives
/// peer presence notifications and relay data.
actor TrackerClient {

    // MARK: - Configuration

    /// Heartbeat interval in seconds.
    private static let heartbeatInterval: TimeInterval = 30.0

    /// WebSocket ping interval for transport-level keepalive.
    private static let pingInterval: TimeInterval = 15.0

    // MARK: - Properties

    /// The tracker server WebSocket URL.
    private let trackerURL: URL

    /// This device's persistent UUID.
    private let deviceId: String

    /// This device's human-readable name.
    private let deviceName: String

    /// The current connection state.
    private(set) var state: TrackerState = .disconnected

    /// The WebSocket task.
    private var webSocket: URLSessionWebSocketTask?

    /// The URL session for WebSocket connections.
    private let session: URLSession

    /// Heartbeat timer task.
    private var heartbeatTask: Task<Void, Never>?

    /// Receive loop task.
    private var receiveTask: Task<Void, Never>?

    /// Logger for tracker operations.
    private let logger = Logger(subsystem: "com.claudesync", category: "TrackerClient")

    /// Pending relay request continuations, keyed by target device ID.
    private var pendingRelayRequests: [String: CheckedContinuation<String, Error>] = [:]

    /// Pending peer list request continuation (at most one at a time).
    private var pendingPeerListRequest: CheckedContinuation<[TrackerPeerInfo], Error>?

    // MARK: - Callbacks

    /// Called when a peer comes online on the tracker.
    var onPeerOnline: (@Sendable (TrackerPeerInfo) async -> Void)?

    /// Called when a peer goes offline on the tracker.
    var onPeerOffline: (@Sendable (String) async -> Void)?

    /// Called when relay data arrives. Parameters: relayId, fromDeviceId, payloadBase64.
    var onRelayData: (@Sendable (String, String, String) async -> Void)?

    /// Called when the tracker connection state changes.
    var onStateChange: (@Sendable (TrackerState) async -> Void)?

    // MARK: - Initialization

    /// Creates a tracker client for the given server URL.
    /// - Parameters:
    ///   - trackerURL: The WebSocket URL of the tracker server (e.g. wss://tracker.example.com).
    ///   - deviceId: This device's persistent UUID.
    ///   - deviceName: This device's human-readable name.
    init(trackerURL: URL, deviceId: String, deviceName: String) {
        self.trackerURL = trackerURL
        self.deviceId = deviceId
        self.deviceName = deviceName
        self.session = URLSession(configuration: .default)
    }

    /// Configures all event callbacks at once. Convenience for callers that set all four.
    func setCallbacks(
        onPeerOnline: (@Sendable (TrackerPeerInfo) async -> Void)?,
        onPeerOffline: (@Sendable (String) async -> Void)?,
        onRelayData: (@Sendable (String, String, String) async -> Void)?,
        onStateChange: (@Sendable (TrackerState) async -> Void)?
    ) {
        self.onPeerOnline = onPeerOnline
        self.onPeerOffline = onPeerOffline
        self.onRelayData = onRelayData
        self.onStateChange = onStateChange
    }

    // MARK: - Connection Lifecycle

    /// Connects to the tracker server and registers this device.
    func connect() async throws {
        guard state == .disconnected else {
            logger.info("TrackerClient already connected or connecting")
            return
        }

        state = .connecting
        await onStateChange?(.connecting)

        // Create the WebSocket connection.
        var request = URLRequest(url: trackerURL)
        request.timeoutInterval = 10

        let ws = session.webSocketTask(with: request)
        webSocket = ws
        ws.resume()

        state = .connected
        await onStateChange?(.connected)

        // Start the receive loop.
        receiveTask = Task { [weak self] in
            await self?.receiveLoop()
        }

        // Send registration.
        try await sendRegister()

        logger.info("Connected to tracker: \(self.trackerURL.absoluteString)")
    }

    /// Disconnects from the tracker server.
    func disconnect() async {
        heartbeatTask?.cancel()
        heartbeatTask = nil
        receiveTask?.cancel()
        receiveTask = nil

        webSocket?.cancel(with: .goingAway, reason: nil)
        webSocket = nil

        // Cancel any pending requests.
        for (_, continuation) in pendingRelayRequests {
            continuation.resume(throwing: TrackerError.disconnected)
        }
        pendingRelayRequests.removeAll()

        if let peerListCont = pendingPeerListRequest {
            peerListCont.resume(throwing: TrackerError.disconnected)
            pendingPeerListRequest = nil
        }

        state = .disconnected
        await onStateChange?(.disconnected)

        logger.info("Disconnected from tracker")
    }

    // MARK: - Registration

    /// Sends a register message to the tracker.
    private func sendRegister() async throws {
        let fingerprint: String
        do {
            fingerprint = try await CertificateManager.shared.certificateFingerprint()
        } catch {
            // If certificate generation fails, use a placeholder.
            logger.warning("Could not get cert fingerprint for registration: \(error.localizedDescription)")
            fingerprint = "unknown"
        }

        let msg = TrackerMessage.register(TrackerRegisterMessage(
            deviceId: deviceId,
            name: deviceName,
            platform: DeviceIdentity.platform,
            fingerprint: fingerprint,
            capabilities: ["auto_sync", "persistent", "relay"]
        ))

        try await send(msg)
    }

    // MARK: - Peer Discovery

    /// Requests the list of registered peers from the tracker.
    /// Blocks until the response arrives or the connection is lost.
    func requestPeerList() async throws -> [TrackerPeerInfo] {
        guard state == .registered || state == .connected else {
            throw TrackerError.notConnected
        }

        return try await withCheckedThrowingContinuation { continuation in
            pendingPeerListRequest = continuation

            Task {
                do {
                    try await send(.peerListRequest)
                } catch {
                    if let cont = pendingPeerListRequest {
                        pendingPeerListRequest = nil
                        cont.resume(throwing: error)
                    }
                }
            }
        }
    }

    // MARK: - Relay

    /// Requests a relay channel to a specific peer through the tracker.
    /// Returns the relay ID on success.
    func requestRelay(targetDeviceId: String) async throws -> String {
        guard state == .registered || state == .connected else {
            throw TrackerError.notConnected
        }

        return try await withCheckedThrowingContinuation { continuation in
            pendingRelayRequests[targetDeviceId] = continuation

            Task {
                let msg = TrackerMessage.relayRequest(TrackerRelayRequestMessage(
                    targetDeviceId: targetDeviceId
                ))
                do {
                    try await send(msg)
                } catch {
                    if let cont = pendingRelayRequests.removeValue(forKey: targetDeviceId) {
                        cont.resume(throwing: error)
                    }
                }
            }
        }
    }

    /// Sends relay data to a peer through the tracker.
    func sendRelayData(relayId: String, targetDeviceId: String, payloadBase64: String) async throws {
        let msg = TrackerMessage.relayData(TrackerRelayDataMessage(
            relayId: relayId,
            fromDeviceId: deviceId,
            targetDeviceId: targetDeviceId,
            payloadBase64: payloadBase64
        ))
        try await send(msg)
    }

    // MARK: - Heartbeat

    /// Starts the periodic heartbeat timer.
    private func startHeartbeat() {
        guard heartbeatTask == nil else { return }

        heartbeatTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: UInt64(Self.heartbeatInterval * 1_000_000_000))
                guard !Task.isCancelled else { break }

                do {
                    let msg = TrackerMessage.heartbeat(TrackerHeartbeatMessage(
                        deviceId: self?.deviceId ?? "",
                        timestamp: Int(Date().timeIntervalSince1970)
                    ))
                    try await self?.send(msg)
                } catch {
                    // Heartbeat failure will be detected by the receive loop or reconnect logic.
                    break
                }
            }
        }

        logger.info("Heartbeat started (interval: \(Self.heartbeatInterval)s)")
    }

    // MARK: - Receive Loop

    /// Continuously receives WebSocket messages and dispatches them.
    private func receiveLoop() async {
        guard let ws = webSocket else { return }

        while !Task.isCancelled {
            do {
                let wsMessage = try await ws.receive()

                let data: Data
                switch wsMessage {
                case .string(let text):
                    guard let textData = text.data(using: .utf8) else { continue }
                    data = textData
                case .data(let rawData):
                    data = rawData
                @unknown default:
                    continue
                }

                let message = try TrackerMessageCoder.decode(data)
                await handleMessage(message)

            } catch {
                if Task.isCancelled { break }

                logger.error("Tracker receive error: \(error.localizedDescription)")
                // Connection lost -- update state and break.
                state = .disconnected
                await onStateChange?(.disconnected)
                break
            }
        }
    }

    /// Dispatches a received tracker message to the appropriate handler.
    private func handleMessage(_ message: TrackerMessage) async {
        switch message {
        case .registerAck(let payload):
            handleRegisterAck(payload)

        case .peerListResponse(let payload):
            handlePeerListResponse(payload)

        case .peerOnline(let payload):
            if let callback = onPeerOnline {
                await callback(payload.peer)
            }

        case .peerOffline(let payload):
            if let callback = onPeerOffline {
                await callback(payload.deviceId)
            }

        case .relayAck(let payload):
            handleRelayAck(payload)

        case .relayData(let payload):
            if let callback = onRelayData {
                await callback(payload.relayId, payload.fromDeviceId, payload.payloadBase64)
            }

        case .register, .heartbeat, .peerListRequest, .relayRequest:
            // These are outgoing-only; receiving them from the server is unexpected but harmless.
            logger.warning("Received unexpected outgoing message type from tracker")
        }
    }

    /// Handles the register acknowledgment from the tracker.
    private func handleRegisterAck(_ payload: TrackerRegisterAckMessage) {
        if payload.success {
            state = .registered
            Task { await onStateChange?(.registered) }
            startHeartbeat()
            logger.info("Registered with tracker. Assigned ID confirmation: \(payload.assignedId ?? "none")")
        } else {
            logger.error("Tracker registration failed: \(payload.error ?? "unknown")")
        }
    }

    /// Handles a peer list response.
    private func handlePeerListResponse(_ payload: TrackerPeerListResponseMessage) {
        if let continuation = pendingPeerListRequest {
            pendingPeerListRequest = nil
            continuation.resume(returning: payload.peers)
        }
    }

    /// Handles a relay acknowledgment.
    private func handleRelayAck(_ payload: TrackerRelayAckMessage) {
        if let continuation = pendingRelayRequests.removeValue(forKey: payload.targetDeviceId) {
            if payload.success, let relayId = payload.relayId {
                continuation.resume(returning: relayId)
            } else {
                continuation.resume(throwing: TrackerError.relayRequestRejected(
                    payload.error ?? "Relay request rejected"
                ))
            }
        }
    }

    // MARK: - Sending

    /// Sends a tracker message as JSON over the WebSocket.
    private func send(_ message: TrackerMessage) async throws {
        guard let ws = webSocket else {
            throw TrackerError.notConnected
        }

        let data = try TrackerMessageCoder.encode(message)
        guard let jsonString = String(data: data, encoding: .utf8) else {
            throw TrackerError.encodingFailed
        }

        try await ws.send(.string(jsonString))
    }
}

// MARK: - Tracker Message Types

/// Top-level tracker protocol message, discriminated by `type` field.
/// Uses the same flat JSON Codable pattern as SyncMessage.
enum TrackerMessage: Codable, Sendable {
    case register(TrackerRegisterMessage)
    case registerAck(TrackerRegisterAckMessage)
    case heartbeat(TrackerHeartbeatMessage)
    case peerListRequest
    case peerListResponse(TrackerPeerListResponseMessage)
    case peerOnline(TrackerPeerOnlineMessage)
    case peerOffline(TrackerPeerOfflineMessage)
    case relayRequest(TrackerRelayRequestMessage)
    case relayAck(TrackerRelayAckMessage)
    case relayData(TrackerRelayDataMessage)

    // MARK: - Type Discriminator

    private struct TypePeek: Decodable {
        let type: String
    }

    private enum TypeOnlyKeys: String, CodingKey {
        case type
    }

    // MARK: - Codable

    init(from decoder: Decoder) throws {
        let peek = try TypePeek(from: decoder)

        switch peek.type {
        case "register":
            self = .register(try TrackerRegisterMessage(from: decoder))
        case "register_ack":
            self = .registerAck(try TrackerRegisterAckMessage(from: decoder))
        case "heartbeat":
            self = .heartbeat(try TrackerHeartbeatMessage(from: decoder))
        case "peer_list_request":
            self = .peerListRequest
        case "peer_list_response":
            self = .peerListResponse(try TrackerPeerListResponseMessage(from: decoder))
        case "peer_online":
            self = .peerOnline(try TrackerPeerOnlineMessage(from: decoder))
        case "peer_offline":
            self = .peerOffline(try TrackerPeerOfflineMessage(from: decoder))
        case "relay_request":
            self = .relayRequest(try TrackerRelayRequestMessage(from: decoder))
        case "relay_ack":
            self = .relayAck(try TrackerRelayAckMessage(from: decoder))
        case "relay_data":
            self = .relayData(try TrackerRelayDataMessage(from: decoder))
        default:
            throw DecodingError.dataCorruptedError(
                forKey: TypeOnlyKeys.type,
                in: try decoder.container(keyedBy: TypeOnlyKeys.self),
                debugDescription: "Unknown tracker message type: \(peek.type)"
            )
        }
    }

    func encode(to encoder: Encoder) throws {
        switch self {
        case .register(let msg):
            try msg.encode(to: encoder)
        case .registerAck(let msg):
            try msg.encode(to: encoder)
        case .heartbeat(let msg):
            try msg.encode(to: encoder)
        case .peerListRequest:
            var container = encoder.container(keyedBy: TypeOnlyKeys.self)
            try container.encode("peer_list_request", forKey: .type)
        case .peerListResponse(let msg):
            try msg.encode(to: encoder)
        case .peerOnline(let msg):
            try msg.encode(to: encoder)
        case .peerOffline(let msg):
            try msg.encode(to: encoder)
        case .relayRequest(let msg):
            try msg.encode(to: encoder)
        case .relayAck(let msg):
            try msg.encode(to: encoder)
        case .relayData(let msg):
            try msg.encode(to: encoder)
        }
    }
}

// MARK: - Register Message

/// Sent by a device to register with the tracker.
struct TrackerRegisterMessage: Codable, Sendable {
    let deviceId: String
    let name: String
    let platform: String
    let fingerprint: String
    let capabilities: [String]

    private enum CodingKeys: String, CodingKey {
        case type
        case deviceId = "device_id"
        case name, platform, fingerprint, capabilities
    }

    init(deviceId: String, name: String, platform: String, fingerprint: String, capabilities: [String]) {
        self.deviceId = deviceId
        self.name = name
        self.platform = platform
        self.fingerprint = fingerprint
        self.capabilities = capabilities
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        deviceId = try container.decode(String.self, forKey: .deviceId)
        name = try container.decode(String.self, forKey: .name)
        platform = try container.decode(String.self, forKey: .platform)
        fingerprint = try container.decode(String.self, forKey: .fingerprint)
        capabilities = try container.decodeIfPresent([String].self, forKey: .capabilities) ?? []
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode("register", forKey: .type)
        try container.encode(deviceId, forKey: .deviceId)
        try container.encode(name, forKey: .name)
        try container.encode(platform, forKey: .platform)
        try container.encode(fingerprint, forKey: .fingerprint)
        try container.encode(capabilities, forKey: .capabilities)
    }
}

// MARK: - Register Ack Message

/// Sent by the tracker in response to a register message.
struct TrackerRegisterAckMessage: Codable, Sendable {
    let success: Bool
    let assignedId: String?
    let error: String?

    private enum CodingKeys: String, CodingKey {
        case type
        case success
        case assignedId = "assigned_id"
        case error
    }

    init(success: Bool, assignedId: String? = nil, error: String? = nil) {
        self.success = success
        self.assignedId = assignedId
        self.error = error
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        success = try container.decode(Bool.self, forKey: .success)
        assignedId = try container.decodeIfPresent(String.self, forKey: .assignedId)
        error = try container.decodeIfPresent(String.self, forKey: .error)
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode("register_ack", forKey: .type)
        try container.encode(success, forKey: .success)
        try container.encodeIfPresent(assignedId, forKey: .assignedId)
        try container.encodeIfPresent(error, forKey: .error)
    }
}

// MARK: - Heartbeat Message

/// Periodic keepalive sent to the tracker.
struct TrackerHeartbeatMessage: Codable, Sendable {
    let deviceId: String
    let timestamp: Int

    private enum CodingKeys: String, CodingKey {
        case type
        case deviceId = "device_id"
        case timestamp
    }

    init(deviceId: String, timestamp: Int = Int(Date().timeIntervalSince1970)) {
        self.deviceId = deviceId
        self.timestamp = timestamp
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        deviceId = try container.decode(String.self, forKey: .deviceId)
        timestamp = try container.decode(Int.self, forKey: .timestamp)
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode("heartbeat", forKey: .type)
        try container.encode(deviceId, forKey: .deviceId)
        try container.encode(timestamp, forKey: .timestamp)
    }
}

// MARK: - Peer List Response Message

/// Tracker response containing all registered peers.
struct TrackerPeerListResponseMessage: Codable, Sendable {
    let peers: [TrackerPeerInfo]

    private enum CodingKeys: String, CodingKey {
        case type
        case peers
    }

    init(peers: [TrackerPeerInfo]) {
        self.peers = peers
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        peers = try container.decode([TrackerPeerInfo].self, forKey: .peers)
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode("peer_list_response", forKey: .type)
        try container.encode(peers, forKey: .peers)
    }
}

// MARK: - Peer Online Message

/// Notification that a peer has come online.
struct TrackerPeerOnlineMessage: Codable, Sendable {
    let peer: TrackerPeerInfo

    private enum CodingKeys: String, CodingKey {
        case type
        case peer
    }

    init(peer: TrackerPeerInfo) {
        self.peer = peer
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        peer = try container.decode(TrackerPeerInfo.self, forKey: .peer)
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode("peer_online", forKey: .type)
        try container.encode(peer, forKey: .peer)
    }
}

// MARK: - Peer Offline Message

/// Notification that a peer has gone offline.
struct TrackerPeerOfflineMessage: Codable, Sendable {
    let deviceId: String

    private enum CodingKeys: String, CodingKey {
        case type
        case deviceId = "device_id"
    }

    init(deviceId: String) {
        self.deviceId = deviceId
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        deviceId = try container.decode(String.self, forKey: .deviceId)
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode("peer_offline", forKey: .type)
        try container.encode(deviceId, forKey: .deviceId)
    }
}

// MARK: - Relay Request Message

/// Requests the tracker to set up a relay channel to a target peer.
struct TrackerRelayRequestMessage: Codable, Sendable {
    let targetDeviceId: String

    private enum CodingKeys: String, CodingKey {
        case type
        case targetDeviceId = "target_device_id"
    }

    init(targetDeviceId: String) {
        self.targetDeviceId = targetDeviceId
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        targetDeviceId = try container.decode(String.self, forKey: .targetDeviceId)
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode("relay_request", forKey: .type)
        try container.encode(targetDeviceId, forKey: .targetDeviceId)
    }
}

// MARK: - Relay Ack Message

/// Tracker response to a relay request.
struct TrackerRelayAckMessage: Codable, Sendable {
    let success: Bool
    let relayId: String?
    let targetDeviceId: String
    let error: String?

    private enum CodingKeys: String, CodingKey {
        case type
        case success
        case relayId = "relay_id"
        case targetDeviceId = "target_device_id"
        case error
    }

    init(success: Bool, relayId: String? = nil, targetDeviceId: String, error: String? = nil) {
        self.success = success
        self.relayId = relayId
        self.targetDeviceId = targetDeviceId
        self.error = error
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        success = try container.decode(Bool.self, forKey: .success)
        relayId = try container.decodeIfPresent(String.self, forKey: .relayId)
        targetDeviceId = try container.decode(String.self, forKey: .targetDeviceId)
        error = try container.decodeIfPresent(String.self, forKey: .error)
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode("relay_ack", forKey: .type)
        try container.encode(success, forKey: .success)
        try container.encodeIfPresent(relayId, forKey: .relayId)
        try container.encode(targetDeviceId, forKey: .targetDeviceId)
        try container.encodeIfPresent(error, forKey: .error)
    }
}

// MARK: - Relay Data Message

/// Carries relay data between two peers through the tracker.
struct TrackerRelayDataMessage: Codable, Sendable {
    let relayId: String
    let fromDeviceId: String
    let targetDeviceId: String
    let payloadBase64: String

    private enum CodingKeys: String, CodingKey {
        case type
        case relayId = "relay_id"
        case fromDeviceId = "from_device_id"
        case targetDeviceId = "target_device_id"
        case payloadBase64 = "payload_base64"
    }

    init(relayId: String, fromDeviceId: String, targetDeviceId: String, payloadBase64: String) {
        self.relayId = relayId
        self.fromDeviceId = fromDeviceId
        self.targetDeviceId = targetDeviceId
        self.payloadBase64 = payloadBase64
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        relayId = try container.decode(String.self, forKey: .relayId)
        fromDeviceId = try container.decode(String.self, forKey: .fromDeviceId)
        targetDeviceId = try container.decode(String.self, forKey: .targetDeviceId)
        payloadBase64 = try container.decode(String.self, forKey: .payloadBase64)
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode("relay_data", forKey: .type)
        try container.encode(relayId, forKey: .relayId)
        try container.encode(fromDeviceId, forKey: .fromDeviceId)
        try container.encode(targetDeviceId, forKey: .targetDeviceId)
        try container.encode(payloadBase64, forKey: .payloadBase64)
    }
}

// MARK: - Tracker Peer Info

/// Information about a peer as reported by the tracker.
struct TrackerPeerInfo: Codable, Sendable, Identifiable {
    let deviceId: String
    let name: String
    let platform: String
    let publicAddr: String
    let fingerprint: String
    let fileCount: Int
    let capabilities: [String]
    let lastSeen: Int

    var id: String { deviceId }

    private enum CodingKeys: String, CodingKey {
        case deviceId = "device_id"
        case name
        case platform
        case publicAddr = "public_addr"
        case fingerprint
        case fileCount = "file_count"
        case capabilities
        case lastSeen = "last_seen"
    }
}

// MARK: - Tracker Message Coder

/// JSON encoder/decoder for TrackerMessage, matching SyncProtocolCoder's pattern.
enum TrackerMessageCoder {

    static let encoder: JSONEncoder = {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        return encoder
    }()

    static let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        return decoder
    }()

    static func encode(_ message: TrackerMessage) throws -> Data {
        return try encoder.encode(message)
    }

    static func decode(_ data: Data) throws -> TrackerMessage {
        return try decoder.decode(TrackerMessage.self, from: data)
    }
}

// MARK: - Tracker Errors

/// Errors specific to tracker client operations.
enum TrackerError: LocalizedError {
    case notConnected
    case disconnected
    case encodingFailed
    case registrationFailed(String)
    case relayRequestRejected(String)
    case timeout

    var errorDescription: String? {
        switch self {
        case .notConnected:
            return "Not connected to tracker server"
        case .disconnected:
            return "Disconnected from tracker server"
        case .encodingFailed:
            return "Failed to encode tracker message"
        case .registrationFailed(let reason):
            return "Tracker registration failed: \(reason)"
        case .relayRequestRejected(let reason):
            return "Relay request rejected: \(reason)"
        case .timeout:
            return "Tracker operation timed out"
        }
    }
}
