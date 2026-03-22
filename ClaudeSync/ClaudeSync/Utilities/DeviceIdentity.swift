// DeviceIdentity.swift
// ClaudeSync
//
// Generates and persists a unique device UUID for this machine.
// Per PROTOCOL.md Section 2.4, the UUID is stored at:
//   macOS: ~/Library/Application Support/claude-sync/device-id
// It is stable across app restarts, OS reboots, and app updates.

import Foundation

/// Manages the persistent device identity used for Bonjour advertisement
/// and peer identification. The UUID is generated once and persisted to
/// a file so it survives app restarts.
enum DeviceIdentity {

    /// Directory for device identity storage.
    private static let storageDirectory: URL = {
        let appSupport = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
        return appSupport.appendingPathComponent("claude-sync")
    }()

    /// File path for the persisted device UUID.
    private static let deviceIdFile: URL = {
        storageDirectory.appendingPathComponent("device-id")
    }()

    /// Returns the persistent device UUID, generating one if this is the first launch.
    /// Thread-safe via file system atomicity.
    static var deviceId: String {
        // Try to read existing device ID from file.
        if let existing = try? String(contentsOf: deviceIdFile, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines),
           !existing.isEmpty {
            return existing
        }

        // Generate a new UUID and persist it.
        let newId = UUID().uuidString.lowercased()

        do {
            try FileManager.default.createDirectory(at: storageDirectory, withIntermediateDirectories: true)
            try newId.write(to: deviceIdFile, atomically: true, encoding: .utf8)
        } catch {
            // If we can't persist, return the generated ID anyway.
            // It will be regenerated next launch, which is acceptable for first-run edge cases.
        }

        return newId
    }

    /// Returns the human-readable device name.
    /// Uses ProcessInfo.processInfo.hostName which returns the local hostname
    /// (e.g. "Ryders-MacBook-Pro.local" -> "Ryders-MacBook-Pro").
    static var deviceName: String {
        let fullHostname = ProcessInfo.processInfo.hostName
        // Strip the ".local" suffix if present for cleaner display.
        if fullHostname.hasSuffix(".local") {
            return String(fullHostname.dropLast(6))
        }
        return fullHostname
    }

    /// Returns a short description of the platform for TXT record metadata.
    /// Lowercase per PROTOCOL.md TXT record spec.
    static var platform: String {
        return "macos"
    }

    /// Resets the device ID. Only used for testing purposes.
    /// After calling this, the next access to deviceId will generate a new UUID.
    static func resetDeviceId() {
        try? FileManager.default.removeItem(at: deviceIdFile)
    }
}
