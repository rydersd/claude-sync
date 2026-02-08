// DiffEngine.swift
// ClaudeSync
//
// Compares file trees between local and remote peer using set-based
// hash comparison. Produces a list of FileDiff results categorizing
// each file as local-only, remote-only, modified, or identical.
// This is the same algorithm as DiffEngine in claude-sync.py.

import Foundation

/// Compares two file hash dictionaries and produces a categorized diff.
/// Used to determine which files need to be transferred during sync.
enum DiffEngine {

    /// Compares local and remote file hash dictionaries.
    /// - Parameters:
    ///   - localHashes: Dictionary of relative_path -> SHA-256 hash for local files.
    ///   - remoteHashes: Dictionary of relative_path -> SHA-256 hash for remote files.
    /// - Returns: Array of FileDiff results sorted by relative path.
    static func compare(
        local localHashes: [String: String],
        remote remoteHashes: [String: String]
    ) -> [FileDiff] {
        var diffs: [FileDiff] = []

        let localPaths = Set(localHashes.keys)
        let remotePaths = Set(remoteHashes.keys)

        // Files that exist only locally.
        for path in localPaths.subtracting(remotePaths).sorted() {
            diffs.append(FileDiff(
                relativePath: path,
                status: .localOnly,
                localHash: localHashes[path],
                remoteHash: nil
            ))
        }

        // Files that exist only on the remote.
        for path in remotePaths.subtracting(localPaths).sorted() {
            diffs.append(FileDiff(
                relativePath: path,
                status: .remoteOnly,
                localHash: nil,
                remoteHash: remoteHashes[path]
            ))
        }

        // Files that exist on both -- check if they differ.
        for path in localPaths.intersection(remotePaths).sorted() {
            let localHash = localHashes[path]!
            let remoteHash = remoteHashes[path]!

            if localHash == remoteHash {
                diffs.append(FileDiff(
                    relativePath: path,
                    status: .identical,
                    localHash: localHash,
                    remoteHash: remoteHash
                ))
            } else {
                diffs.append(FileDiff(
                    relativePath: path,
                    status: .modified,
                    localHash: localHash,
                    remoteHash: remoteHash
                ))
            }
        }

        return diffs
    }

    /// Returns only the diffs that represent actual differences (not identical files).
    static func differences(
        local localHashes: [String: String],
        remote remoteHashes: [String: String]
    ) -> [FileDiff] {
        return compare(local: localHashes, remote: remoteHashes)
            .filter { $0.status != .identical }
    }

    /// Counts the number of differing files between local and remote.
    static func differenceCount(
        local localHashes: [String: String],
        remote remoteHashes: [String: String]
    ) -> Int {
        return differences(local: localHashes, remote: remoteHashes).count
    }

    /// Returns file paths that need to be pulled from the remote
    /// (files that are remote-only or modified).
    static func filesToPull(
        local localHashes: [String: String],
        remote remoteHashes: [String: String]
    ) -> [String] {
        return differences(local: localHashes, remote: remoteHashes)
            .filter { $0.status == .remoteOnly || $0.status == .modified }
            .map { $0.relativePath }
    }

    /// Returns file paths that need to be pushed to the remote
    /// (files that are local-only or modified).
    static func filesToPush(
        local localHashes: [String: String],
        remote remoteHashes: [String: String]
    ) -> [String] {
        return differences(local: localHashes, remote: remoteHashes)
            .filter { $0.status == .localOnly || $0.status == .modified }
            .map { $0.relativePath }
    }

    /// Quick check: are two file trees identical?
    /// Uses fingerprint comparison for O(1) check before doing full diff.
    static func areIdentical(
        localFingerprint: String,
        remoteFingerprint: String
    ) -> Bool {
        return !localFingerprint.isEmpty
            && !remoteFingerprint.isEmpty
            && localFingerprint == remoteFingerprint
    }
}
