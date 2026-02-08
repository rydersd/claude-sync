// NetworkManager.swift
// ClaudeSync
//
// Central coordinator that owns the ServiceAdvertiser, ServiceBrowser,
// ConfigScanner, and SyncEngine. Published as an ObservableObject for
// SwiftUI data flow. Manages the lifecycle of peer discovery, connections,
// and sync operations.

import Foundation
import Network
import Combine
import os

/// The root observable object that coordinates all networking and sync state.
/// Owned by ClaudeSyncApp and injected into the view hierarchy via @EnvironmentObject.
@MainActor
final class NetworkManager: ObservableObject {

    // MARK: - Published State

    /// All discovered peers, keyed by device ID.
    @Published var peers: [Peer] = []

    /// Whether the network services are currently active.
    @Published var isOnline: Bool = false

    /// The local config file hashes.
    @Published var localHashes: [String: String] = [:]

    /// The local config fingerprint.
    @Published var localFingerprint: String = ""

    /// Number of local config files.
    @Published var localConfigCount: Int = 0

    /// Error message to display in the UI, if any.
    @Published var lastError: String?

    /// Whether a scan is currently in progress.
    @Published var isScanning: Bool = false

    // MARK: - Private Properties

    /// The Bonjour advertiser for this machine.
    private var advertiser: ServiceAdvertiser?

    /// The Bonjour browser for discovering peers.
    private var browser: ServiceBrowser?

    /// The config scanner for local files.
    private let scanner = ConfigScanner()

    /// The sync engine for file transfer.
    private let syncEngine = SyncEngine()

    /// Logger for network manager events.
    private let logger = Logger(subsystem: "com.claudesync", category: "NetworkManager")

    /// Active connections to peers, keyed by device ID.
    private var connections: [String: SyncConnection] = [:]

    /// Timer for periodic config re-scanning.
    private var scanTimer: Timer?

    // MARK: - Initialization

    init() {
        // Start networking immediately on init.
        Task { @MainActor in
            await startServices()
        }
    }

    deinit {
        scanTimer?.invalidate()
    }

    // MARK: - Service Lifecycle

