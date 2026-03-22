// ==========================================================================
// Trust store - persists paired device information to disk
// Stored at ~/Library/Application Support/claude-sync/paired_devices.json
// (macOS) or equivalent on other platforms. Thread-safe read/write.
// ==========================================================================

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;

/// File name for the persisted trust store.
const TRUST_STORE_FILENAME: &str = "paired_devices.json";

/// A single trusted (paired) device record.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TrustedDevice {
    pub device_id: String,
    pub name: String,
    pub cert_fingerprint: String,
    /// When the device was paired (Unix epoch seconds)
    pub paired_at: i64,
}

/// Serialization wrapper for the trust store file.
#[derive(Debug, Clone, Serialize, Deserialize)]
struct TrustStoreData {
    devices: HashMap<String, TrustedDevice>,
}

/// Persists paired device trust information to disk.
/// Provides add/remove/query operations with automatic persistence.
pub struct TrustStore {
    /// Path to the directory containing the trust store file
    store_path: PathBuf,
    /// In-memory representation of trusted devices
    data: TrustStoreData,
}

impl TrustStore {
    /// Create a new TrustStore using the platform data directory.
    /// Call `load()` after construction to read existing data from disk.
    pub fn new() -> Self {
        let store_path = dirs::data_dir()
            .unwrap_or_else(|| PathBuf::from("."))
            .join("claude-sync");
        Self {
            store_path,
            data: TrustStoreData {
                devices: HashMap::new(),
            },
        }
    }

    /// Load existing trust data from disk. If the file doesn't exist,
    /// the store starts empty (not an error).
    pub fn load(&mut self) -> Result<(), Box<dyn std::error::Error>> {
        let file_path = self.store_path.join(TRUST_STORE_FILENAME);
        if !file_path.exists() {
            log::info!("No trust store file found at {:?}, starting empty", file_path);
            return Ok(());
        }

        let contents = fs::read_to_string(&file_path)?;
        self.data = serde_json::from_str(&contents)?;
        log::info!(
            "Loaded trust store with {} paired devices",
            self.data.devices.len()
        );
        Ok(())
    }

    /// Save the current trust data to disk atomically (write to temp, then rename).
    pub fn save(&self) -> Result<(), Box<dyn std::error::Error>> {
        fs::create_dir_all(&self.store_path)?;

        let file_path = self.store_path.join(TRUST_STORE_FILENAME);
        let tmp_path = file_path.with_extension("json.tmp");

        let json = serde_json::to_string_pretty(&self.data)?;
        fs::write(&tmp_path, &json)?;
        fs::rename(&tmp_path, &file_path)?;

        Ok(())
    }

    /// Add a device to the trust store and persist to disk.
    pub fn add_trusted_device(&mut self, device: TrustedDevice) -> Result<(), Box<dyn std::error::Error>> {
        log::info!(
            "Adding trusted device: {} ({})",
            device.name,
            device.device_id
        );
        self.data.devices.insert(device.device_id.clone(), device);
        self.save()
    }

    /// Remove a device from the trust store and persist the change.
    pub fn remove_trusted_device(&mut self, device_id: &str) -> Result<(), Box<dyn std::error::Error>> {
        if self.data.devices.remove(device_id).is_some() {
            log::info!("Removed trusted device: {}", device_id);
            self.save()?;
        }
        Ok(())
    }

    /// Check if a device is trusted by its device_id.
    pub fn is_trusted(&self, device_id: &str) -> bool {
        self.data.devices.contains_key(device_id)
    }

    /// Check if a certificate fingerprint belongs to a trusted device.
    pub fn is_trusted_cert(&self, fingerprint: &str) -> bool {
        self.data
            .devices
            .values()
            .any(|d| d.cert_fingerprint == fingerprint)
    }

    /// Get all trusted devices as a list.
    pub fn all_trusted_devices(&self) -> Vec<TrustedDevice> {
        self.data.devices.values().cloned().collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_trust_store_in_memory() {
        let mut store = TrustStore {
            store_path: PathBuf::from("/tmp/test-claude-sync-trust"),
            data: TrustStoreData {
                devices: HashMap::new(),
            },
        };

        assert!(!store.is_trusted("device-1"));
        assert!(!store.is_trusted_cert("AA:BB:CC"));

        let device = TrustedDevice {
            device_id: "device-1".to_string(),
            name: "Test MacBook".to_string(),
            cert_fingerprint: "AA:BB:CC".to_string(),
            paired_at: 1700000000,
        };

        // Don't persist in test; just modify in-memory
        store.data.devices.insert(device.device_id.clone(), device);

        assert!(store.is_trusted("device-1"));
        assert!(store.is_trusted_cert("AA:BB:CC"));
        assert!(!store.is_trusted("device-2"));

        assert_eq!(store.all_trusted_devices().len(), 1);
    }
}
