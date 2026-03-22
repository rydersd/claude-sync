// ==========================================================================
// Device identity management
// Generates and persists a stable UUID v4 for this device.
// Storage path per PROTOCOL.md Section 2.4:
//   macOS:   ~/Library/Application Support/claude-sync/device-id
//   Windows: %APPDATA%\claude-sync\device-id
//   Linux:   ~/.local/share/claude-sync/device-id (XDG data dir)
// ==========================================================================

use std::fs;
use std::path::PathBuf;
use uuid::Uuid;

/// Get or create a stable device identity UUID.
/// The ID is persisted to a platform-appropriate path so it
/// remains consistent across app restarts and updates.
pub fn get_or_create_device_id() -> String {
    let id_path = device_id_path();

    // Try to read an existing device ID
    if let Ok(contents) = fs::read_to_string(&id_path) {
        let trimmed = contents.trim();
        // Validate it looks like a UUID
        if Uuid::parse_str(trimmed).is_ok() {
            return trimmed.to_string();
        }
    }

    // Generate a new UUID v4 and persist it
    let new_id = Uuid::new_v4().to_string();
    if let Some(parent) = id_path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    let _ = fs::write(&id_path, &new_id);

    new_id
}

/// Get the hostname of this machine, falling back to "unknown" if
/// the hostname cannot be determined.
pub fn get_hostname() -> String {
    hostname::get()
        .ok()
        .and_then(|h| h.into_string().ok())
        .unwrap_or_else(|| "unknown".to_string())
}

/// Determine the platform string for this device.
/// Returns "macos", "linux", "windows", or "unknown".
pub fn get_platform() -> String {
    if cfg!(target_os = "macos") {
        "macos".to_string()
    } else if cfg!(target_os = "linux") {
        "linux".to_string()
    } else if cfg!(target_os = "windows") {
        "windows".to_string()
    } else {
        "unknown".to_string()
    }
}

/// Path to the device ID file per PROTOCOL.md Section 2.4.
/// Uses the platform-appropriate application data directory.
fn device_id_path() -> PathBuf {
    // dirs::data_dir() returns:
    //   macOS:   ~/Library/Application Support
    //   Windows: %APPDATA% (C:\Users\<user>\AppData\Roaming)
    //   Linux:   ~/.local/share (XDG_DATA_HOME)
    let base = dirs::data_dir()
        .unwrap_or_else(|| {
            dirs::home_dir()
                .unwrap_or_else(|| PathBuf::from("."))
        });

    base.join("claude-sync").join("device-id")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_get_platform_returns_known_value() {
        let platform = get_platform();
        assert!(
            ["macos", "linux", "windows", "unknown"].contains(&platform.as_str()),
            "Unexpected platform: {}",
            platform
        );
    }

    #[test]
    fn test_get_hostname_returns_nonempty() {
        let host = get_hostname();
        assert!(!host.is_empty(), "Hostname should not be empty");
    }

    #[test]
    fn test_device_id_path_uses_app_support() {
        let path = device_id_path();
        let path_str = path.to_string_lossy();
        // Should contain claude-sync directory name
        assert!(
            path_str.contains("claude-sync"),
            "Device ID path should be under claude-sync dir: {}",
            path_str
        );
        // Should NOT be in home root like old .claude-sync-device-id
        assert!(
            !path_str.ends_with(".claude-sync-device-id"),
            "Device ID should not use old flat-file path: {}",
            path_str
        );
    }
}
