// SettingsMerger.swift
// ClaudeSync
//
// Handles merging of settings.json between machines. Splits settings
// into portable keys (safe to sync) and machine-specific keys (never sync).
// Uses the same key classification as claude-sync.py.

import Foundation
import os

/// Merges settings.json content between local and remote machines,
/// respecting the portable vs machine-specific key distinction.
enum SettingsMerger {

    /// Logger for merge operations.
    private static let logger = Logger(subsystem: "com.claudesync", category: "SettingsMerger")

    /// Settings keys that are safe to sync across machines.
    /// Per PROTOCOL.md Section 6.4, this is an explicit allowlist.
    static let portableKeys: Set<String> = [
        "hooks",
        "statusLine",
        "attribution",
        "permissions",
        "theme",
        "teammateMode",
    ]

    /// Settings keys that are machine-specific and must never be synced.
    /// Any key not in portableKeys is implicitly machine-specific.
    static let machineSpecificKeys: Set<String> = [
        "env",
        "mcpServers",
        "projects",
    ]

    /// Specific env var keys promoted to sync between machines.
    /// The env block as a whole remains machine-specific — only these named keys transfer.
    /// [EXPERIMENTAL → STANDARD]
    static let recommendedEnvKeys: Set<String> = [
        "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS",
    ]

    // MARK: - Extraction

    /// Extracts only the portable keys from a settings dictionary.
    /// - Parameter settings: The full settings dictionary.
    /// - Returns: A new dictionary containing only portable keys.
    static func extractPortable(from settings: [String: Any]) -> [String: Any] {
        var portable: [String: Any] = [:]
        for key in portableKeys {
            if let value = settings[key] {
                portable[key] = value
            }
        }

        // Extract recommended env keys (specific keys promoted from env block)
        if let envDict = settings["env"] as? [String: Any] {
            var recEnv: [String: Any] = [:]
            for key in recommendedEnvKeys {
                if let value = envDict[key] {
                    recEnv[key] = value
                }
            }
            if !recEnv.isEmpty {
                portable["env"] = recEnv
            }
        }

        return portable
    }

    // MARK: - Merging

    /// Deep merges an overlay dictionary into a base dictionary.
    /// For nested dictionaries, the merge is recursive. For leaf values,
    /// the overlay value wins.
    /// - Parameters:
    ///   - base: The base dictionary.
    ///   - overlay: The overlay dictionary whose values take precedence.
    /// - Returns: A new merged dictionary.
    static func deepMerge(base: [String: Any], overlay: [String: Any]) -> [String: Any] {
        var result = base

        for (key, overlayValue) in overlay {
            if let baseDict = result[key] as? [String: Any],
               let overlayDict = overlayValue as? [String: Any] {
                // Both are dictionaries -- merge recursively.
                result[key] = deepMerge(base: baseDict, overlay: overlayDict)
            } else {
                // Leaf value or type mismatch -- overlay wins.
                result[key] = overlayValue
            }
        }

        return result
    }

    /// Prepares settings for pushing to a peer: extracts only portable keys.
    /// - Parameter localSettings: The full local settings.json content.
    /// - Returns: Settings containing only portable keys.
    static func prepareForPush(localSettings: [String: Any]) -> [String: Any] {
        return extractPortable(from: localSettings)
    }

    /// Merges received remote portable settings into local settings.
    /// Machine-specific keys in the local settings are preserved.
    /// - Parameters:
    ///   - localSettings: The full local settings.json content.
    ///   - remotePortableSettings: Portable settings received from the remote peer.
    /// - Returns: Merged settings with remote portable keys applied.
    static func mergeForPull(
        localSettings: [String: Any],
        remotePortableSettings: [String: Any]
    ) -> [String: Any] {
        // Start with local settings (preserves machine-specific keys).
        // Only merge the portable keys from remote.
        let portableOnly = extractPortable(from: remotePortableSettings)
        var result = deepMerge(base: localSettings, overlay: portableOnly)

        // Merge recommended env keys without clobbering local env
        if let remoteEnv = portableOnly["env"] as? [String: Any] {
            var localEnv = result["env"] as? [String: Any] ?? [:]
            for key in recommendedEnvKeys {
                if let value = remoteEnv[key] {
                    localEnv[key] = value
                }
            }
            result["env"] = localEnv
        }

        return result
    }

    // MARK: - JSON Serialization

    /// Reads settings.json from a URL and returns it as a dictionary.
    /// - Parameter url: The URL of the settings.json file.
    /// - Returns: The parsed dictionary, or empty dictionary if file does not exist.
    static func readSettings(from url: URL) -> [String: Any] {
        guard FileManager.default.fileExists(atPath: url.path) else {
            return [:]
        }

        do {
            let data = try Data(contentsOf: url)
            if let dict = try JSONSerialization.jsonObject(with: data) as? [String: Any] {
                return dict
            }
        } catch {
            logger.error("Failed to read settings at \(url.path): \(error.localizedDescription)")
        }

        return [:]
    }

    /// Writes a settings dictionary to a URL as formatted JSON.
    /// - Parameters:
    ///   - settings: The settings dictionary to write.
    ///   - url: The destination URL.
    /// - Throws: If serialization or file writing fails.
    static func writeSettings(_ settings: [String: Any], to url: URL) throws {
        let data = try JSONSerialization.data(
            withJSONObject: settings,
            options: [.prettyPrinted, .sortedKeys]
        )
        // Append a trailing newline for consistency with the Python tool.
        var output = data
        output.append(contentsOf: "\n".utf8)
        try output.write(to: url)
    }

    /// Encodes a settings dictionary to JSON Data for transmission.
    /// Only includes portable keys.
    /// - Parameter settings: The full settings dictionary.
    /// - Returns: JSON Data of the portable keys only.
    static func encodePortable(from settings: [String: Any]) throws -> Data {
        let portable = extractPortable(from: settings)
        return try JSONSerialization.data(withJSONObject: portable, options: [.sortedKeys])
    }

    /// Decodes received settings JSON Data into a dictionary.
    static func decodeSettings(from data: Data) throws -> [String: Any] {
        guard let dict = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw SettingsMergerError.invalidFormat
        }
        return dict
    }
}

// MARK: - Error Types

enum SettingsMergerError: LocalizedError {
    case invalidFormat

    var errorDescription: String? {
        switch self {
        case .invalidFormat:
            return "Settings data is not a valid JSON dictionary"
        }
    }
}
