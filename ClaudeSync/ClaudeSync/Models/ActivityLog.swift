// ActivityLog.swift
// ClaudeSync
//
// Observable activity log that records sync events for the UI.
// NetworkManager appends events; the MenuBarView displays them.

import Foundation
import SwiftUI

// MARK: - Activity Event

/// A single timestamped event in the activity log.
struct ActivityEvent: Identifiable {
    let id = UUID()
    let timestamp: Date
    let kind: Kind
    let message: String
    let detail: String?

    enum Kind: String {
        // Tracker
        case trackerConnecting
        case trackerConnected
        case trackerDisconnected
        case trackerError

        // Peers
        case peerDiscovered
        case peerLost
        case peerConnected

        // File watching
        case watchingStarted
        case watchingStopped
        case fileChangeDetected

        // Sync operations
        case syncStarted
        case syncCompleted
        case syncFailed
        case fileTransferred
        case conflictResolved

        // General
        case info
        case warning
        case error
    }

    /// SF Symbol for this event kind.
    var iconName: String {
        switch kind {
        case .trackerConnecting:    return "globe.badge.chevron.backward"
        case .trackerConnected:     return "globe"
        case .trackerDisconnected:  return "globe.badge.chevron.backward"
        case .trackerError:         return "globe.badge.chevron.backward"
        case .peerDiscovered:       return "person.badge.plus"
        case .peerLost:             return "person.badge.minus"
        case .peerConnected:        return "link"
        case .watchingStarted:      return "eye"
        case .watchingStopped:      return "eye.slash"
        case .fileChangeDetected:   return "doc.badge.ellipsis"
        case .syncStarted:          return "arrow.triangle.2.circlepath"
        case .syncCompleted:        return "checkmark.circle"
        case .syncFailed:           return "xmark.circle"
        case .fileTransferred:      return "arrow.up.doc"
        case .conflictResolved:     return "arrow.triangle.merge"
        case .info:                 return "info.circle"
        case .warning:              return "exclamationmark.triangle"
        case .error:                return "xmark.octagon"
        }
    }

    /// Color for the icon.
    var iconColor: Color {
        switch kind {
        case .trackerConnected, .peerConnected, .syncCompleted,
             .fileTransferred, .watchingStarted:
            return .green
        case .trackerConnecting, .syncStarted, .fileChangeDetected,
             .conflictResolved:
            return .orange
        case .peerDiscovered, .info:
            return .blue
        case .trackerDisconnected, .peerLost, .watchingStopped:
            return .secondary
        case .trackerError, .syncFailed, .error:
            return .red
        case .warning:
            return .yellow
        }
    }

    /// Relative timestamp string.
    var relativeTime: String {
        let interval = Date().timeIntervalSince(timestamp)
        if interval < 5 {
            return "now"
        } else if interval < 60 {
            return "\(Int(interval))s ago"
        } else if interval < 3600 {
            return "\(Int(interval / 60))m ago"
        } else {
            return "\(Int(interval / 3600))h ago"
        }
    }
}

// MARK: - Activity Log

/// Observable container for activity events. Keeps the most recent 50 events.
@MainActor
final class ActivityLog: ObservableObject {
    static let maxEvents = 50

    @Published private(set) var events: [ActivityEvent] = []

    /// Whether there are new events since the user last looked.
    @Published var hasUnread: Bool = false

    /// The most recent event, for summary display.
    var latestEvent: ActivityEvent? { events.first }

    /// Number of events in the last 60 seconds.
    var recentCount: Int {
        let cutoff = Date().addingTimeInterval(-60)
        return events.filter { $0.timestamp > cutoff }.count
    }

    func log(_ kind: ActivityEvent.Kind, _ message: String, detail: String? = nil) {
        let event = ActivityEvent(
            timestamp: Date(),
            kind: kind,
            message: message,
            detail: detail
        )
        events.insert(event, at: 0)
        if events.count > Self.maxEvents {
            events.removeLast(events.count - Self.maxEvents)
        }
        hasUnread = true
    }

    func markRead() {
        hasUnread = false
    }

    func clear() {
        events.removeAll()
        hasUnread = false
    }
}