    /// Starts Bonjour advertising and browsing, and performs initial config scan.
    func startServices() async {
        logger.info("Starting ClaudeSync services...")

        // Perform initial config scan.
        await refreshLocalConfig()

        // Start advertising.
        let deviceId = DeviceIdentity.deviceId
        let deviceName = DeviceIdentity.deviceName

        let adv = ServiceAdvertiser(deviceId: deviceId, deviceName: deviceName)
        adv.onIncomingConnection = { [weak self] connection in
            Task { @MainActor [weak self] in
                self?.handleIncomingConnection(connection)
            }
        }
        adv.onStateChange = { [weak self] state in
            Task { @MainActor [weak self] in
                switch state {
                case .ready:
                    self?.isOnline = true
                case .failed, .cancelled:
                    self?.isOnline = false
                default:
                    break
                }
            }
        }
        adv.start()
        adv.updateMetadata(configCount: localConfigCount, fingerprint: localFingerprint)
        self.advertiser = adv

        // Start browsing.
        let brw = ServiceBrowser(localDeviceId: deviceId)
        brw.onPeerDiscovered = { [weak self] peerInfo in
            Task { @MainActor [weak self] in
                self?.handlePeerDiscovered(peerInfo)
            }
        }
        brw.onPeerLost = { [weak self] deviceId in
            Task { @MainActor [weak self] in
                self?.handlePeerLost(deviceId: deviceId)
            }
        }
        brw.start()
        self.browser = brw

        // Set up periodic re-scanning every 30 seconds to detect local file changes.
        scanTimer = Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in
                await self?.refreshLocalConfig()
            }
        }

        isOnline = true
        logger.info("ClaudeSync services started. Device ID: \(deviceId.prefix(8))")
    }

    /// Stops all network services.
    func stopServices() {
        advertiser?.stop()
        browser?.stop()
        scanTimer?.invalidate()
        scanTimer = nil

        // Cancel all active connections.
        for (_, connection) in connections {
            connection.cancel()
        }
        connections.removeAll()

        isOnline = false
        logger.info("ClaudeSync services stopped")
    }

    // MARK: - Config Scanning

    /// Re-scans the local ~/.claude/ directory and updates published state.
    func refreshLocalConfig() async {
        isScanning = true
        defer { isScanning = false }

        let hashes = await scanner.scan()
        let fingerprint = FileHasher.computeFingerprint(from: hashes)

        localHashes = hashes
        localFingerprint = fingerprint
        localConfigCount = hashes.count

        // Update the advertiser's TXT record with new metadata.
        advertiser?.updateMetadata(configCount: localConfigCount, fingerprint: localFingerprint)

        logger.info("Local config scan: \(hashes.count) files, fingerprint: \(fingerprint.prefix(8))")
    }

    // MARK: - Peer Discovery

    /// Handles a newly discovered peer from the browser.
    private func handlePeerDiscovered(_ peerInfo: PeerInfo) {
        // Check if we already know this peer.
        if let existingIndex = peers.firstIndex(where: { $0.id == peerInfo.deviceId }) {
            // Update existing peer metadata.
            let peer = peers[existingIndex]
            peer.name = peerInfo.name
            peer.platform = peerInfo.platform
            peer.configCount = peerInfo.configCount
            peer.fingerprint = peerInfo.fingerprint
            peer.protocolVersion = peerInfo.protocolVersion
            peer.endpoint = peerInfo.endpoint
            peer.browserResult = peerInfo.browserResult
            peer.lastSeen = Date()

            // Update status if it was offline.
            if peer.status == .offline {
                peer.status = .discovered
            }

            // Quick check: are we already in sync?
            if DiffEngine.areIdentical(
                localFingerprint: localFingerprint,
                remoteFingerprint: peerInfo.fingerprint
            ) {
                peer.status = .synced
                peer.differingFileCount = 0
            }
        } else {
            // Create a new peer entry.
            let peer = Peer(
                id: peerInfo.deviceId,
                name: peerInfo.name,
                platform: peerInfo.platform,
                configCount: peerInfo.configCount,
                fingerprint: peerInfo.fingerprint,
                protocolVersion: peerInfo.protocolVersion,
                status: .discovered,
                endpoint: peerInfo.endpoint,
                browserResult: peerInfo.browserResult,
                lastSeen: Date()
            )

            // Quick sync check via fingerprint.
            if DiffEngine.areIdentical(
                localFingerprint: localFingerprint,
                remoteFingerprint: peerInfo.fingerprint
            ) {
                peer.status = .synced
                peer.differingFileCount = 0
            }

            peers.append(peer)
        }

        logger.info("Peer updated: \(peerInfo.name) (\(peerInfo.deviceId.prefix(8)))")
    }

    /// Handles a peer that has disappeared from the network.
    private func handlePeerLost(deviceId: String) {
        if let index = peers.firstIndex(where: { $0.id == deviceId }) {
            peers[index].status = .offline
            // Cancel any active connection to this peer.
            if let conn = connections.removeValue(forKey: deviceId) {
                conn.cancel()
            }
        }

        logger.info("Peer lost: \(deviceId.prefix(8))")
    }

    // MARK: - Connection Management

    /// Handles an incoming connection from the listener.
    private func handleIncomingConnection(_ syncConnection: SyncConnection) {
        syncConnection.onMessage = { [weak self] message in
            Task { @MainActor [weak self] in
                self?.handleMessage(message, from: syncConnection)
            }
        }

        syncConnection.onStateChange = { [weak self] state in
            Task { @MainActor [weak self] in
                guard let self = self else { return }
                switch state {
                case .ready:
                    self.logger.info("Incoming connection ready: \(syncConnection.id)")
                case .failed(let error):
                    self.logger.error("Incoming connection failed: \(error.localizedDescription)")
                case .cancelled:
                    self.logger.info("Incoming connection cancelled: \(syncConnection.id)")
                default:
                    break
                }
            }
        }

        syncConnection.start()
    }

    /// Connects to a specific peer.
    func connectToPeer(_ peer: Peer) async {
        guard let endpoint = peer.endpoint else {
            lastError = "No endpoint available for peer \(peer.name)"
            return
        }

        peer.status = .connecting

        let connection = SyncConnection(to: endpoint, id: "outgoing-\(peer.id.prefix(8))")

        connection.onMessage = { [weak self] message in
            Task { @MainActor [weak self] in
                self?.handleMessage(message, from: connection, peerId: peer.id)
            }
        }

        connection.onStateChange = { [weak self] state in
            Task { @MainActor [weak self] in
                guard let self = self else { return }
                switch state {
                case .ready:
                    peer.status = .connected
                    self.connections[peer.id] = connection
                    // Send hello message.
                    await self.sendHello(via: connection)
                case .failed(let error):
                    peer.status = .error
                    self.lastError = "Connection to \(peer.name) failed: \(error.localizedDescription)"
                    self.connections.removeValue(forKey: peer.id)
                case .cancelled:
                    if peer.status != .offline {
                        peer.status = .discovered
                    }
                    self.connections.removeValue(forKey: peer.id)
                default:
                    break
                }
            }
        }

        connection.start()
        connections[peer.id] = connection
    }

    /// Sends a hello message on a connection.
    private func sendHello(via connection: SyncConnection) async {
        let hello = SyncProtocolCoder.makeHello(
            deviceId: DeviceIdentity.deviceId,
            deviceName: DeviceIdentity.deviceName,
            configCount: localConfigCount,
            fingerprint: localFingerprint
        )

        do {
            try await connection.send(hello)
            logger.info("Sent hello via \(connection.id)")
        } catch {
            logger.error("Failed to send hello: \(error.localizedDescription)")
            lastError = "Failed to send hello: \(error.localizedDescription)"
        }
    }

    /// Handles an incoming protocol message.
    private func handleMessage(_ message: SyncMessage, from connection: SyncConnection, peerId: String? = nil) {
        switch message {
        case .hello(let payload):
            handleHello(payload, from: connection)

        case .manifestRequest(let payload):
            handleManifestRequest(payload, from: connection)

        case .manifest(let payload):
            handleManifest(payload, peerId: peerId)

        case .fileTransfer(let payload):
            Task {
                await handleFileTransfer(payload, from: connection)
            }

        case .fileAck:
            // Handled inline during push operations.
            break

        case .syncComplete(let payload):
            logger.info("Sync complete from peer: \(payload.message)")

        case .error(let payload):
            logger.error("Error from peer: \(payload.message)")
            lastError = "Peer error: \(payload.message)"

        case .syncRequest, .syncAck:
            // Handled during sync operations.
            break
        }
    }

    /// Handles a hello message from a peer.
    private func handleHello(_ payload: HelloPayload, from connection: SyncConnection) {
        logger.info("Received hello from \(payload.deviceName) (\(payload.deviceId.prefix(8)))")

        // Associate this connection with the peer.
        if let peer = peers.first(where: { $0.id == payload.deviceId }) {
            peer.status = .connected
            peer.name = payload.deviceName
            peer.configCount = payload.configCount
            peer.fingerprint = payload.fingerprint
            peer.lastSeen = Date()
            connections[peer.id] = connection

            // Quick sync check.
            if DiffEngine.areIdentical(
                localFingerprint: localFingerprint,
                remoteFingerprint: payload.fingerprint
            ) {
                peer.status = .synced
                peer.differingFileCount = 0
            }
        } else {
            // New peer we did not know about yet (connected to us before we discovered them).
            let peer = Peer(
                id: payload.deviceId,
                name: payload.deviceName,
                platform: payload.platform,
                configCount: payload.configCount,
                fingerprint: payload.fingerprint,
                protocolVersion: payload.protocolVersion,
                status: .connected,
                lastSeen: Date()
            )
            peers.append(peer)
            connections[peer.id] = connection
        }

        // Send hello back if this was an incoming connection.
        Task {
            await sendHello(via: connection)
        }
    }

    /// Handles a manifest request from a peer.
    private func handleManifestRequest(_ payload: ManifestRequestPayload, from connection: SyncConnection) {
        let response = SyncProtocolCoder.makeManifest(
            requestId: payload.requestId,
            files: localHashes
        )

        Task {
            do {
                try await connection.send(response)
                logger.info("Sent manifest with \(self.localHashes.count) files")
            } catch {
                logger.error("Failed to send manifest: \(error.localizedDescription)")
            }
        }
    }

    /// Handles a received manifest from a peer.
    private func handleManifest(_ payload: ManifestPayload, peerId: String?) {
        guard let peerId = peerId,
              let peer = peers.first(where: { $0.id == peerId }) else {
            return
        }

        peer.remoteManifest = payload.files
        peer.configCount = payload.files.count

        // Compute differences.
        let diffCount = DiffEngine.differenceCount(
            local: localHashes,
            remote: payload.files
        )
        peer.differingFileCount = diffCount

        if diffCount == 0 {
            peer.status = .synced
        } else {
            peer.status = .connected
        }

        logger.info("Received manifest from \(peer.name): \(payload.files.count) files, \(diffCount) differences")
    }

    /// Handles an incoming file transfer (we are the pull target).
    private func handleFileTransfer(_ payload: FileTransferPayload, from connection: SyncConnection) async {
        // Decode the base64 content.
        guard let fileData = Data(base64Encoded: payload.contentBase64) else {
            let ack = FileAckPayload(
                syncId: payload.syncId,
                relativePath: payload.relativePath,
                success: false,
                error: "Base64 decode failed"
            )
            try? await connection.send(.fileAck(ack))
            return
        }

        // Write the file.
        do {
            try await scanner.writeFile(data: fileData, relativePath: payload.relativePath)

            let ack = FileAckPayload(
                syncId: payload.syncId,
                relativePath: payload.relativePath,
                success: true,
                error: nil
            )
            try? await connection.send(.fileAck(ack))
            logger.info("Received and wrote file: \(payload.relativePath)")
        } catch {
            let ack = FileAckPayload(
                syncId: payload.syncId,
                relativePath: payload.relativePath,
                success: false,
                error: error.localizedDescription
            )
            try? await connection.send(.fileAck(ack))
            logger.error("Failed to write received file: \(error.localizedDescription)")
        }
    }

    // MARK: - Sync Operations

    /// Requests and receives the manifest from a peer to compute differences.
    func compareWithPeer(_ peer: Peer) async {
        guard let connection = connections[peer.id] else {
            lastError = "No connection to \(peer.name)"
            return
        }

        peer.status = .comparing

        let request = SyncProtocolCoder.makeManifestRequest()
        do {
            try await connection.send(request)
            logger.info("Requested manifest from \(peer.name)")
        } catch {
            peer.status = .error
            lastError = "Failed to request manifest: \(error.localizedDescription)"
        }
    }

    /// Pushes differing files to a connected peer.
    func pushToPeer(_ peer: Peer) async {
        guard let connection = connections[peer.id],
              let remoteManifest = peer.remoteManifest else {
            lastError = "Cannot push: no connection or manifest for \(peer.name)"
            return
        }

        peer.status = .syncing

        let filesToPush = DiffEngine.filesToPush(local: localHashes, remote: remoteManifest)

        guard !filesToPush.isEmpty else {
            peer.status = .synced
            peer.differingFileCount = 0
            return
        }

        let syncId = SyncProtocolCoder.generateId()

        // Send sync request.
        let syncRequest = SyncRequestPayload(
            syncId: syncId,
            direction: "push",
            files: filesToPush
        )

        do {
            try await connection.send(.syncRequest(syncRequest))

            // Push files via the sync engine.
            let count = try await syncEngine.pushFiles(
                filesToPush,
                localHashes: localHashes,
                via: connection,
                syncId: syncId
            )

            logger.info("Pushed \(count) files to \(peer.name)")

            // Refresh state after push.
            await refreshLocalConfig()
            peer.status = .synced
            peer.differingFileCount = 0
        } catch {
            peer.status = .error
            lastError = "Push failed: \(error.localizedDescription)"
            logger.error("Push to \(peer.name) failed: \(error.localizedDescription)")
        }
    }

    /// Pulls differing files from a connected peer.
    func pullFromPeer(_ peer: Peer) async {
        guard let connection = connections[peer.id],
              let remoteManifest = peer.remoteManifest else {
            lastError = "Cannot pull: no connection or manifest for \(peer.name)"
            return
        }

        peer.status = .syncing

        let filesToPull = DiffEngine.filesToPull(local: localHashes, remote: remoteManifest)

        guard !filesToPull.isEmpty else {
            peer.status = .synced
            peer.differingFileCount = 0
            return
        }

        let syncId = SyncProtocolCoder.generateId()

        // Send sync request asking the peer to send us files.
        let syncRequest = SyncRequestPayload(
            syncId: syncId,
            direction: "pull",
            files: filesToPull
        )

        do {
            try await connection.send(.syncRequest(syncRequest))

            // Receive files via the sync engine.
            let count = try await syncEngine.receiveFiles(
                expectedFiles: filesToPull.count,
                via: connection,
                syncId: syncId
            )

            logger.info("Pulled \(count) files from \(peer.name)")

            // Refresh state after pull.
            await refreshLocalConfig()
            peer.status = .synced
            peer.differingFileCount = 0
        } catch {
            peer.status = .error
            lastError = "Pull failed: \(error.localizedDescription)"
            logger.error("Pull from \(peer.name) failed: \(error.localizedDescription)")
        }
    }

    /// Returns the file diffs between local and a specific peer.
    func diffsWithPeer(_ peer: Peer) -> [FileDiff] {
        guard let remoteManifest = peer.remoteManifest else {
            return []
        }
        return DiffEngine.compare(local: localHashes, remote: remoteManifest)
    }
}
