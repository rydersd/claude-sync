// ==========================================================================
// Device pairing manager - 6-digit code exchange workflow
// When two devices want to pair over WAN, they exchange a 6-digit numeric
// code out-of-band (shown on screen, typed on the other device). Once
// verified, certificate fingerprints are exchanged and stored in the
// trust store for future authentication.
// ==========================================================================

use rand::Rng;
use serde::{Deserialize, Serialize};

use super::trust_store::{TrustStore, TrustedDevice};

/// A paired device record returned to the frontend.
/// Mirrors TrustedDevice but uses a simpler name for the IPC boundary.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PairedDevice {
    pub device_id: String,
    pub name: String,
    pub cert_fingerprint: String,
    /// Unix epoch seconds when the pairing was established
    pub paired_at: i64,
}

/// Manages the device pairing workflow using 6-digit codes.
/// Wraps the TrustStore for persistence and provides pairing logic.
pub struct PairingManager {
    trust_store: TrustStore,
}

impl PairingManager {
    /// Create a new PairingManager with a loaded trust store.
    pub fn new(trust_store: TrustStore) -> Self {
        Self { trust_store }
    }

    /// Generate a random 6-digit pairing code (000000 - 999999).
    /// This code should be displayed to the user and communicated
    /// to the peer device out-of-band (verbally, visually, etc.).
    pub fn generate_pairing_code() -> String {
        let mut rng = rand::thread_rng();
        format!("{:06}", rng.gen_range(0..1_000_000))
    }

    /// Check if a device is already paired (by device_id).
    pub fn is_device_paired(&self, device_id: &str) -> bool {
        self.trust_store.is_trusted(device_id)
    }

    /// Complete a pairing by storing the peer's identity in the trust store.
    /// Called after the 6-digit code has been verified on both sides.
    pub fn complete_pairing(
        &mut self,
        peer_device_id: String,
        peer_name: String,
        peer_cert_fingerprint: String,
    ) -> Result<(), Box<dyn std::error::Error>> {
        let now = chrono::Utc::now().timestamp();

        let device = TrustedDevice {
            device_id: peer_device_id,
            name: peer_name,
            cert_fingerprint: peer_cert_fingerprint,
            paired_at: now,
        };

        self.trust_store.add_trusted_device(device)?;
        Ok(())
    }

    /// Remove a pairing (unpair a device). Removes it from the trust store.
    pub fn unpair_device(&mut self, device_id: &str) -> Result<(), Box<dyn std::error::Error>> {
        self.trust_store.remove_trusted_device(device_id)?;
        Ok(())
    }

    /// Get all currently paired devices.
    pub fn paired_devices(&self) -> Vec<PairedDevice> {
        self.trust_store
            .all_trusted_devices()
            .into_iter()
            .map(|td| PairedDevice {
                device_id: td.device_id,
                name: td.name,
                cert_fingerprint: td.cert_fingerprint,
                paired_at: td.paired_at,
            })
            .collect()
    }

    /// Get a reference to the underlying trust store (for certificate validation).
    pub fn trust_store(&self) -> &TrustStore {
        &self.trust_store
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_pairing_code_format() {
        let code = PairingManager::generate_pairing_code();
        assert_eq!(code.len(), 6, "Pairing code should be 6 digits");
        assert!(
            code.chars().all(|c| c.is_ascii_digit()),
            "Pairing code should contain only digits"
        );
    }

    #[test]
    fn test_pairing_code_uniqueness() {
        // Generate several codes and check they're not all the same
        // (probabilistically; 6-digit codes have 1M possibilities)
        let codes: Vec<String> = (0..10)
            .map(|_| PairingManager::generate_pairing_code())
            .collect();
        let unique: std::collections::HashSet<&String> = codes.iter().collect();
        // With 10 draws from 1M, collision is extremely unlikely
        assert!(unique.len() > 1, "Pairing codes should be random");
    }
}
