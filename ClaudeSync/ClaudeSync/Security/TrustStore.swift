// TrustStore.swift
// ClaudeSync
//
// Persists paired device information to ~/Library/Application Support/claude-sync/paired_devices.json.
// Stores certificate fingerprints (SHA-256 of DER-encoded cert) so that TLS connections
// from paired devices can be verified without a central CA.
//
// All mutable state is actor-isolated for thread safety. Disk I/O uses atomic writes
// to prevent corruption from concurrent access or crashes.

import Foundation
import os

/// Persists paired device trust information.
/// Stores certificate fingerprints keyed by device ID in a JSON file.
actor TrustStore {

    // MARK: - Types

    /// A single trusted device record.
    struct TrustedDevice: Codable, Sendable {
        /// The peer's persistent device UUID.
        let deviceId: String

        /// Human-readable device name at pairing time.
        let name: String

        /// SHA-256 fingerprint of the peer's DER-encoded certificate.
        let certFingerprint: String

        /// Timestamp when the pairing was established.
        let pairedAt: Date

        private enum CodingKeys: String, CodingKey {
            case deviceId = "device_id"
            case name
            case certFingerprint = "cert_fingerprint"
            case pairedAt = "paired_at"
        }
    }

    // MARK: - Properties

    /// Path to the trust store JSON file.
    private let storePath: URL

    /// In-memory cache of trusted devices, keyed by device ID.
    private var trustedDevices: [String: TrustedDevice] = [:]

    /// Whether the trust store has been loaded from disk.
    private var isLoaded: Bool = false

    /// Logger for trust store operations.
    private let logger = Logger(subsystem: "com.claudesync", category: "TrustStore")

    /// JSON encoder configured with ISO 8601 dates and sorted keys for deterministic output.
    private static let encoder: JSONEncoder = {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        return encoder
    }()

    /// JSON decoder configured to match the encoder's date strategy.
    private static let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return decoder
    }()

    // MARK: - Initialization

    /// Creates a trust store at the default location.
    /// - Parameter storePath: Override path for testing. Defaults to
    ///   ~/Library/Application Support/claude-sync/paired_devices.json
    init(storePath: URL? = nil) {
        if let storePath = storePath {
            self.storePath = storePath
        } else {
            let appSupport = FileManager.default.urls(
                for: .applicationSupportDirectory,
                in: .userDomainMask
            ).first!
            self.storePath = appSupport
                .appendingPathComponent("claude-sync")
                .appendingPathComponent("paired_devices.json")
        }
    }

    // MARK: - Load / Save

    /// Loads the trust store from disk. Safe to call multiple times (idempotent after first load).
    func load() async throws {
        guard !isLoaded else { return }

        let fm = FileManager.default
        guard fm.fileExists(atPath: storePath.path) else {
            // No store file yet -- start with empty trust set.
            isLoaded = true
            logger.info("No paired_devices.json found, starting with empty trust store")
            return
        }

        let data = try Data(contentsOf: storePath)
        let devices = try Self.decoder.decode([TrustedDevice].self, from: data)

        trustedDevices = Dictionary(uniqueKeysWithValues: devices.map { ($0.deviceId, $0) })
        isLoaded = true
        logger.info("Loaded trust store: \(devices.count) paired devices")
    }

    /// Saves the current trust store to disk atomically.
    func save() async throws {
        let devices = Array(trustedDevices.values)
            .sorted { $0.pairedAt < $1.pairedAt } // Deterministic order by pairing time.

        let data = try Self.encoder.encode(devices)

        // Ensure the parent directory exists.
        let parentDir = storePath.deletingLastPathComponent()
        try FileManager.default.createDirectory(at: parentDir, withIntermediateDirectories: true)

        // Write atomically to prevent corruption.
        try data.write(to: storePath, options: .atomic)
        logger.info("Saved trust store: \(devices.count) paired devices")
    }

    // MARK: - Trust Management

    /// Adds a trusted device to the store and persists to disk.
    /// If a device with the same ID already exists, it is replaced.
    func addTrustedDevice(_ device: TrustedDevice) async throws {
        if !isLoaded { try await load() }

        trustedDevices[device.deviceId] = device
        try await save()
        logger.info("Added trusted device: \(device.name) (\(device.deviceId.prefix(8)))")
    }

    /// Removes a trusted device from the store and persists to disk.
    func removeTrustedDevice(deviceId: String) async throws {
        if !isLoaded { try await load() }

        let removed = trustedDevices.removeValue(forKey: deviceId)
        if removed != nil {
            try await save()
            logger.info("Removed trusted device: \(deviceId.prefix(8))")
        }
    }

    /// Checks if a device is trusted by its device ID.
    func isTrusted(deviceId: String) -> Bool {
        return trustedDevices[deviceId] != nil
    }

    /// Checks if a certificate fingerprint belongs to any trusted device.
    func isTrustedCert(fingerprint: String) -> Bool {
        return trustedDevices.values.contains { $0.certFingerprint == fingerprint }
    }

    /// Returns the trusted device record for a given device ID, if it exists.
    func trustedDevice(for deviceId: String) -> TrustedDevice? {
        return trustedDevices[deviceId]
    }

    /// Returns all trusted devices sorted by pairing date.
    func allTrustedDevices() -> [TrustedDevice] {
        return Array(trustedDevices.values).sorted { $0.pairedAt < $1.pairedAt }
    }

    /// Returns the total number of trusted devices.
    func count() -> Int {
        return trustedDevices.count
    }
}
