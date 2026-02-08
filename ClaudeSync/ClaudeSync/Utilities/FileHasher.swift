// FileHasher.swift
// ClaudeSync
//
// SHA-256 file hashing using CryptoKit. Reads files in 64KB chunks
// to handle large files without excessive memory allocation.
// Also provides a fingerprint function that hashes the sorted set
// of all file hashes for quick equality comparison between peers.

import Foundation
import CryptoKit

/// Provides SHA-256 hashing for individual files and file tree fingerprinting.
enum FileHasher {

    /// Chunk size for reading files during hashing (64 KB).
    private static let chunkSize = 65_536

    /// Computes the SHA-256 hash of a file at the given URL.
    /// Reads in chunks to avoid loading the entire file into memory.
    /// - Parameter url: The file URL to hash.
    /// - Returns: Lowercase hex string of the SHA-256 digest.
    /// - Throws: If the file cannot be read.
    static func hashFile(at url: URL) throws -> String {
        let handle = try FileHandle(forReadingFrom: url)
        defer { handle.closeFile() }

        var hasher = SHA256()

        while true {
            let chunk = handle.readData(ofLength: chunkSize)
            if chunk.isEmpty {
                break
            }
            hasher.update(data: chunk)
        }

        let digest = hasher.finalize()
        return digest.map { String(format: "%02x", $0) }.joined()
    }

    /// Computes a fingerprint from a dictionary of file hashes.
    /// The fingerprint is a SHA-256 hash of the sorted (path:hash) pairs,
    /// enabling quick comparison between two file trees.
    /// - Parameter fileHashes: Dictionary of relative_path -> SHA-256 hash.
    /// - Returns: Lowercase hex string of the fingerprint.
    static func computeFingerprint(from fileHashes: [String: String]) -> String {
        // Sort by path for deterministic ordering.
        let sortedEntries = fileHashes.sorted { $0.key < $1.key }

        var hasher = SHA256()
        for (path, hash) in sortedEntries {
            // Use "path:hash\n" as the input for each entry.
            if let data = "\(path):\(hash)\n".data(using: .utf8) {
                hasher.update(data: data)
            }
        }

        let digest = hasher.finalize()
        return digest.map { String(format: "%02x", $0) }.joined()
    }
}
