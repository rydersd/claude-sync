// DeviceIdentity.swift
// ClaudeSync
//
// Generates and persists a unique device UUID for this machine.
// The UUID is stored in UserDefaults and reused across app launches.
// Also provides the human-readable device name from the system hostname.

import Foundation

/// Manages the persistent device identity used for Bonjour advertisement
/// and peer identification. The UUID is generated once and stored in
/// UserDefaults so it survives app restarts.
enum DeviceIdentity {

    /// UserDefaults key for the persisted device UUID.
    private static let deviceIdKey = "com.claudesync.deviceId"

    /// Returns the persistent device UUID, generating one if this is the first launch.
    /// Thread-safe because UserDefaults is thread-safe for reads/writes.
    static var deviceId: String {
        if let existing = UserDefaults.standard.string(forKey: deviceIdKey) {
            return existing
        }

        let newId = UUID().uuidString.lowercased()
        UserDefaults.standard.set(newId, forKey: deviceIdKey)
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
    static var platform: String {
        return "macOS"
    }

    /// Resets the device ID. Only used for testing purposes.
    /// After calling this, the next access to deviceId will generate a new UUID.
    static func resetDeviceId() {
        UserDefaults.standard.removeObject(forKey: deviceIdKey)
    }
}
