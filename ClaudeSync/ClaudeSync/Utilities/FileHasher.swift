// FileHasher.swift
// ClaudeSync
//
// SHA-256 file hashing using CryptoKit. Reads files in 64KB chunks
// to handle large files without excessive memory allocation.
// Also provides a fingerprint function per PROTOCOL.md Section 2.3:
// hash sorted "path:hash" entries joined by "\n", return first 16 hex chars.

import Foundation
import CryptoKit

/// Provides SHA-256 hashing for individual files and file tree fingerprinting.
enum FileHasher {

    /// Chunk size for reading files during hashing (64 KB).
    private static let chunkSize = 65_536

    /// Computes the SHA-256 hash of a file at the given URL.
    /// Reads in chunks to avoid loading the entire file into memory.
    /// - Parameter url: The file URL to hash.
    /// - Returns: Lowercase hex string of the SHA-256 digest (64 characters).
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

    /// Computes a fingerprint from a dictionary of file hashes per PROTOCOL.md Section 2.3.
    ///
    /// Algorithm:
    /// 1. Create "path:hash" strings for each file.
    /// 2. Sort lexicographically.
    /// 3. Join with "\n" (no trailing newline).
    /// 4. SHA-256 the joined string.
    /// 5. Return the first 16 hex characters.
    ///
    /// - Parameter fileHashes: Dictionary of relative_path -> SHA-256 hash.
    /// - Returns: 16-character lowercase hex fingerprint string.
    static func computeFingerprint(from fileHashes: [String: String]) -> String {
        guard !fileHashes.isEmpty else { return "" }

        // Build sorted "path:hash" entries.
        let sortedEntries = fileHashes
            .map { "\($0.key):\($0.value)" }
            .sorted()

        // Join with newline (no trailing newline).
        let joined = sortedEntries.joined(separator: "\n")

        // SHA-256 the joined string.
        guard let data = joined.data(using: .utf8) else { return "" }
        let digest = SHA256.hash(data: data)
        let fullHash = digest.map { String(format: "%02x", $0) }.joined()

        // Return first 16 characters.
        return String(fullHash.prefix(16))
    }
}
