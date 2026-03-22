// NetworkManager.swift
// ClaudeSync
//
// Central coordinator that owns the ServiceAdvertiser, ServiceBrowser,
// ConfigScanner, and SyncEngine. Published as an ObservableObject for
// SwiftUI data flow. Manages the lifecycle of peer discovery, connections,
// and sync operations per PROTOCOL.md.

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

    /// The local config file hashes (path -> SHA-256).
    @Published var localHashes: [String: String] = [:]

    /// The local config fingerprint (16-char hex per PROTOCOL.md Section 2.3).
    @Published var localFingerprint: String = ""

    /// Number of local config files.
    @Published var localConfigCount: Int = 0

    /// Error message to display in the UI, if any.
    @Published var lastError: String?

    /// Whether a scan is currently in progress.
    @Published var isScanning: Bool = false

    /// Whether live auto-sync mode is enabled (v2 persistent connections + file watching).
    @Published var isAutoSyncEnabled: Bool = false

    /// Whether the file watcher is currently active.
    @Published var isWatching: Bool = false

    /// WAN peers discovered via tracker server (kept separate from LAN peers list).
    @Published var wanPeers: [Peer] = []

    /// Whether we are currently connected to a tracker server.
    @Published var isTrackerConnected: Bool = false

    /// The loaded sync configuration (trackers, auto-sync, security settings).
    @Published var syncConfig: SyncConfig = .default

    /// The pairing manager for device trust operations.
    @Published var pairingManager: PairingManager?

    /// Live activity log for the UI. Records sync events, tracker status changes, etc.
    let activityLog = ActivityLog()

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

    /// Tracks whether we've already sent a hello response on an incoming connection.
    /// Key is connection ID.
    private var helloSent: Set<String> = []

    /// Whether a sync is currently in progress (per PROTOCOL.md Section 3.4: at most 1 concurrent sync).
    private var syncInProgress: Bool = false

    // MARK: - v2 Auto-Sync Properties

    /// File watcher for detecting local changes to ~/.claude/ in real time.
    private var fileWatcher: FileWatcher?

    /// Connection pool managing persistent peer connections with keepalive.
    private var connectionPool: ConnectionPool?

    /// Tracks which peers support v2 auto_sync capability (peer device ID -> capabilities).
    private var peerCapabilities: [String: [String]] = [:]

    /// v2 capabilities that this peer advertises when auto-sync is enabled.
    private static let autoSyncCapabilities = ["auto_sync", "persistent"]

    // MARK: - WAN / Tracker Properties

    /// The active tracker client for WAN peer discovery.
    private var trackerClient: TrackerClient?

    /// Active relay connections to WAN peers, keyed by relay ID.
    private var relayConnections: [String: RelayConnection] = [:]

    /// Maps WAN peer device IDs to their relay IDs for message routing.
    private var peerRelayMap: [String: String] = [:]

    /// The certificate manager instance for TLS identity.
    private let certManager = CertificateManager.shared

    /// Task for tracker reconnection with backoff.
    private var trackerReconnectTask: Task<Void, Never>?

    /// Current tracker reconnect backoff delay in seconds.
    private var trackerReconnectDelay: TimeInterval = 2.0

    /// Maximum tracker reconnect delay.
    private static let maxTrackerReconnectDelay: TimeInterval = 60.0

    /// Maximum offline duration before stopping WAN reconnection attempts.
    private static let maxWanOfflineDuration: TimeInterval = 3600.0

    // MARK: - Initialization

    init() {
        // Load sync configuration from disk.
        syncConfig = SyncConfigLoader.load()

        // Initialize the pairing manager.
        let pm = PairingManager()
        pairingManager = pm

        Task { @MainActor in
            // Load paired devices.
            await pm.loadPairedDevices()
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
        activityLog.log(.info, "Services started", detail: "Device: \(deviceName)")
    }

    /// Stops all network services.
    func stopServices() {
        // Stop auto-sync components first.
        Task {
            await stopAutoSyncInternal()
        }

        advertiser?.stop()
        browser?.stop()
        scanTimer?.invalidate()
        scanTimer = nil

        for (_, connection) in connections {
            connection.cancel()
        }
        connections.removeAll()
        helloSent.removeAll()
        peerCapabilities.removeAll()

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

        logger.info("Local config scan: \(hashes.count) files, fingerprint: \(fingerprint)")
    }

    // MARK: - Peer Discovery

    /// Handles a newly discovered peer from the browser.
    private func handlePeerDiscovered(_ peerInfo: PeerInfo) {
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

            if peer.status == .offline {
                peer.status = .discovered
            }

            // Quick sync check via fingerprint.
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

            if DiffEngine.areIdentical(
                localFingerprint: localFingerprint,
                remoteFingerprint: peerInfo.fingerprint
            ) {
                peer.status = .synced
                peer.differingFileCount = 0
            }

            peers.append(peer)
            activityLog.log(.peerDiscovered, "Discovered \(peerInfo.name)", detail: "\(peerInfo.platform) \u{00B7} \(peerInfo.configCount) configs")
        }

        logger.info("Peer updated: \(peerInfo.name) (\(peerInfo.deviceId.prefix(8)))")
    }

    /// Handles a peer that has disappeared from the network.
    private func handlePeerLost(deviceId: String) {
        if let index = peers.firstIndex(where: { $0.id == deviceId }) {
            let name = peers[index].name
            peers[index].status = .offline
            if let conn = connections.removeValue(forKey: deviceId) {
                conn.cancel()
            }
            activityLog.log(.peerLost, "\(name) went offline")
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
                    // Client sends hello first per PROTOCOL.md Section 3.3.
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
    /// When auto-sync is enabled, includes v2 capabilities in the hello.
    private func sendHello(via connection: SyncConnection) async {
        let capabilities: [String]? = isAutoSyncEnabled ? Self.autoSyncCapabilities : nil

        let hello = SyncProtocolCoder.makeHello(
            deviceId: DeviceIdentity.deviceId,
            deviceName: DeviceIdentity.deviceName,
            fileCount: localConfigCount,
            fingerprint: localFingerprint,
            capabilities: capabilities
        )

        do {
            try await connection.send(hello)
            helloSent.insert(connection.id)
            logger.info("Sent hello via \(connection.id) (capabilities: \(capabilities?.joined(separator: ", ") ?? "none"))")
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

        case .syncNotNeeded(let payload):
            logger.info("Sync not needed, fingerprint: \(payload.fingerprint)")
            // Find the peer and mark as synced.
            if let peerId = peerId, let peer = peers.first(where: { $0.id == peerId }) {
                peer.status = .synced
                peer.differingFileCount = 0
            }

        case .manifestRequest:
            handleManifestRequest(from: connection)

        case .manifest(let payload):
            handleManifest(payload, peerId: peerId)

        case .syncRequest(let payload):
            handleSyncRequest(payload, from: connection)

        case .syncAck:
            // Handled inline during sync operations.
            break

        case .file(let payload):
            Task {
                await handleFileTransfer(payload, from: connection)
            }

        case .fileAck:
            // Handled inline during push operations.
            break

        case .syncComplete(let payload):
            logger.info("Sync complete: \(payload.filesTransferred) files, direction: \(payload.direction)")
            syncInProgress = false

        case .statusRequest:
            handleStatusRequest(from: connection)

        case .status:
            // Status responses handled by caller.
            break

        case .error(let payload):
            logger.error("Error from peer: [\(payload.code)] \(payload.message)")
            lastError = "Peer error: \(payload.message)"
            syncInProgress = false

        // MARK: v2 Auto-Sync Message Handling

        case .subscribe(let payload):
            handleSubscribe(payload, from: connection, peerId: peerId)

        case .subscribeAck(let payload):
            handleSubscribeAck(payload, peerId: peerId)

        case .fileChanged(let payload):
            Task {
                await handleFileChanged(payload, from: connection, peerId: peerId)
            }

        case .fileChangedAck:
            // Handled by the broadcast sender; logged for diagnostics.
            break

        case .keepalive:
            // Update last-seen in the connection pool.
            if let peerId = peerId {
                Task {
                    await connectionPool?.markMessageReceived(from: peerId)
                }
            }
        }
    }

    /// Handles a hello message from a peer.
    private func handleHello(_ payload: HelloMessage, from connection: SyncConnection) {
        logger.info("Received hello from \(payload.name) (\(payload.deviceId.prefix(8)))")

        // Check protocol version compatibility per PROTOCOL.md Section 4.1.
        if payload.protocolVersion != 1 {
            let errorMsg = SyncProtocolCoder.makeError(
                code: "version_mismatch",
                message: "This device speaks protocol version 1, but received version \(payload.protocolVersion). Please update the app."
            )
            connection.send(errorMsg) { _ in }
            connection.cancel()
            return
        }

        // Associate this connection with the peer.
        if let peer = peers.first(where: { $0.id == payload.deviceId }) {
            peer.status = .connected
            peer.name = payload.name
            peer.configCount = payload.fileCount
            peer.fingerprint = payload.fingerprint
            peer.platform = payload.platform
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
            // New peer we didn't know about yet (connected to us before we discovered them).
            let peer = Peer(
                id: payload.deviceId,
                name: payload.name,
                platform: payload.platform,
                configCount: payload.fileCount,
                fingerprint: payload.fingerprint,
                protocolVersion: payload.protocolVersion,
                status: .connected,
                lastSeen: Date()
            )
            peers.append(peer)
            connections[peer.id] = connection
        }

        // Send hello back if we haven't already on this connection.
        if !helloSent.contains(connection.id) {
            Task {
                await sendHello(via: connection)
            }
        }

        // v2: Store remote capabilities and negotiate auto-sync if both peers support it.
        if let remoteCapabilities = payload.capabilities {
            peerCapabilities[payload.deviceId] = remoteCapabilities
        }

        if isAutoSyncEnabled, peerSupportsAutoSync(payload.deviceId) {
            Task {
                await setupAutoSyncWithPeer(
                    peerId: payload.deviceId,
                    connection: connection,
                    peer: peers.first(where: { $0.id == payload.deviceId })
                )
            }
        }
    }

    /// Handles a manifest request from a peer.
    private func handleManifestRequest(from connection: SyncConnection) {
        Task {
            // Build manifest entries with full metadata per PROTOCOL.md Section 4.2.
            let entries = await scanner.scanForManifest()
            let response = SyncProtocolCoder.makeManifest(files: entries)

            do {
                try await connection.send(response)
                logger.info("Sent manifest with \(entries.count) files")
            } catch {
                logger.error("Failed to send manifest: \(error.localizedDescription)")
            }
        }
    }

    /// Handles a received manifest from a peer.
    private func handleManifest(_ payload: ManifestMessage, peerId: String?) {
        guard let peerId = peerId,
              let peer = peers.first(where: { $0.id == peerId }) else {
            return
        }

        // Convert manifest entries to path -> hash for internal comparison.
        var hashMap: [String: String] = [:]
        for entry in payload.files {
            hashMap[entry.path] = entry.sha256
        }

        peer.remoteManifest = hashMap
        peer.configCount = payload.files.count

        // Compute differences.
        let diffCount = DiffEngine.differenceCount(
            local: localHashes,
            remote: hashMap
        )
        peer.differingFileCount = diffCount

        if diffCount == 0 {
            peer.status = .synced
        } else {
            peer.status = .connected
        }

        logger.info("Received manifest from \(peer.name): \(payload.files.count) files, \(diffCount) differences")
    }

    /// Handles an incoming sync request from a peer.
    private func handleSyncRequest(_ payload: SyncRequestMessage, from connection: SyncConnection) {
        // Check if another sync is already in progress per PROTOCOL.md Section 3.4.
        if syncInProgress {
            let reject = SyncProtocolCoder.makeSyncAck(
                accepted: false,
                reason: "Another sync operation is already in progress"
            )
            connection.send(reject) { _ in }
            return
        }

        syncInProgress = true

        // Accept the sync request.
        let accept = SyncProtocolCoder.makeSyncAck(accepted: true)
        connection.send(accept) { [weak self] error in
            guard error == nil else { return }

            // If this is a pull request, the remote wants files FROM us.
            if payload.direction == "pull" {
                Task { @MainActor [weak self] in
                    guard let self = self else { return }
                    await self.handlePullRequest(files: payload.files, via: connection)
                }
            }
            // If push, the remote will send us files — handled by the message loop.
        }
    }

    /// Handles a pull request by sending requested files to the peer.
    private func handlePullRequest(files: [String], via connection: SyncConnection) async {
        do {
            let count = try await syncEngine.pushFiles(
                files,
                localHashes: localHashes,
                via: connection
            )
            logger.info("Served pull request: \(count) files sent")
        } catch {
            logger.error("Failed to serve pull request: \(error.localizedDescription)")
        }
        syncInProgress = false
    }

    /// Handles an incoming file transfer (we are the receiver).
    private func handleFileTransfer(_ payload: FileMessage, from connection: SyncConnection) async {
        // Decode the base64 content.
        guard let fileData = Data(base64Encoded: payload.contentBase64) else {
            let ack = SyncProtocolCoder.makeFileAck(
                path: payload.path,
                success: false,
                error: "checksum_mismatch"
            )
            try? await connection.send(ack)
            return
        }

        // Write the file.
        do {
            try await scanner.writeFile(data: fileData, relativePath: payload.path)

            let ack = SyncProtocolCoder.makeFileAck(path: payload.path, success: true)
            try? await connection.send(ack)
            logger.info("Received and wrote file: \(payload.path)")
        } catch {
            let ack = SyncProtocolCoder.makeFileAck(
                path: payload.path,
                success: false,
                error: error.localizedDescription
            )
            try? await connection.send(ack)
            logger.error("Failed to write received file: \(error.localizedDescription)")
        }
    }

    /// Handles a status request from a peer.
    private func handleStatusRequest(from connection: SyncConnection) {
        let status = SyncProtocolCoder.makeStatus(
            deviceId: DeviceIdentity.deviceId,
            name: DeviceIdentity.deviceName,
            uptimeSeconds: Int(ProcessInfo.processInfo.systemUptime),
            lastSyncTimestamp: 0,
            fileCount: localConfigCount,
            fingerprint: localFingerprint
        )
        connection.send(status) { _ in }
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

    /// Pushes differing files to a connected peer per PROTOCOL.md Section 5.4.
    func pushToPeer(_ peer: Peer) async {
        guard let connection = connections[peer.id],
              let remoteManifest = peer.remoteManifest else {
            lastError = "Cannot push: no connection or manifest for \(peer.name)"
            return
        }

        peer.status = .syncing
        syncInProgress = true

        let filesToPush = DiffEngine.filesToPush(local: localHashes, remote: remoteManifest)

        guard !filesToPush.isEmpty else {
            peer.status = .synced
            peer.differingFileCount = 0
            syncInProgress = false
            return
        }

        // Send sync_request.
        let syncRequest = SyncProtocolCoder.makeSyncRequest(direction: "push", files: filesToPush)

        do {
            try await connection.send(syncRequest)

            // Wait for sync_ack.
            let ackMsg = try await connection.receiveMessage()
            guard case .syncAck(let ack) = ackMsg, ack.accepted else {
                let reason = (ackMsg as? SyncAckMessage)?.reason ?? "Rejected"
                peer.status = .error
                lastError = "Push rejected: \(reason)"
                syncInProgress = false
                return
            }

            // Push files via the sync engine.
            let count = try await syncEngine.pushFiles(
                filesToPush,
                localHashes: localHashes,
                via: connection
            )

            logger.info("Pushed \(count) files to \(peer.name)")
            activityLog.log(.syncCompleted, "Pushed \(count) files to \(peer.name)")

            // Refresh state after push per PROTOCOL.md Section 5.7.
            await refreshLocalConfig()
            peer.status = .synced
            peer.differingFileCount = 0
        } catch {
            peer.status = .error
            lastError = "Push failed: \(error.localizedDescription)"
            logger.error("Push to \(peer.name) failed: \(error.localizedDescription)")
            activityLog.log(.syncFailed, "Push to \(peer.name) failed", detail: error.localizedDescription)
        }

        syncInProgress = false
    }

    /// Pulls differing files from a connected peer per PROTOCOL.md Section 5.5.
    func pullFromPeer(_ peer: Peer) async {
        guard let connection = connections[peer.id],
              let remoteManifest = peer.remoteManifest else {
            lastError = "Cannot pull: no connection or manifest for \(peer.name)"
            return
        }

        peer.status = .syncing
        syncInProgress = true

        let filesToPull = DiffEngine.filesToPull(local: localHashes, remote: remoteManifest)

        guard !filesToPull.isEmpty else {
            peer.status = .synced
            peer.differingFileCount = 0
            syncInProgress = false
            return
        }

        // Send sync_request asking the peer to send us files.
        let syncRequest = SyncProtocolCoder.makeSyncRequest(direction: "pull", files: filesToPull)

        do {
            try await connection.send(syncRequest)

            // Wait for sync_ack.
            let ackMsg = try await connection.receiveMessage()
            guard case .syncAck(let ack) = ackMsg, ack.accepted else {
                peer.status = .error
                lastError = "Pull rejected by peer"
                syncInProgress = false
                return
            }

            // Receive files via the sync engine.
            let count = try await syncEngine.receiveFiles(
                expectedFiles: filesToPull.count,
                via: connection
            )

            logger.info("Pulled \(count) files from \(peer.name)")
            activityLog.log(.syncCompleted, "Pulled \(count) files from \(peer.name)")

            // Refresh state after pull per PROTOCOL.md Section 5.7.
            await refreshLocalConfig()
            peer.status = .synced
            peer.differingFileCount = 0
        } catch {
            peer.status = .error
            lastError = "Pull failed: \(error.localizedDescription)"
            logger.error("Pull from \(peer.name) failed: \(error.localizedDescription)")
        }

        syncInProgress = false
    }

    /// Returns the file diffs between local and a specific peer.
    func diffsWithPeer(_ peer: Peer) -> [FileDiff] {
        guard let remoteManifest = peer.remoteManifest else {
            return []
        }
        return DiffEngine.compare(local: localHashes, remote: remoteManifest)
    }

    // MARK: - v2 Auto-Sync Lifecycle

    /// Enables live auto-sync: starts the file watcher, initializes the connection pool,
    /// and establishes persistent connections with all currently connected v2-capable peers.
    /// Replaces the 30s polling timer with real-time file watching.
    func startAutoSync() async {
        guard !isAutoSyncEnabled else {
            logger.info("Auto-sync already enabled")
            return
        }

        isAutoSyncEnabled = true

        // Initialize the connection pool.
        let pool = ConnectionPool()
        await pool.setOnPeerDead { [weak self] peerId in
            Task { @MainActor [weak self] in
                self?.handlePeerDead(peerId: peerId)
            }
        }
        connectionPool = pool

        // Start the file watcher.
        let watcher = FileWatcher { [weak self] changedPaths in
            Task { @MainActor [weak self] in
                await self?.handleLocalFilesChanged(changedPaths)
            }
        }
        fileWatcher = watcher
        await watcher.start()
        isWatching = true

        // Stop the polling timer since we now have real-time watching.
        scanTimer?.invalidate()
        scanTimer = nil

        // Set up auto-sync with all currently connected v2-capable peers.
        for peer in peers where peer.status == .connected || peer.status == .synced {
            if let connection = connections[peer.id], peerSupportsAutoSync(peer.id) {
                await setupAutoSyncWithPeer(peerId: peer.id, connection: connection, peer: peer)
            }
        }

        logger.info("Auto-sync enabled: file watcher active, connection pool initialized")
        activityLog.log(.watchingStarted, "Auto-sync enabled", detail: "Watching ~/.claude/ for changes")
    }

    /// Disables live auto-sync: stops the file watcher and tears down the connection pool.
    /// Restores the 30s polling timer for v1-style sync.
    func stopAutoSync() async {
        await stopAutoSyncInternal()
    }

    /// Internal implementation of stopAutoSync, callable from non-async contexts via Task.
    private func stopAutoSyncInternal() async {
        guard isAutoSyncEnabled else { return }

        isAutoSyncEnabled = false

        // Stop the file watcher.
        if let watcher = fileWatcher {
            await watcher.stop()
            fileWatcher = nil
            isWatching = false
        }

        // Tear down the connection pool.
        if let pool = connectionPool {
            await pool.removeAll()
            connectionPool = nil
        }

        peerCapabilities.removeAll()

        // Restore the 30s polling timer.
        scanTimer = Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in
                await self?.refreshLocalConfig()
            }
        }

        logger.info("Auto-sync disabled: restored 30s polling timer")
        activityLog.log(.watchingStopped, "Auto-sync disabled")
    }

    /// Toggles auto-sync on or off.
    func toggleAutoSync() async {
        if isAutoSyncEnabled {
            await stopAutoSync()
        } else {
            await startAutoSync()
        }
    }

    // MARK: - v2 Capability Negotiation

    /// Checks whether a peer supports the auto_sync capability.
    private func peerSupportsAutoSync(_ peerId: String) -> Bool {
        guard let capabilities = peerCapabilities[peerId] else { return false }
        return capabilities.contains("auto_sync")
    }

    /// Sets up a persistent auto-sync session with a v2-capable peer:
    /// adds the connection to the pool, sends a subscribe message, and
    /// keeps the connection alive instead of closing after sync_complete.
    private func setupAutoSyncWithPeer(peerId: String, connection: SyncConnection, peer: Peer?) async {
        guard let pool = connectionPool else { return }

        // Add to the connection pool with the peer's endpoint for reconnection.
        let endpoint = peer?.endpoint
        await pool.addConnection(peerId: peerId, connection: connection, endpoint: endpoint)

        // Send a subscribe message to start receiving file change notifications.
        let subscribeMsg = SyncProtocolCoder.makeSubscribe()
        do {
            try await connection.send(subscribeMsg)
            logger.info("Sent subscribe to peer \(peerId.prefix(8))")
        } catch {
            logger.error("Failed to send subscribe to \(peerId.prefix(8)): \(error.localizedDescription)")
        }
    }

    // MARK: - v2 Incoming Message Handlers

    /// Handles an incoming subscribe request from a peer.
    /// Sends subscribe_ack and marks the peer as subscribed in the connection pool.
    private func handleSubscribe(_ payload: SubscribeMessage, from connection: SyncConnection, peerId: String?) {
        // Accept all subscribe requests.
        let ack = SyncProtocolCoder.makeSubscribeAck(
            accepted: true,
            subscribedPaths: payload.paths
        )
        connection.send(ack) { [logger] error in
            if let error = error {
                logger.error("Failed to send subscribe_ack: \(error.localizedDescription)")
            }
        }

        // Mark this peer as subscribed in the connection pool.
        if let peerId = peerId {
            Task {
                await connectionPool?.markSubscribed(peerId: peerId)
            }
            logger.info("Peer \(peerId.prefix(8)) subscribed to paths: \(payload.paths)")
        }
    }

    /// Handles an incoming subscribe_ack from a peer.
    private func handleSubscribeAck(_ payload: SubscribeAckMessage, peerId: String?) {
        if payload.accepted {
            logger.info("Subscribe accepted by peer \(peerId?.prefix(8) ?? "unknown"): paths \(payload.subscribedPaths)")
            if let peerId = peerId {
                Task {
                    await connectionPool?.markSubscribed(peerId: peerId)
                }
            }
        } else {
            logger.warning("Subscribe rejected by peer \(peerId?.prefix(8) ?? "unknown")")
        }
    }

    /// Handles an incoming file_changed notification from a peer.
    /// Uses ConflictResolver if the previous_sha256 does not match the local hash.
    private func handleFileChanged(_ payload: FileChangedMessage, from connection: SyncConnection, peerId: String?) async {
        // Update connection pool last-seen timestamp.
        if let peerId = peerId {
            await connectionPool?.markMessageReceived(from: peerId)
        }

        // Handle deletion.
        if payload.change == .deleted {
            await handleFileDeleted(path: payload.path, from: connection)
            return
        }

        // For files >1MB where content is omitted, the receiver must pull via sync_request.
        guard let contentBase64 = payload.contentBase64,
              let remoteData = Data(base64Encoded: contentBase64) else {
            logger.info("file_changed for \(payload.path): content omitted (>1MB), need full sync")
            let ack = SyncProtocolCoder.makeFileChangedAck(
                path: payload.path,
                accepted: false,
                conflict: false
            )
            try? await connection.send(ack)
            return
        }

        // Check for conflict: does the previous_sha256 match our current local hash?
        let localHash = localHashes[payload.path]
        let hasConflict = payload.previousSha256 != nil
            && localHash != nil
            && payload.previousSha256 != localHash

        var dataToWrite: Data
        var isConflict = false

        if hasConflict {
            // Conflict detected: both sides modified the file since the last known state.
            logger.warning("Conflict detected on \(payload.path): local=\(localHash?.prefix(8) ?? "nil"), expected=\(payload.previousSha256?.prefix(8) ?? "nil")")

            // Read local file data for conflict resolution.
            guard let localData = await scanner.readFile(relativePath: payload.path) else {
                // Local file missing but hash recorded -- accept remote.
                dataToWrite = remoteData
                isConflict = true
                logger.info("Conflict on \(payload.path): local file missing, accepting remote")
                // Fall through to write.
                try? await writeAndAckFileChanged(
                    path: payload.path,
                    data: dataToWrite,
                    isConflict: isConflict,
                    executable: payload.executable,
                    connection: connection
                )
                return
            }

            let localTimestamp = Int64(Date().timeIntervalSince1970 * 1000)
            let resolution = ConflictResolver.resolve(
                path: payload.path,
                localData: localData,
                remoteData: remoteData,
                localTimestamp: localTimestamp,
                remoteTimestamp: payload.changeEpochMs,
                localDeviceId: DeviceIdentity.deviceId,
                remoteDeviceId: peerId ?? ""
            )

            switch resolution {
            case .acceptRemote(let data):
                dataToWrite = data
                isConflict = true
            case .keepLocal:
                // Do not write; acknowledge with conflict flag.
                let ack = SyncProtocolCoder.makeFileChangedAck(
                    path: payload.path,
                    accepted: false,
                    conflict: true
                )
                try? await connection.send(ack)
                return
            case .merge(let mergedData):
                dataToWrite = mergedData
                isConflict = true
            }
        } else {
            // No conflict: apply the remote change directly.
            dataToWrite = remoteData
        }

        await writeAndAckFileChanged(
            path: payload.path,
            data: dataToWrite,
            isConflict: isConflict,
            executable: payload.executable,
            connection: connection
        )
    }

    /// Writes a file from a file_changed message and sends the appropriate ack.
    private func writeAndAckFileChanged(
        path: String,
        data: Data,
        isConflict: Bool,
        executable: Bool,
        connection: SyncConnection
    ) async {
        do {
            try await scanner.writeFile(data: data, relativePath: path)
            logger.info("Applied file_changed: \(path) (\(data.count) bytes, conflict: \(isConflict))")

            // Refresh our local hashes for the changed file.
            await refreshLocalConfig()

            let ack = SyncProtocolCoder.makeFileChangedAck(
                path: path,
                accepted: true,
                conflict: isConflict
            )
            try? await connection.send(ack)
        } catch {
            logger.error("Failed to write file_changed \(path): \(error.localizedDescription)")
            let ack = SyncProtocolCoder.makeFileChangedAck(
                path: path,
                accepted: false,
                conflict: isConflict
            )
            try? await connection.send(ack)
        }
    }

    /// Handles a file deletion from a file_changed message.
    private func handleFileDeleted(path: String, from connection: SyncConnection) async {
        let baseDirectory = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".claude")
        let fileURL = baseDirectory.appendingPathComponent(path)

        do {
            if FileManager.default.fileExists(atPath: fileURL.path) {
                try FileManager.default.removeItem(at: fileURL)
                logger.info("Deleted file via file_changed: \(path)")
            }

            await refreshLocalConfig()

            let ack = SyncProtocolCoder.makeFileChangedAck(
                path: path,
                accepted: true,
                conflict: false
            )
            try? await connection.send(ack)
        } catch {
            logger.error("Failed to delete \(path): \(error.localizedDescription)")
            let ack = SyncProtocolCoder.makeFileChangedAck(
                path: path,
                accepted: false,
                conflict: false
            )
            try? await connection.send(ack)
        }
    }

    // MARK: - v2 File Watcher Callback

    /// Called when the file watcher detects local file changes.
    /// Builds file_changed messages for each changed file and broadcasts
    /// them to all subscribed peers via the connection pool.
    private func handleLocalFilesChanged(_ changedPaths: Set<String>) async {
        guard isAutoSyncEnabled, let pool = connectionPool else { return }

        let baseDirectory = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".claude")

        // Snapshot the current local hashes before we re-scan.
        let previousHashes = localHashes

        // Re-scan to get updated hashes.
        await refreshLocalConfig()

        let nowMs = Int64(Date().timeIntervalSince1970 * 1000)

        for relativePath in changedPaths {
            let fileURL = baseDirectory.appendingPathComponent(relativePath)
            let fileExists = FileManager.default.fileExists(atPath: fileURL.path)

            if !fileExists {
                // File was deleted.
                let msg = SyncProtocolCoder.makeFileChanged(
                    path: relativePath,
                    change: .deleted,
                    sha256: nil,
                    size: nil,
                    mtimeEpoch: nil,
                    changeEpochMs: nowMs,
                    previousSha256: previousHashes[relativePath],
                    contentBase64: nil,
                    executable: false
                )
                await pool.broadcast(msg)
                continue
            }

            // Read the file data.
            guard let fileData = try? Data(contentsOf: fileURL) else {
                logger.warning("Cannot read changed file: \(relativePath)")
                continue
            }

            let newHash = localHashes[relativePath] ?? ""
            let previousHash = previousHashes[relativePath]
            let changeType: FileChangeType = previousHash == nil ? .created : .modified

            // Skip if hash is unchanged (file might have been touched but not modified).
            if let prev = previousHash, prev == newHash {
                continue
            }

            let isExecutable = relativePath.hasSuffix(".sh") || relativePath.hasSuffix(".py")
            let mtimeEpoch: Int?
            do {
                let attrs = try FileManager.default.attributesOfItem(atPath: fileURL.path)
                if let mdate = attrs[.modificationDate] as? Date {
                    mtimeEpoch = Int(mdate.timeIntervalSince1970)
                } else {
                    mtimeEpoch = nil
                }
            } catch {
                mtimeEpoch = nil
            }

            // For files >1MB, omit content and let the receiver pull via sync_request.
            let maxInlineSize = 1_048_576 // 1MB
            let contentBase64: String? = fileData.count <= maxInlineSize
                ? fileData.base64EncodedString()
                : nil

            let msg = SyncProtocolCoder.makeFileChanged(
                path: relativePath,
                change: changeType,
                sha256: newHash,
                size: fileData.count,
                mtimeEpoch: mtimeEpoch,
                changeEpochMs: nowMs,
                previousSha256: previousHash,
                contentBase64: contentBase64,
                executable: isExecutable
            )

            await pool.broadcast(msg)
            logger.info("Broadcast file_changed: \(relativePath) (\(changeType.rawValue))")
            activityLog.log(.fileChangeDetected, "File changed: \(relativePath)", detail: changeType.rawValue)
        }
    }

    // MARK: - v2 Reconnection

    /// Called when the connection pool detects a dead peer.
    /// Attempts reconnection using the peer's known endpoint with exponential backoff.
    private func handlePeerDead(peerId: String) {
        guard let peer = peers.first(where: { $0.id == peerId }),
              let endpoint = peer.endpoint else {
            logger.warning("Cannot reconnect to \(peerId.prefix(8)): no endpoint")
            return
        }

        Task {
            guard let pool = connectionPool else { return }
            let delay = await pool.reconnectDelay(for: peerId)

            logger.info("Scheduling reconnect to \(peerId.prefix(8)) in \(delay)s")

            try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))

            guard isAutoSyncEnabled else { return }

            // Attempt reconnection.
            let newConnection = SyncConnection(to: endpoint, id: "autosync-\(peerId.prefix(8))")

            newConnection.onMessage = { [weak self] message in
                Task { @MainActor [weak self] in
                    self?.handleMessage(message, from: newConnection, peerId: peerId)
                }
            }

            newConnection.onStateChange = { [weak self] state in
                Task { @MainActor [weak self] in
                    guard let self = self else { return }
                    switch state {
                    case .ready:
                        peer.status = .connected
                        self.connections[peerId] = newConnection
                        await self.sendHello(via: newConnection)
                    case .failed:
                        self.logger.warning("Reconnect to \(peerId.prefix(8)) failed")
                    default:
                        break
                    }
                }
            }

            newConnection.start()
        }
    }

    // MARK: - Tracker Connection Management

    /// Connects to a tracker server for WAN peer discovery.
    /// Creates a TrackerClient, connects, registers, and starts listening for peer events.
    func connectToTracker(url: URL) async {
        // Disconnect any existing tracker connection first.
        await disconnectFromTracker()

        let client = TrackerClient(
            trackerURL: url,
            deviceId: DeviceIdentity.deviceId,
            deviceName: DeviceIdentity.deviceName
        )

        // Configure callbacks for peer presence and relay data events.
        await client.setCallbacks(
            onPeerOnline: { [weak self] peer in
                Task { @MainActor [weak self] in
                    self?.handleTrackerPeerOnline(peer: peer)
                }
            },
            onPeerOffline: { [weak self] deviceId in
                Task { @MainActor [weak self] in
                    self?.handleTrackerPeerOffline(deviceId: deviceId)
                }
            },
            onRelayData: { [weak self] relayId, fromDeviceId, payloadBase64 in
                Task { @MainActor [weak self] in
                    await self?.handleTrackerRelayData(
                        relayId: relayId,
                        fromDeviceId: fromDeviceId,
                        payloadBase64: payloadBase64
                    )
                }
            },
            onStateChange: { [weak self] newState in
                Task { @MainActor [weak self] in
                    self?.isTrackerConnected = (newState == .registered || newState == .connected)
                    // If disconnected unexpectedly, schedule reconnection.
                    if newState == .disconnected {
                        self?.scheduleTrackerReconnect(url: url)
                    }
                }
            }
        )

        trackerClient = client

        do {
            try await client.connect()
            isTrackerConnected = true
            trackerReconnectDelay = 2.0 // Reset backoff on successful connect.
            logger.info("Connected to tracker: \(url.absoluteString)")
            activityLog.log(.trackerConnected, "Connected to tracker", detail: url.host ?? url.absoluteString)

            // Request the initial peer list.
            let peerList = try await client.requestPeerList()
            for peerInfo in peerList {
                handleTrackerPeerOnline(peer: peerInfo)
            }
        } catch {
            logger.error("Failed to connect to tracker: \(error.localizedDescription)")
            activityLog.log(.trackerError, "Tracker connection failed", detail: error.localizedDescription)
            lastError = "Tracker connection failed: \(error.localizedDescription)"
            isTrackerConnected = false
            scheduleTrackerReconnect(url: url)
        }
    }

    /// Disconnects from the tracker server and cleans up relay connections.
    func disconnectFromTracker() async {
        trackerReconnectTask?.cancel()
        trackerReconnectTask = nil

        if let client = trackerClient {
            await client.disconnect()
            trackerClient = nil
        }

        // Close all relay connections.
        for (_, relay) in relayConnections {
            await relay.close()
        }
        relayConnections.removeAll()
        peerRelayMap.removeAll()

        isTrackerConnected = false
        wanPeers.removeAll()
        logger.info("Disconnected from tracker")
        activityLog.log(.trackerDisconnected, "Disconnected from tracker")
    }

    /// Schedules a tracker reconnection attempt with exponential backoff.
    private func scheduleTrackerReconnect(url: URL) {
        guard trackerReconnectTask == nil else { return }

        let delay = trackerReconnectDelay
        trackerReconnectDelay = min(trackerReconnectDelay * 2.0, Self.maxTrackerReconnectDelay)

        logger.info("Scheduling tracker reconnect in \(delay)s")

        trackerReconnectTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
            guard !Task.isCancelled else { return }

            await self?.connectToTracker(url: url)
            await MainActor.run {
                self?.trackerReconnectTask = nil
            }
        }
    }

    /// Connects all enabled trackers from the sync configuration.
    func connectConfiguredTrackers() async {
        for tracker in syncConfig.trackers where tracker.enabled {
            guard let url = URL(string: tracker.url) else {
                logger.warning("Invalid tracker URL: \(tracker.url)")
                continue
            }
            // Currently one active tracker at a time. Connect to the first enabled one.
            await connectToTracker(url: url)
            break
        }
    }

    // MARK: - WAN Peer Presence Events

    /// Handles a peer coming online on the tracker.
    /// Creates or updates a WAN Peer entry and triggers reconnection if the peer
    /// was previously offline (immediate reconnect, skipping backoff).
    private func handleTrackerPeerOnline(peer: TrackerPeerInfo) {
        // Skip our own device.
        guard peer.deviceId != DeviceIdentity.deviceId else { return }

        if let existingIndex = wanPeers.firstIndex(where: { $0.id == peer.deviceId }) {
            let existing = wanPeers[existingIndex]
            existing.name = peer.name
            existing.platform = peer.platform
            existing.configCount = peer.fileCount
            existing.fingerprint = peer.fingerprint
            existing.lastSeen = Date()
            if existing.status == .offline {
                existing.status = .discovered
                // Peer came back online -- trigger immediate reconnect (skip backoff).
                Task {
                    await connectToWanPeer(peer: peer)
                }
            }
        } else {
            let newPeer = Peer(
                id: peer.deviceId,
                name: peer.name,
                platform: peer.platform,
                configCount: peer.fileCount,
                fingerprint: peer.fingerprint,
                status: .discovered,
                lastSeen: Date()
            )
            wanPeers.append(newPeer)
        }

        logger.info("WAN peer online: \(peer.name) (\(peer.deviceId.prefix(8)))")
    }

    /// Handles a peer going offline on the tracker.
    private func handleTrackerPeerOffline(deviceId: String) {
        if let index = wanPeers.firstIndex(where: { $0.id == deviceId }) {
            wanPeers[index].status = .offline
        }

        // Clean up relay connection if one exists.
        if let relayId = peerRelayMap.removeValue(forKey: deviceId) {
            if let relay = relayConnections.removeValue(forKey: relayId) {
                Task { await relay.close() }
            }
        }

        logger.info("WAN peer offline: \(deviceId.prefix(8))")
    }

    /// Routes incoming relay data to the appropriate RelayConnection.
    private func handleTrackerRelayData(
        relayId: String,
        fromDeviceId: String,
        payloadBase64: String
    ) async {
        if let relay = relayConnections[relayId] {
            await relay.handleIncomingRelayData(payloadBase64: payloadBase64)
        } else {
            // Incoming relay from a peer we haven't set up yet -- create on-the-fly.
            guard let client = trackerClient else { return }

            let relay = RelayConnection(
                trackerClient: client,
                relayId: relayId,
                remoteDeviceId: fromDeviceId
            )
            relayConnections[relayId] = relay
            peerRelayMap[fromDeviceId] = relayId

            await relay.handleIncomingRelayData(payloadBase64: payloadBase64)
            logger.info("Created incoming relay: \(relayId.prefix(8)) from \(fromDeviceId.prefix(8))")
        }
    }

    // MARK: - WAN Peer Connection

    /// Connects to a WAN peer. Tries direct TCP first (5s timeout),
    /// then falls back to relay through the tracker.
    func connectToWanPeer(peer: TrackerPeerInfo) async {
        guard let wanPeer = wanPeers.first(where: { $0.id == peer.deviceId }) else { return }
        wanPeer.status = .connecting

        // Attempt 1: Direct TCP to the peer's public address.
        if !peer.publicAddr.isEmpty {
            let directSuccess = await attemptDirectConnection(
                to: peer.publicAddr,
                peerId: peer.deviceId,
                timeout: 5.0
            )
            if directSuccess {
                wanPeer.status = .connected
                logger.info("Direct WAN connection to \(peer.name) succeeded")
                return
            }
        }

        // Attempt 2: Relay through the tracker.
        guard let client = trackerClient else {
            wanPeer.status = .error
            lastError = "No tracker connection for relay to \(peer.name)"
            return
        }

        do {
            let relayId = try await client.requestRelay(targetDeviceId: peer.deviceId)
            let relay = RelayConnection(
                trackerClient: client,
                relayId: relayId,
                remoteDeviceId: peer.deviceId
            )
            relayConnections[relayId] = relay
            peerRelayMap[peer.deviceId] = relayId
            wanPeer.status = .connected

            // Send hello through the relay to initiate the sync protocol.
            let hello = SyncProtocolCoder.makeHello(
                deviceId: DeviceIdentity.deviceId,
                deviceName: DeviceIdentity.deviceName,
                fileCount: localConfigCount,
                fingerprint: localFingerprint,
                capabilities: isAutoSyncEnabled ? Self.autoSyncCapabilities : nil
            )
            try await relay.send(hello)

            logger.info("Relay connection to \(peer.name) established (relay: \(relayId.prefix(8)))")
        } catch {
            wanPeer.status = .error
            lastError = "Failed to relay to \(peer.name): \(error.localizedDescription)"
            logger.error("Relay to \(peer.name) failed: \(error.localizedDescription)")
        }
    }

    /// Attempts a direct TCP connection to a WAN peer's public address.
    /// Returns true if the connection succeeds within the timeout.
    private func attemptDirectConnection(
        to addressString: String,
        peerId: String,
        timeout: TimeInterval
    ) async -> Bool {
        let components = addressString.split(separator: ":")
        guard components.count == 2,
              let portValue = UInt16(components[1]),
              let port = NWEndpoint.Port(rawValue: portValue) else {
            return false
        }

        let host = NWEndpoint.Host(String(components[0]))
        let endpoint = NWEndpoint.hostPort(host: host, port: port)
        let connection = SyncConnection(to: endpoint, id: "wan-direct-\(peerId.prefix(8))")

        return await withCheckedContinuation { (continuation: CheckedContinuation<Bool, Never>) in
            var hasResumed = false

            let timeoutTask = Task {
                try? await Task.sleep(nanoseconds: UInt64(timeout * 1_000_000_000))
                if !hasResumed {
                    hasResumed = true
                    connection.cancel()
                    continuation.resume(returning: false)
                }
            }

            connection.onStateChange = { [weak self] state in
                guard !hasResumed else { return }
                switch state {
                case .ready:
                    hasResumed = true
                    timeoutTask.cancel()
                    Task { @MainActor [weak self] in
                        self?.connections[peerId] = connection
                        connection.onMessage = { [weak self] message in
                            Task { @MainActor [weak self] in
                                self?.handleMessage(message, from: connection, peerId: peerId)
                            }
                        }
                    }
                    continuation.resume(returning: true)
                case .failed, .cancelled:
                    hasResumed = true
                    timeoutTask.cancel()
                    continuation.resume(returning: false)
                default:
                    break
                }
            }

            connection.start()
        }
    }

    // MARK: - WAN Auto-Healing

    /// Handles a WAN connection drop. The tracker will notify us via peer_online
    /// when the peer reconnects, triggering immediate reconnection (skip backoff).
    private func handleWanPeerDisconnected(peerId: String) {
        guard let peer = wanPeers.first(where: { $0.id == peerId }) else { return }
        peer.status = .offline

        // Clean up relay.
        if let relayId = peerRelayMap.removeValue(forKey: peerId) {
            if let relay = relayConnections.removeValue(forKey: relayId) {
                Task { await relay.close() }
            }
        }

        logger.info("WAN peer disconnected: \(peerId.prefix(8)), waiting for tracker re-notification")
    }

    // MARK: - Sync Config Management

    /// Reloads the sync configuration from disk and applies changes.
    func reloadSyncConfig() async {
        syncConfig = SyncConfigLoader.load()

        if syncConfig.trackers.contains(where: { $0.enabled }) {
            await connectConfiguredTrackers()
        } else {
            await disconnectFromTracker()
        }

        logger.info("Sync configuration reloaded")
    }

    /// Saves the current sync configuration to disk.
    func saveSyncConfig() {
        do {
            try SyncConfigLoader.save(syncConfig)
        } catch {
            logger.error("Failed to save sync config: \(error.localizedDescription)")
            lastError = "Failed to save configuration: \(error.localizedDescription)"
        }
    }

    /// Adds a tracker to the configuration and connects to it.
    func addTracker(name: String, url: String) async {
        let tracker = SyncConfig.TrackerConfig(url: url, name: name, enabled: true)
        syncConfig.trackers.append(tracker)
        saveSyncConfig()

        if let trackerURL = URL(string: url) {
            await connectToTracker(url: trackerURL)
        }
    }

    /// Removes a tracker from the configuration.
    func removeTracker(url: String) async {
        syncConfig.trackers.removeAll { $0.url == url }
        saveSyncConfig()

        // If the removed tracker was active, disconnect and try the next one.
        if trackerClient != nil {
            await disconnectFromTracker()
            await connectConfiguredTrackers()
        }
    }

    /// Toggles a tracker's enabled state and persists the change.
    func toggleTracker(url: String) {
        guard let index = syncConfig.trackers.firstIndex(where: { $0.url == url }) else { return }
        syncConfig.trackers[index].enabled.toggle()
        saveSyncConfig()
    }
}
