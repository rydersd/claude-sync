// ==========================================================================
// Device identity management
// Generates and persists a stable UUID v4 for this device.
// The UUID is stored in ~/.claude-sync-device-id so it survives app reinstalls.
// ==========================================================================

use std::fs;
use std::path::PathBuf;
use uuid::Uuid;

/// Filename for the persisted device identity.
const DEVICE_ID_FILE: &str = ".claude-sync-device-id";

/// Get or create a stable device identity UUID.
/// The ID is persisted to a file in the user's home directory so it
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

/// Path to the device ID file: ~/.claude-sync-device-id
fn device_id_path() -> PathBuf {
    dirs::home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(DEVICE_ID_FILE)
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
}
