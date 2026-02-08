// SyncStatus.swift
// ClaudeSync
//
// Represents the current state of a peer connection or sync operation.
// Used by the UI to display appropriate status indicators and
// enable/disable actions based on current state.

import SwiftUI

enum SyncStatus: String, CaseIterable, Sendable {
    /// Peer discovered via Bonjour but no connection attempted yet.
    case discovered

    /// TCP connection is being established to the peer.
    case connecting

    /// Connection established, handshake (hello exchange) completed.
    case connected

    /// Actively exchanging manifests to determine file differences.
    case comparing

    /// File transfer is in progress (push or pull).
    case syncing

    /// Sync completed successfully.
    case synced

    /// Connection or sync encountered an error.
    case error

    /// Peer was previously seen but is no longer advertising on the network.
    case offline

    // MARK: - Display Properties

    /// Human-readable label for the status.
    var displayName: String {
        switch self {
        case .discovered: return "Discovered"
        case .connecting: return "Connecting..."
        case .connected: return "Connected"
        case .comparing: return "Comparing..."
        case .syncing: return "Syncing..."
        case .synced: return "In Sync"
        case .error: return "Error"
        case .offline: return "Offline"
        }
    }

    /// Color used for the status indicator dot in the UI.
    var indicatorColor: Color {
        switch self {
        case .discovered: return .blue
        case .connecting: return .orange
        case .connected: return .green
        case .comparing: return .orange
        case .syncing: return .orange
        case .synced: return .green
        case .error: return .red
        case .offline: return .gray
        }
    }

    /// SF Symbol name for the status.
    var iconName: String {
        switch self {
        case .discovered: return "circle.fill"
        case .connecting: return "circle.dotted"
        case .connected: return "circle.fill"
        case .comparing: return "arrow.triangle.2.circlepath"
        case .syncing: return "arrow.triangle.2.circlepath"
        case .synced: return "checkmark.circle.fill"
        case .error: return "exclamationmark.circle.fill"
        case .offline: return "circle"
        }
    }

    /// Whether the peer is in a state where sync actions are available.
    var canSync: Bool {
        switch self {
        case .connected, .synced, .comparing:
            return true
        default:
            return false
        }
    }

    /// Whether the status represents an active operation (show progress indicator).
    var isActive: Bool {
        switch self {
        case .connecting, .comparing, .syncing:
            return true
        default:
            return false
        }
    }
}
