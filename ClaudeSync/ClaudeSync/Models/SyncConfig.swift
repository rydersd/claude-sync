// SyncConfig.swift
// ClaudeSync
//
// Configuration model loaded from ~/.claude/sync-config.json.
// Controls tracker URLs, auto-sync behavior, and security policy.
// Changes are persisted back to disk on modification.

import Foundation
import os

/// Application-level configuration for sync behavior, loaded from ~/.claude/sync-config.json.
/// Codable for JSON serialization. Uses snake_case keys on disk to match the CLI tool's format.
struct SyncConfig: Codable, Sendable {

    /// Tracker server configurations for WAN peer discovery.
    var trackers: [TrackerConfig]

    /// Auto-sync behavior settings.
    var autoSync: AutoSyncConfig

    /// Security and pairing settings.
    var security: SecurityConfig

    // MARK: - Nested Types

    /// Configuration for a single tracker server.
    struct TrackerConfig: Codable, Sendable, Identifiable {
        /// WebSocket URL of the tracker (e.g. "wss://tracker.example.com/ws").
        let url: String

        /// Human-readable name for this tracker.
        let name: String

        /// Whether this tracker is enabled for connection.
        var enabled: Bool

        var id: String { url }

        private enum CodingKeys: String, CodingKey {
            case url, name, enabled
        }
    }

    /// Auto-sync behavior configuration.
    struct AutoSyncConfig: Codable, Sendable {
        /// Whether auto-sync should be enabled on startup.
        var enabled: Bool

        /// Debounce interval in milliseconds for file change notifications.
        var debounceMs: Int

        private enum CodingKeys: String, CodingKey {
            case enabled
            case debounceMs = "debounce_ms"
        }
    }

    /// Security and pairing configuration.
    struct SecurityConfig: Codable, Sendable {
        /// Whether TLS certificate pairing is required for all connections.
        var requirePairing: Bool

        /// Whether unpaired LAN peers are allowed (requires pairing = false for WAN).
        var allowUnpairedLan: Bool

        private enum CodingKeys: String, CodingKey {
            case requirePairing = "require_pairing"
            case allowUnpairedLan = "allow_unpaired_lan"
        }
    }

    // MARK: - CodingKeys

    private enum CodingKeys: String, CodingKey {
        case trackers
        case autoSync = "auto_sync"
        case security
    }

    // MARK: - Defaults

    /// Returns a default configuration with no trackers, auto-sync disabled,
    /// and pairing required for WAN but optional for LAN.
    static let `default` = SyncConfig(
        trackers: [],
        autoSync: AutoSyncConfig(enabled: false, debounceMs: 500),
        security: SecurityConfig(requirePairing: true, allowUnpairedLan: true)
    )
}

// MARK: - SyncConfig Loader

/// Handles loading and saving SyncConfig from/to ~/.claude/sync-config.json.
enum SyncConfigLoader {

    /// Path to the sync configuration file.
    private static let configPath: URL = {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".claude")
            .appendingPathComponent("sync-config.json")
    }()

    /// Logger for config loading operations.
    private static let logger = Logger(subsystem: "com.claudesync", category: "SyncConfigLoader")

    /// JSON encoder for writing config to disk.
    private static let encoder: JSONEncoder = {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        return encoder
    }()

    /// JSON decoder for reading config from disk.
    private static let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        return decoder
    }()

    /// Loads the sync configuration from disk.
    /// Returns the default configuration if the file doesn't exist or can't be parsed.
    static func load() -> SyncConfig {
        guard FileManager.default.fileExists(atPath: configPath.path) else {
            logger.info("No sync-config.json found, using defaults")
            return .default
        }

        do {
            let data = try Data(contentsOf: configPath)
            let config = try decoder.decode(SyncConfig.self, from: data)
            logger.info("Loaded sync-config.json: \(config.trackers.count) trackers, autoSync=\(config.autoSync.enabled)")
            return config
        } catch {
            logger.error("Failed to parse sync-config.json: \(error.localizedDescription), using defaults")
            return .default
        }
    }

    /// Saves the sync configuration to disk atomically.
    static func save(_ config: SyncConfig) throws {
        let parentDir = configPath.deletingLastPathComponent()
        try FileManager.default.createDirectory(at: parentDir, withIntermediateDirectories: true)

        let data = try encoder.encode(config)
        try data.write(to: configPath, options: .atomic)

        logger.info("Saved sync-config.json")
    }
}
