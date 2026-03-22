// ServiceBrowser.swift
// ClaudeSync
//
// Uses NWBrowser to discover other ClaudeSync instances on the local network.
// Monitors for `_claude-sync._tcp` services and extracts metadata from
// the Bonjour TXT record. Filters out self by device_id to prevent
// connecting to our own instance.

import Foundation
import Network
import os

/// Discovers ClaudeSync peers on the local network using Bonjour (NWBrowser).
/// Publishes discovered peers and notifies when peers appear/disappear.
final class ServiceBrowser: @unchecked Sendable {

    // MARK: - Properties

    /// The NWBrowser instance that scans for services.
    private var browser: NWBrowser?

    /// Queue for browser events.
    private let queue = DispatchQueue(label: "com.claudesync.browser")

    /// Logger for browser events.
    private let logger = Logger(subsystem: "com.claudesync", category: "ServiceBrowser")

    /// Our own device ID, used to filter self from results.
    private let localDeviceId: String

    /// Whether the browser is actively scanning.
    private(set) var isBrowsing = false

    /// Callback invoked when a new peer is discovered.
    var onPeerDiscovered: ((PeerInfo) -> Void)?

    /// Callback invoked when a peer disappears from the network.
    var onPeerLost: ((String) -> Void)?

    /// Callback for browser state changes.
    var onStateChange: ((NWBrowser.State) -> Void)?

    /// Currently known browser results, keyed by a derived identifier.
    private var currentResults: [String: NWBrowser.Result] = [:]

    // MARK: - Initialization

    /// Creates a browser that will filter out the given local device ID.
    init(localDeviceId: String) {
        self.localDeviceId = localDeviceId
    }

    // MARK: - Lifecycle

    /// Starts browsing for ClaudeSync peers on the local network.
    func start() {
        guard !isBrowsing else {
            logger.warning("Browser already running, ignoring start()")
            return
        }

        let descriptor = NWBrowser.Descriptor.bonjour(
            type: ServiceAdvertiser.serviceType,
            domain: "local."
        )

        browser = NWBrowser(for: descriptor, using: .claudeSync)

        guard let browser = browser else { return }

        browser.stateUpdateHandler = { [weak self] state in
            guard let self = self else { return }
            self.logger.debug("Browser state: \(String(describing: state))")

            switch state {
            case .ready:
                self.isBrowsing = true
            case .failed(let error):
                self.logger.error("Browser failed: \(error.localizedDescription)")
                self.isBrowsing = false
                // Attempt to restart after a delay.
                self.queue.asyncAfter(deadline: .now() + 2.0) { [weak self] in
                    self?.browser?.cancel()
                    self?.start()
                }
            case .cancelled:
                self.isBrowsing = false
            default:
                break
            }

            self.onStateChange?(state)
        }

        browser.browseResultsChangedHandler = { [weak self] results, changes in
            guard let self = self else { return }
            self.handleResultsChanged(results: results, changes: changes)
        }

        browser.start(queue: queue)
    }

    /// Stops browsing for peers.
    func stop() {
        browser?.cancel()
        browser = nil
        isBrowsing = false
        currentResults.removeAll()
        logger.info("Browser stopped")
    }

    // MARK: - Results Handling

    /// Processes changes in the set of discovered Bonjour services.
    private func handleResultsChanged(
        results: Set<NWBrowser.Result>,
        changes: Set<NWBrowser.Result.Change>
    ) {
        for change in changes {
            switch change {
            case .added(let result):
                handlePeerAdded(result)

            case .removed(let result):
                handlePeerRemoved(result)

            case .changed(old: _, new: let newResult, flags: _):
                // Treat a changed result as a re-add (metadata may have updated).
                handlePeerAdded(newResult)

            case .identical:
                break

            @unknown default:
                break
            }
        }
    }

    /// Processes a newly discovered or updated peer.
    private func handlePeerAdded(_ result: NWBrowser.Result) {
        guard let peerInfo = extractPeerInfo(from: result) else {
            logger.debug("Skipping result with no parseable TXT record")
            return
        }

        // Filter out ourselves.
        guard peerInfo.deviceId != localDeviceId else {
            logger.debug("Filtered out self from browser results")
            return
        }

        let key = resultKey(for: result)
        currentResults[key] = result

        logger.info("Discovered peer: \(peerInfo.name) (\(peerInfo.deviceId.prefix(8)))")
        onPeerDiscovered?(peerInfo)
    }

    /// Processes a peer that has disappeared from the network.
    private func handlePeerRemoved(_ result: NWBrowser.Result) {
        let key = resultKey(for: result)
        currentResults.removeValue(forKey: key)

        // Try to extract the device_id from the result's TXT record.
        if let peerInfo = extractPeerInfo(from: result) {
            guard peerInfo.deviceId != localDeviceId else { return }
            logger.info("Lost peer: \(peerInfo.name) (\(peerInfo.deviceId.prefix(8)))")
            onPeerLost?(peerInfo.deviceId)
        } else {
            // If we cannot extract the device_id, use the result key as fallback.
            logger.info("Lost unidentified peer: \(key)")
            onPeerLost?(key)
        }
    }

    // MARK: - TXT Record Parsing

    /// Extracts peer metadata from a browser result's Bonjour TXT record.
    private func extractPeerInfo(from result: NWBrowser.Result) -> PeerInfo? {
        // Extract the TXT record from the result metadata.
        guard case .service(let name, let type, let domain, _) = result.endpoint else {
            return nil
        }

        // Access the TXT record from the result's metadata.
        let txtRecord: NWTXTRecord?
        if case .bonjour(let record) = result.metadata {
            txtRecord = record
        } else {
            txtRecord = nil
        }

        guard let txt = txtRecord else {
            return nil
        }

        // Extract fields from TXT record per PROTOCOL.md Section 2.2.
        // The `id` field is required.
        guard let deviceId = txt["id"] else {
            logger.debug("TXT record missing id for service: \(name)")
            return nil
        }

        let peerName = txt["name"] ?? name
        let platform = txt["platform"] ?? "unknown"
        let configCountStr = txt["configs"] ?? "0"
        let configCount = Int(configCountStr) ?? 0
        let fingerprint = txt["fingerprint"] ?? ""
        let versionStr = txt["v"] ?? "1"
        let version = Int(versionStr) ?? 1

        return PeerInfo(
            deviceId: deviceId,
            name: peerName,
            platform: platform,
            configCount: configCount,
            fingerprint: fingerprint,
            protocolVersion: version,
            endpoint: result.endpoint,
            browserResult: result,
            serviceName: name,
            serviceType: type,
            serviceDomain: domain
        )
    }

    /// Generates a stable key for a browser result for tracking purposes.
    private func resultKey(for result: NWBrowser.Result) -> String {
        switch result.endpoint {
        case .service(let name, let type, let domain, _):
            return "\(name).\(type).\(domain)"
        default:
            return result.endpoint.debugDescription
        }
    }
}

// MARK: - PeerInfo

/// Parsed metadata about a discovered peer, extracted from the Bonjour TXT record.
/// This is a lightweight value type used to communicate discovery events.
struct PeerInfo: Sendable {
    let deviceId: String
    let name: String
    let platform: String
    let configCount: Int
    let fingerprint: String
    let protocolVersion: Int
    let endpoint: NWEndpoint?
    let browserResult: NWBrowser.Result?
    let serviceName: String
    let serviceType: String
    let serviceDomain: String
}
