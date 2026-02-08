// ConfigFile.swift
// ClaudeSync
//
// Represents a single syncable configuration file from ~/.claude/.
// Tracks relative path, SHA-256 hash, size, and modification date.
// Used by ConfigScanner and DiffEngine to compare file trees.

import Foundation

/// A single configuration file that can be synced between peers.
struct ConfigFile: Identifiable, Hashable, Sendable {
    /// Unique identifier based on the relative path.
    var id: String { relativePath }

    /// Path relative to ~/.claude/ (e.g. "rules/my-rule.md").
    let relativePath: String

    /// SHA-256 hash of the file contents. Empty string if not yet computed.
    let hash: String

    /// File size in bytes.
    let size: UInt64

    /// Last modification date from the filesystem.
    let modifiedDate: Date

    /// Human-readable file size (e.g. "1.2 KB").
    var formattedSize: String {
        let formatter = ByteCountFormatter()
        formatter.countStyle = .file
        formatter.allowedUnits = [.useKB, .useMB, .useBytes]
        return formatter.string(fromByteCount: Int64(size))
    }

    /// The file extension, used for display grouping.
    var fileExtension: String {
        (relativePath as NSString).pathExtension
    }

    /// The directory component of the path (e.g. "rules" from "rules/my-rule.md").
    var directory: String {
        let components = relativePath.split(separator: "/")
        if components.count > 1 {
            return String(components.first ?? "")
        }
        return ""
    }
}

/// Represents the difference status of a config file between two peers.
enum FileDiffStatus: String, Sendable {
    /// File exists only on the local machine.
    case localOnly = "local_only"

    /// File exists only on the remote peer.
    case remoteOnly = "remote_only"

    /// File exists on both but content differs.
    case modified = "modified"

    /// File is identical on both machines.
    case identical = "identical"

    /// Human-readable display label.
    var displayName: String {
        switch self {
        case .localOnly: return "Local Only"
        case .remoteOnly: return "Remote Only"
        case .modified: return "Modified"
        case .identical: return "Identical"
        }
    }

    /// Color used for the diff status in the UI.
    var color: SwiftUI.Color {
        switch self {
        case .localOnly: return .green
        case .remoteOnly: return .blue
        case .modified: return .orange
        case .identical: return .secondary
        }
    }

    /// SF Symbol name for the diff status.
    var iconName: String {
        switch self {
        case .localOnly: return "plus.circle.fill"
        case .remoteOnly: return "arrow.down.circle.fill"
        case .modified: return "pencil.circle.fill"
        case .identical: return "checkmark.circle"
        }
    }
}

/// A file comparison result pairing a path with its diff status.
struct FileDiff: Identifiable, Sendable {
    var id: String { relativePath }

    /// Relative path of the file.
    let relativePath: String

    /// How this file differs between local and remote.
    let status: FileDiffStatus

    /// Local file hash, nil if file does not exist locally.
    let localHash: String?

    /// Remote file hash, nil if file does not exist on remote.
    let remoteHash: String?
}

import SwiftUI
