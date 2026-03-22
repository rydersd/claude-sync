// ConflictResolver.swift
// ClaudeSync
//
// Resolves conflicts when a file_changed message arrives with a
// previous_sha256 that does not match the local file's current hash.
// This indicates a concurrent edit -- both sides modified the file
// since the last known sync point.
//
// Resolution strategy:
//   1. Compare timestamps -- newer wins (change_epoch_ms).
//   2. If timestamps are within 1 second -- lower device_id (lexicographic) wins
//      for deterministic tiebreaking across all peers.
//   3. Special case: memory/ paths use append-merge (concatenate both versions
//      separated by "\n---\n") to preserve both sides of accumulated knowledge.

import Foundation
import os

// MARK: - Conflict Resolution Result

/// The outcome of a conflict resolution decision.
enum ConflictResolution: Sendable {
    /// Accept the remote version and overwrite local.
    case acceptRemote(Data)

    /// Keep the local version unchanged.
    case keepLocal

    /// Merge both versions into a combined result.
    case merge(Data)
}

// MARK: - Conflict Resolver

/// Resolves file conflicts using a deterministic last-writer-wins strategy
/// with special append-merge handling for memory/ files.
struct ConflictResolver: Sendable {

    /// Logger for conflict resolution decisions.
    private static let logger = Logger(subsystem: "com.claudesync", category: "ConflictResolver")

    /// The separator used when append-merging memory/ files.
    private static let memorySeparator = "\n---\n"

    /// Resolves a conflict between local and remote versions of a file.
    ///
    /// - Parameters:
    ///   - path: Relative path of the conflicting file (e.g. "rules/git-commits.md").
    ///   - localData: The current local file contents.
    ///   - remoteData: The incoming remote file contents.
    ///   - localTimestamp: Local change timestamp in milliseconds since epoch.
    ///   - remoteTimestamp: Remote change timestamp in milliseconds since epoch.
    ///   - localDeviceId: This device's persistent UUID.
    ///   - remoteDeviceId: The remote peer's persistent UUID.
    /// - Returns: The resolution decision with appropriate data payload.
    static func resolve(
        path: String,
        localData: Data,
        remoteData: Data,
        localTimestamp: Int64,
        remoteTimestamp: Int64,
        localDeviceId: String,
        remoteDeviceId: String
    ) -> ConflictResolution {
        // Special case: memory/ paths use append-merge to preserve both versions.
        if path.hasPrefix("memory/") {
            return resolveMemoryConflict(
                path: path,
                localData: localData,
                remoteData: remoteData
            )
        }

        // General case: last-writer-wins with deterministic tiebreaking.
        return resolveByTimestamp(
            path: path,
            localData: localData,
            remoteData: remoteData,
            localTimestamp: localTimestamp,
            remoteTimestamp: remoteTimestamp,
            localDeviceId: localDeviceId,
            remoteDeviceId: remoteDeviceId
        )
    }

    // MARK: - Timestamp Resolution

    /// Resolves a conflict using timestamp comparison with device_id tiebreaking.
    ///
    /// Algorithm:
    ///   1. If timestamps differ by >1 second, the newer timestamp wins.
    ///   2. If timestamps are within 1 second of each other, the lower device_id
    ///      (lexicographic comparison) wins for deterministic tiebreaking.
    private static func resolveByTimestamp(
        path: String,
        localData: Data,
        remoteData: Data,
        localTimestamp: Int64,
        remoteTimestamp: Int64,
        localDeviceId: String,
        remoteDeviceId: String
    ) -> ConflictResolution {
        let timeDifference = abs(remoteTimestamp - localTimestamp)

        // If timestamps differ by more than 1 second, newer wins.
        if timeDifference > 1000 {
            if remoteTimestamp > localTimestamp {
                logger.info("Conflict on \(path): remote wins (newer by \(timeDifference)ms)")
                return .acceptRemote(remoteData)
            } else {
                logger.info("Conflict on \(path): local wins (newer by \(timeDifference)ms)")
                return .keepLocal
            }
        }

        // Within 1 second: use device_id as deterministic tiebreaker.
        // Lower device_id (lexicographic) wins.
        if remoteDeviceId < localDeviceId {
            logger.info("Conflict on \(path): remote wins (lower device_id tiebreaker)")
            return .acceptRemote(remoteData)
        } else {
            logger.info("Conflict on \(path): local wins (lower device_id tiebreaker)")
            return .keepLocal
        }
    }

    // MARK: - Memory Append-Merge

    /// Resolves a conflict for memory/ files by appending both versions.
    /// Memory files accumulate knowledge (voice profiles, story banks, etc.)
    /// so both local and remote content should be preserved.
    ///
    /// The merged result concatenates local + separator + remote.
    /// If either side contains content that is a substring of the other,
    /// the longer version is used directly to avoid duplication.
    private static func resolveMemoryConflict(
        path: String,
        localData: Data,
        remoteData: Data
    ) -> ConflictResolution {
        // Attempt to decode as UTF-8 strings for merge.
        guard let localString = String(data: localData, encoding: .utf8),
              let remoteString = String(data: remoteData, encoding: .utf8) else {
            // Binary memory files: fall back to accepting the larger version.
            logger.warning("Conflict on \(path): binary memory file, keeping larger version")
            if remoteData.count >= localData.count {
                return .acceptRemote(remoteData)
            } else {
                return .keepLocal
            }
        }

        // If the content is identical, no real conflict.
        if localString == remoteString {
            logger.info("Conflict on \(path): memory files are identical")
            return .keepLocal
        }

        // If one contains the other entirely, use the superset.
        if localString.contains(remoteString) {
            logger.info("Conflict on \(path): local is superset, keeping local")
            return .keepLocal
        }
        if remoteString.contains(localString) {
            logger.info("Conflict on \(path): remote is superset, accepting remote")
            return .acceptRemote(remoteData)
        }

        // True merge: concatenate with separator.
        let merged = localString + memorySeparator + remoteString

        guard let mergedData = merged.data(using: .utf8) else {
            // If merge somehow fails to encode, keep local as safe fallback.
            logger.error("Conflict on \(path): failed to encode merged content, keeping local")
            return .keepLocal
        }

        logger.info("Conflict on \(path): memory append-merge (\(localData.count) + \(remoteData.count) bytes)")
        return .merge(mergedData)
    }
}
