// SyncEngine.swift
// ClaudeSync
//
// Orchestrates push/pull file transfer between local machine and a peer.
// Coordinates between ConfigScanner (local files), SyncConnection (network),
// and SettingsMerger (settings.json special handling).
//
// Push flow: read local files -> base64 encode -> send as FileTransfer messages
// Pull flow: receive FileTransfer messages -> decode -> write to disk

import Foundation
import CryptoKit
import os

/// Orchestrates file synchronization between the local machine and a connected peer.
/// Handles both push (local -> remote) and pull (remote -> local) operations.
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

    /// Pushes local files to a connected peer.
    /// Reads each file, base64-encodes it, and sends it as a FileTransfer message.
    /// - Parameters:
    ///   - files: List of relative paths to push.
    ///   - localHashes: Current local file hashes for verification.
    ///   - connection: The active connection to the peer.
    ///   - syncId: Unique identifier for this sync session.
    ///   - onProgress: Callback invoked after each file is sent (index, total, path).
    /// - Returns: The number of files successfully transferred.
    func pushFiles(
        _ files: [String],
        localHashes: [String: String],
        via connection: SyncConnection,
        syncId: String,
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

            // Base64 encode the file contents for JSON transport.
            let base64Content = fileData.base64EncodedString()

            // Use the known hash or compute one.
            let hash = localHashes[relativePath] ?? {
                let url = baseDirectory.appendingPathComponent(relativePath)
                return (try? FileHasher.hashFile(at: url)) ?? ""
            }()

            // Build and send the FileTransfer message.
            let transferPayload = FileTransferPayload(
                syncId: syncId,
                relativePath: relativePath,
                contentBase64: base64Content,
                hash: hash,
                index: index + 1,
                totalFiles: totalFiles
            )

            try await connection.send(.fileTransfer(transferPayload))
            logger.info("Sent file \(index + 1)/\(totalFiles): \(relativePath)")

            // Wait for FileAck from the peer.
            let ackMessage = try await connection.receiveMessage()
            switch ackMessage {
            case .fileAck(let ack):
                if ack.success {
                    successCount += 1
                } else {
                    logger.error("Peer rejected file \(relativePath): \(ack.error ?? "unknown")")
                }
            case .error(let errorPayload):
                logger.error("Peer error during push: \(errorPayload.message)")
                throw SyncEngineError.peerError(errorPayload.message)
            default:
                logger.warning("Unexpected message type during push, expected fileAck")
            }

            onProgress?(index + 1, totalFiles, relativePath)
        }

        // Send SyncComplete message.
        let completePayload = SyncCompletePayload(
            syncId: syncId,
            filesTransferred: successCount,
            success: true,
            message: "Push complete: \(successCount)/\(totalFiles) files transferred"
        )
        try await connection.send(.syncComplete(completePayload))

        logger.info("Push complete: \(successCount)/\(totalFiles) files")
        return successCount
    }

    // MARK: - Pull (Remote -> Local)

    /// Handles receiving files from a peer during a pull operation.
    /// Listens for FileTransfer messages, decodes them, writes to disk,
    /// and sends FileAck responses.
    /// - Parameters:
    ///   - expectedFiles: Number of files expected to receive.
    ///   - connection: The active connection to the peer.
    ///   - syncId: Unique identifier for this sync session.
    ///   - onProgress: Callback invoked after each file is received (index, total, path).
    /// - Returns: The number of files successfully received and written.
    func receiveFiles(
        expectedFiles: Int,
        via connection: SyncConnection,
        syncId: String,
        onProgress: (@Sendable (Int, Int, String) -> Void)? = nil
    ) async throws -> Int {
        var successCount = 0

        for _ in 0..<expectedFiles {
            let message = try await connection.receiveMessage()

            switch message {
            case .fileTransfer(let transfer):
                // Decode the base64 content.
                guard let fileData = Data(base64Encoded: transfer.contentBase64) else {
                    logger.error("Failed to decode base64 for: \(transfer.relativePath)")
                    let ack = FileAckPayload(
                        syncId: syncId,
                        relativePath: transfer.relativePath,
                        success: false,
                        error: "Base64 decode failed"
                    )
                    try await connection.send(.fileAck(ack))
                    continue
                }

                // Verify the hash matches.
                let computedHash = computeSHA256(data: fileData)
                if !transfer.hash.isEmpty && computedHash != transfer.hash {
                    logger.warning("Hash mismatch for \(transfer.relativePath): expected \(transfer.hash.prefix(8)), got \(computedHash.prefix(8))")
                    // Accept the file anyway but log the mismatch.
                    // In a stricter mode, we could reject it.
                }

                // Handle settings.json specially via SettingsMerger.
                if transfer.relativePath == "settings.json" {
                    do {
                        try await handleSettingsPull(data: fileData)
                    } catch {
                        logger.error("Settings merge failed: \(error.localizedDescription)")
                        let ack = FileAckPayload(
                            syncId: syncId,
                            relativePath: transfer.relativePath,
                            success: false,
                            error: "Settings merge failed: \(error.localizedDescription)"
                        )
                        try await connection.send(.fileAck(ack))
                        continue
                    }
                } else {
                    // Write the file to disk.
                    do {
                        try await scanner.writeFile(data: fileData, relativePath: transfer.relativePath)
                    } catch {
                        logger.error("Write failed for \(transfer.relativePath): \(error.localizedDescription)")
                        let ack = FileAckPayload(
                            syncId: syncId,
                            relativePath: transfer.relativePath,
                            success: false,
                            error: "Write failed: \(error.localizedDescription)"
                        )
                        try await connection.send(.fileAck(ack))
                        continue
                    }
                }

                // Send success ack.
                let ack = FileAckPayload(
                    syncId: syncId,
                    relativePath: transfer.relativePath,
                    success: true,
                    error: nil
                )
                try await connection.send(.fileAck(ack))
                successCount += 1

                logger.info("Received file \(transfer.index)/\(transfer.totalFiles): \(transfer.relativePath)")
                onProgress?(transfer.index, transfer.totalFiles, transfer.relativePath)

            case .syncComplete:
                // Peer signaled completion early (fewer files than expected).
                logger.info("Peer signaled sync complete early")
                break

            case .error(let errorPayload):
                logger.error("Peer error during pull: \(errorPayload.message)")
                throw SyncEngineError.peerError(errorPayload.message)

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
    /// Used for verifying received file contents.
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
