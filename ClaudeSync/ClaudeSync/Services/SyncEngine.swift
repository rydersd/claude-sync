// SyncEngine.swift
// ClaudeSync
//
// Orchestrates push/pull file transfer between local machine and a peer.
// Coordinates between ConfigScanner (local files), SyncConnection (network),
// and SettingsMerger (settings.json special handling).
//
// Push flow: read local files -> base64 encode -> send as file messages
// Pull flow: receive file messages -> decode -> verify -> write to disk

import Foundation
import CryptoKit
import os

/// Orchestrates file synchronization between the local machine and a connected peer.
/// Handles both push (local -> remote) and pull (remote -> local) operations
/// per PROTOCOL.md Section 5.
actor SyncEngine {

    /// Logger for sync operations.
    private let logger = Logger(subsystem: "com.claudesync", category: "SyncEngine")

    /// The config scanner for reading/writing local files.
    private let scanner: ConfigScanner

    /// The base URL for the local config directory (~/.claude/).
    private let baseDirectory: URL

    // MARK: - Initialization

    init(scanner: ConfigScanner? = nil) {
        let baseDir = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".claude")
        self.baseDirectory = baseDir
        self.scanner = scanner ?? ConfigScanner(baseDirectory: baseDir)
    }

    // MARK: - Push (Local -> Remote)

    /// Pushes local files to a connected peer per PROTOCOL.md Section 5.4.
    /// Reads each file, base64-encodes it, computes SHA-256, and sends as a file message.
    /// Waits for file_ack after each file.
    /// - Parameters:
    ///   - files: List of relative paths to push.
    ///   - localHashes: Current local file hashes for verification.
    ///   - connection: The active connection to the peer.
    ///   - onProgress: Callback invoked after each file is sent (index, total, path).
    /// - Returns: The number of files successfully transferred.
    func pushFiles(
        _ files: [String],
        localHashes: [String: String],
        via connection: SyncConnection,
        onProgress: (@Sendable (Int, Int, String) -> Void)? = nil
    ) async throws -> Int {
        var successCount = 0
        let totalFiles = files.count

        for (index, relativePath) in files.enumerated() {
            // Read the file contents from disk.
            guard let fileData = await scanner.readFile(relativePath: relativePath) else {
                logger.warning("Skipping \(relativePath): file not readable")
                continue
            }

            // Compute SHA-256 hash of the raw file data.
            let hash = localHashes[relativePath] ?? computeSHA256(data: fileData)

            // Check if file should be executable.
            let isExecutable = relativePath.hasSuffix(".sh") || relativePath.hasSuffix(".py")

            // Build and send the file message per PROTOCOL.md Section 4.3.
            let fileMsg = SyncProtocolCoder.makeFile(
                path: relativePath,
                data: fileData,
                sha256: hash,
                executable: isExecutable
            )

            try await connection.send(fileMsg)
            logger.info("Sent file \(index + 1)/\(totalFiles): \(relativePath)")

            // Wait for file_ack from the peer.
            let ackMessage = try await connection.receiveMessage()
            switch ackMessage {
            case .fileAck(let ack):
                if ack.success {
                    successCount += 1
                } else {
                    logger.error("Peer rejected file \(relativePath): \(ack.error ?? "unknown")")
                }
            case .error(let errorMsg):
                logger.error("Peer error during push: \(errorMsg.message)")
                throw SyncEngineError.peerError(errorMsg.message)
            default:
                logger.warning("Unexpected message type during push, expected file_ack")
            }

            onProgress?(index + 1, totalFiles, relativePath)
        }

        // Send sync_complete message per PROTOCOL.md Section 4.3.
        let completeMsg = SyncProtocolCoder.makeSyncComplete(
            filesTransferred: successCount,
            direction: "push"
        )
        try await connection.send(completeMsg)

        logger.info("Push complete: \(successCount)/\(totalFiles) files")
        return successCount
    }

    // MARK: - Pull (Remote -> Local)

    /// Handles receiving files from a peer during a pull operation per PROTOCOL.md Section 5.5.
    /// Listens for file messages, decodes, verifies integrity, writes to disk,
    /// and sends file_ack responses.
    /// - Parameters:
    ///   - expectedFiles: Number of files expected to receive.
    ///   - connection: The active connection to the peer.
    ///   - onProgress: Callback invoked after each file is received (index, total, path).
    /// - Returns: The number of files successfully received and written.
    func receiveFiles(
        expectedFiles: Int,
        via connection: SyncConnection,
        onProgress: (@Sendable (Int, Int, String) -> Void)? = nil
    ) async throws -> Int {
        var successCount = 0

        for i in 0..<expectedFiles {
            let message = try await connection.receiveMessage()

            switch message {
            case .file(let transfer):
                // Decode the base64 content.
                guard let fileData = Data(base64Encoded: transfer.contentBase64) else {
                    logger.error("Failed to decode base64 for: \(transfer.path)")
                    let ack = SyncProtocolCoder.makeFileAck(
                        path: transfer.path,
                        success: false,
                        error: "checksum_mismatch"
                    )
                    try await connection.send(ack)
                    continue
                }

                // Verify size per PROTOCOL.md Section 4.3 integrity verification.
                if fileData.count != transfer.size {
                    logger.error("Size mismatch for \(transfer.path): expected \(transfer.size), got \(fileData.count)")
                    let ack = SyncProtocolCoder.makeFileAck(
                        path: transfer.path,
                        success: false,
                        error: "size_mismatch"
                    )
                    try await connection.send(ack)
                    continue
                }

                // Verify SHA-256 hash.
                let computedHash = computeSHA256(data: fileData)
                if computedHash != transfer.sha256 {
                    logger.error("Checksum mismatch for \(transfer.path): expected \(transfer.sha256.prefix(8)), got \(computedHash.prefix(8))")
                    let ack = SyncProtocolCoder.makeFileAck(
                        path: transfer.path,
                        success: false,
                        error: "checksum_mismatch"
                    )
                    try await connection.send(ack)
                    continue
                }

                // Handle settings.json specially via SettingsMerger.
                if transfer.path == "settings.json" {
                    do {
                        try await handleSettingsPull(data: fileData)
                    } catch {
                        logger.error("Settings merge failed: \(error.localizedDescription)")
                        let ack = SyncProtocolCoder.makeFileAck(
                            path: transfer.path,
                            success: false,
                            error: "Settings merge failed: \(error.localizedDescription)"
                        )
                        try await connection.send(ack)
                        continue
                    }
                } else {
                    // Write the file to disk using atomic write per PROTOCOL.md Section 5.6.
                    do {
                        try await scanner.writeFile(data: fileData, relativePath: transfer.path)
                    } catch {
                        logger.error("Write failed for \(transfer.path): \(error.localizedDescription)")
                        let ack = SyncProtocolCoder.makeFileAck(
                            path: transfer.path,
                            success: false,
                            error: "Write failed: \(error.localizedDescription)"
                        )
                        try await connection.send(ack)
                        continue
                    }
                }

                // Send success ack.
                let ack = SyncProtocolCoder.makeFileAck(path: transfer.path, success: true)
                try await connection.send(ack)
                successCount += 1

                logger.info("Received file \(i + 1)/\(expectedFiles): \(transfer.path)")
                onProgress?(i + 1, expectedFiles, transfer.path)

            case .syncComplete:
                // Peer signaled completion early (fewer files than expected).
                logger.info("Peer signaled sync complete early")
                break

            case .error(let errorMsg):
                logger.error("Peer error during pull: \(errorMsg.message)")
                throw SyncEngineError.peerError(errorMsg.message)

            default:
                logger.warning("Unexpected message type during pull")
            }
        }

        logger.info("Pull complete: \(successCount)/\(expectedFiles) files")
        return successCount
    }

    // MARK: - Settings Special Handling

    /// Handles the special merge logic for settings.json during a pull.
    /// Remote portable keys are merged into local settings; machine-specific keys are preserved.
    private func handleSettingsPull(data: Data) async throws {
        let remoteSettings = try SettingsMerger.decodeSettings(from: data)
        let settingsURL = baseDirectory.appendingPathComponent("settings.json")
        let localSettings = SettingsMerger.readSettings(from: settingsURL)

        let merged = SettingsMerger.mergeForPull(
            localSettings: localSettings,
            remotePortableSettings: remoteSettings
        )

        try SettingsMerger.writeSettings(merged, to: settingsURL)
        logger.info("Settings.json merged successfully")
    }

    // MARK: - Hash Utility

    /// Computes SHA-256 hash of in-memory Data.
    private func computeSHA256(data: Data) -> String {
        let digest = SHA256.hash(data: data)
        return digest.map { String(format: "%02x", $0) }.joined()
    }
}

// MARK: - Error Types

enum SyncEngineError: LocalizedError {
    case peerError(String)
    case fileNotReadable(String)
    case hashMismatch(String)
    case unexpectedMessage

    var errorDescription: String? {
        switch self {
        case .peerError(let message):
            return "Peer reported error: \(message)"
        case .fileNotReadable(let path):
            return "Cannot read file: \(path)"
        case .hashMismatch(let path):
            return "Hash verification failed for: \(path)"
        case .unexpectedMessage:
            return "Received unexpected message type during sync"
        }
    }
}
