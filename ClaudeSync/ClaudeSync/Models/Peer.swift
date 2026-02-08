// Peer.swift
// ClaudeSync
//
// Represents a discovered peer device on the local network.
// Uses @Observable (macOS 14+) for efficient SwiftUI updates.
// Each peer is uniquely identified by a persistent device UUID
// stored in the Bonjour TXT record.

import Foundation
import Network

@Observable
final class Peer: Identifiable, Hashable {
    /// Unique identifier derived from the peer's persisted device UUID.
    let id: String

    /// Human-readable device name from the Bonjour TXT record.
    var name: String

    /// The platform identifier (e.g. "macOS", "Linux").
    var platform: String

    /// Number of config files the peer reports having.
    var configCount: Int

    /// Fingerprint: hash of all config hashes, used for quick equality check.
    var fingerprint: String

    /// Protocol version for compatibility checking.
    var protocolVersion: String

    /// Current connection status to this peer.
    var status: SyncStatus

    /// The Bonjour endpoint used to establish a connection.
    var endpoint: NWEndpoint?

    /// The NWBrowser.Result for re-resolving if needed.
    var browserResult: NWBrowser.Result?

    /// Timestamp of last successful communication.
    var lastSeen: Date

    /// Active connection to this peer, if any.
    var connection: SyncConnection?

    /// The peer's full file manifest, populated after a manifest exchange.
    var remoteManifest: [String: String]?

    /// Number of files that differ between local and this peer.
    var differingFileCount: Int

    init(
        id: String,
        name: String,
        platform: String = "unknown",
        configCount: Int = 0,
        fingerprint: String = "",
        protocolVersion: String = "1",
        status: SyncStatus = .discovered,
        endpoint: NWEndpoint? = nil,
        browserResult: NWBrowser.Result? = nil,
        lastSeen: Date = Date()
    ) {
        self.id = id
        self.name = name
        self.platform = platform
        self.configCount = configCount
        self.fingerprint = fingerprint
        self.protocolVersion = protocolVersion
        self.status = status
        self.endpoint = endpoint
        self.browserResult = browserResult
        self.lastSeen = lastSeen
        self.remoteManifest = nil
        self.differingFileCount = 0
    }

    // MARK: - Hashable

    static func == (lhs: Peer, rhs: Peer) -> Bool {
        lhs.id == rhs.id
    }

    func hash(into hasher: inout Hasher) {
        hasher.combine(id)
    }

    // MARK: - Display Helpers

    /// Returns a platform-appropriate SF Symbol name for this peer.
    var platformIcon: String {
        switch platform.lowercased() {
        case "macos":
            return "desktopcomputer"
        case "linux":
            return "server.rack"
        case "windows":
            return "pc"
        default:
            return "laptopcomputer"
        }
    }

    /// Human-readable description of when this peer was last seen.
    var lastSeenDescription: String {
        let interval = Date().timeIntervalSince(lastSeen)
        if interval < 60 {
            return "Just now"
        } else if interval < 3600 {
            let minutes = Int(interval / 60)
            return "\(minutes) minute\(minutes == 1 ? "" : "s") ago"
        } else if interval < 86400 {
            let hours = Int(interval / 3600)
            return "\(hours) hour\(hours == 1 ? "" : "s") ago"
        } else {
            let days = Int(interval / 86400)
            return "\(days) day\(days == 1 ? "" : "s") ago"
        }
    }
}
