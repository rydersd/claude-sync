// ServiceAdvertiser.swift
// ClaudeSync
//
// Uses NWListener to advertise this machine on the local network
// via Bonjour as `_claude-sync._tcp`. The TXT record carries metadata
// (device ID, name, config count, fingerprint) so browsers can display
// peer info before establishing a connection.

import Foundation
import Network
import os

/// Advertises this ClaudeSync instance on the local network via Bonjour.
/// Creates a TCP listener on a dynamic port and registers the service
/// with metadata in the TXT record.
final class ServiceAdvertiser: @unchecked Sendable {

    // MARK: - Properties

    /// The NWListener that accepts incoming peer connections.
    private var listener: NWListener?

    /// Queue for listener events.
    private let queue = DispatchQueue(label: "com.claudesync.advertiser")

    /// Logger for advertiser lifecycle events.
    private let logger = Logger(subsystem: "com.claudesync", category: "ServiceAdvertiser")

    /// The Bonjour service type used for discovery.
    static let serviceType = "_claude-sync._tcp"

    /// Callback invoked when a new peer connects (incoming connection).
    /// The caller is responsible for retaining the SyncConnection.
    var onIncomingConnection: ((SyncConnection) -> Void)?

    /// Callback for advertiser state changes.
    var onStateChange: ((NWListener.State) -> Void)?

    /// Whether the advertiser is currently running.
    private(set) var isAdvertising = false

    /// The port the listener is bound to, available after start.
    var port: NWEndpoint.Port? {
        listener?.port
    }

    // MARK: - TXT Record Metadata

    /// The device identity to include in the TXT record.
    private let deviceId: String
    private let deviceName: String

    /// Mutable metadata that updates as config state changes.
    private var configCount: Int = 0
    private var fingerprint: String = ""

    // MARK: - Initialization

    /// Creates an advertiser with the given device identity.
    /// - Parameters:
    ///   - deviceId: The persistent UUID for this device.
    ///   - deviceName: Human-readable device name.
    init(deviceId: String, deviceName: String) {
        self.deviceId = deviceId
        self.deviceName = deviceName
    }

    // MARK: - Lifecycle

    /// Starts advertising on the network. Creates a TCP listener on a dynamic port
    /// and registers the Bonjour service.
    func start() {
        guard !isAdvertising else {
            logger.warning("Advertiser already running, ignoring start()")
            return
        }

        do {
            // Create a listener with the ClaudeSync framing protocol.
            listener = try NWListener(using: .claudeSync)
        } catch {
            logger.error("Failed to create NWListener: \(error.localizedDescription)")
            return
        }

        guard let listener = listener else { return }

        // Configure Bonjour advertisement with service type and TXT record.
        let txtRecord = buildTXTRecord()
        listener.service = NWListener.Service(
            type: Self.serviceType,
            txtRecord: txtRecord
        )

        // Handle listener state changes.
        listener.stateUpdateHandler = { [weak self] state in
            guard let self = self else { return }
            self.logger.debug("Listener state: \(String(describing: state))")

            switch state {
            case .ready:
                if let port = self.listener?.port {
                    self.logger.info("Listening on port \(port.rawValue)")
                }
                self.isAdvertising = true
            case .failed(let error):
                self.logger.error("Listener failed: \(error.localizedDescription)")
                self.isAdvertising = false
                // Attempt to restart after a short delay.
                self.queue.asyncAfter(deadline: .now() + 2.0) { [weak self] in
                    self?.listener?.cancel()
                    self?.start()
                }
            case .cancelled:
                self.isAdvertising = false
            default:
                break
            }

            self.onStateChange?(state)
        }

        // Handle incoming connections from peers.
        listener.newConnectionHandler = { [weak self] nwConnection in
            guard let self = self else { return }
            self.logger.info("Incoming connection from: \(String(describing: nwConnection.endpoint))")

            let syncConnection = SyncConnection(
                connection: nwConnection,
                id: "incoming-\(UUID().uuidString.prefix(8))"
            )
            self.onIncomingConnection?(syncConnection)
        }

        listener.start(queue: queue)
    }

    /// Stops advertising and tears down the listener.
    func stop() {
        listener?.cancel()
        listener = nil
        isAdvertising = false
        logger.info("Advertiser stopped")
    }

    // MARK: - TXT Record

    /// Updates the config metadata in the TXT record. Call this when the
    /// local config state changes (file added/removed, etc.).
    func updateMetadata(configCount: Int, fingerprint: String) {
        self.configCount = configCount
        self.fingerprint = fingerprint

        // Update the TXT record on the running listener.
        if let listener = listener, isAdvertising {
            let txtRecord = buildTXTRecord()
            listener.service = NWListener.Service(
                type: Self.serviceType,
                txtRecord: txtRecord
            )
        }
    }

    /// Builds the Bonjour TXT record dictionary containing peer metadata.
    private func buildTXTRecord() -> NWTXTRecord {
        var txtRecord = NWTXTRecord()
        txtRecord["version"] = "1"
        txtRecord["device_id"] = deviceId
        txtRecord["name"] = deviceName
        txtRecord["configs"] = String(configCount)
        txtRecord["fingerprint"] = String(fingerprint.prefix(16))
        txtRecord["platform"] = "macOS"
        return txtRecord
    }
}
