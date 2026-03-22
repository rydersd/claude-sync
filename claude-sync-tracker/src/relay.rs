use std::collections::HashMap;

use chrono::{DateTime, Utc};
use serde::Serialize;
use uuid::Uuid;

use crate::protocol::TrackerMessage;
use crate::registry::PeerRegistry;

/// A relay session that tunnels encrypted traffic between two peers through the tracker.
#[derive(Debug)]
pub struct RelaySession {
    pub relay_id: String,
    pub source_device_id: String,
    pub target_device_id: String,
    pub created_at: DateTime<Utc>,
    /// Total bytes of base64 payload forwarded through this relay.
    pub bytes_relayed: u64,
}

/// Serializable relay session info for the REST API.
#[derive(Debug, Serialize)]
pub struct RelaySessionInfo {
    pub relay_id: String,
    pub source_device_id: String,
    pub target_device_id: String,
    pub created_at: String,
    pub duration_seconds: i64,
    pub bytes_relayed: u64,
}

/// Manages active relay sessions for NAT traversal.
///
/// Relay data is opaque to the tracker — peers encrypt end-to-end and the tracker
/// simply forwards base64 payloads between the two endpoints of a relay.
pub struct RelayManager {
    sessions: HashMap<String, RelaySession>,
}

impl RelayManager {
    pub fn new() -> Self {
        Self {
            sessions: HashMap::new(),
        }
    }

    /// Create a new relay session between two peers.
    ///
    /// Both peers must be registered in the registry. Returns the relay_id on success.
    pub fn create_relay(
        &mut self,
        source_device_id: String,
        target_device_id: String,
        registry: &PeerRegistry,
    ) -> Result<String, String> {
        // Validate that both peers are registered.
        if !registry.is_registered(&source_device_id) {
            return Err(format!("Source peer {} is not registered", source_device_id));
        }
        if !registry.is_registered(&target_device_id) {
            return Err(format!("Target peer {} is not registered", target_device_id));
        }

        let relay_id = Uuid::new_v4().to_string();

        log::info!(
            "Creating relay {} : {} <-> {}",
            relay_id,
            source_device_id,
            target_device_id
        );

        self.sessions.insert(
            relay_id.clone(),
            RelaySession {
                relay_id: relay_id.clone(),
                source_device_id,
                target_device_id,
                created_at: Utc::now(),
                bytes_relayed: 0,
            },
        );

        Ok(relay_id)
    }

    /// Forward relay data from one peer to the other through the tracker.
    ///
    /// The `from_device_id` must be one of the two endpoints of the relay. The data
    /// is forwarded to the *other* endpoint via the registry's WebSocket sender.
    pub fn forward_data(
        &mut self,
        relay_id: &str,
        from_device_id: &str,
        payload_base64: &str,
        registry: &PeerRegistry,
    ) -> Result<(), String> {
        let session = self
            .sessions
            .get_mut(relay_id)
            .ok_or_else(|| format!("Relay session {} not found", relay_id))?;

        // Track the bytes forwarded (base64 payload length as a rough measure).
        session.bytes_relayed += payload_base64.len() as u64;

        // Determine the target (the other peer in the relay).
        let target_device_id = if session.source_device_id == from_device_id {
            session.target_device_id.clone()
        } else if session.target_device_id == from_device_id {
            session.source_device_id.clone()
        } else {
            return Err(format!(
                "Device {} is not part of relay {}",
                from_device_id, relay_id
            ));
        };

        let forward_msg = TrackerMessage::RelayData {
            relay_id: relay_id.to_string(),
            from_device_id: from_device_id.to_string(),
            payload_base64: payload_base64.to_string(),
        };

        if registry.send_to(&target_device_id, &forward_msg) {
            Ok(())
        } else {
            Err(format!(
                "Failed to forward relay data to {}",
                target_device_id
            ))
        }
    }

    /// Remove a relay session by ID.
    ///
    /// This is part of the public API for programmatic relay management. Not all code
    /// paths within the tracker binary itself call this — `cleanup_device` handles the
    /// common case of a peer disconnecting.
    #[allow(dead_code)]
    pub fn remove_relay(&mut self, relay_id: &str) {
        if self.sessions.remove(relay_id).is_some() {
            log::info!("Relay session removed: {}", relay_id);
        }
    }

    /// Clean up all relay sessions involving a disconnected device.
    /// Returns the number of sessions that were removed.
    pub fn cleanup_device_count(&mut self, device_id: &str) -> usize {
        let to_remove: Vec<String> = self
            .sessions
            .iter()
            .filter(|(_, s)| s.source_device_id == device_id || s.target_device_id == device_id)
            .map(|(id, _)| id.clone())
            .collect();

        let count = to_remove.len();

        for id in to_remove {
            if let Some(session) = self.sessions.remove(&id) {
                let age_secs = Utc::now()
                    .signed_duration_since(session.created_at)
                    .num_seconds();
                log::info!(
                    "Cleaning up relay {} (device {} disconnected, session was {}s old)",
                    session.relay_id,
                    device_id,
                    age_secs,
                );
            }
        }

        count
    }

    /// Return serializable info about all active relay sessions (for the REST API).
    pub fn all_sessions(&self) -> Vec<RelaySessionInfo> {
        let now = Utc::now();
        self.sessions
            .values()
            .map(|s| {
                let duration = now.signed_duration_since(s.created_at).num_seconds();
                RelaySessionInfo {
                    relay_id: s.relay_id.clone(),
                    source_device_id: s.source_device_id.clone(),
                    target_device_id: s.target_device_id.clone(),
                    created_at: s.created_at.to_rfc3339(),
                    duration_seconds: duration,
                    bytes_relayed: s.bytes_relayed,
                }
            })
            .collect()
    }

    /// Number of active relay sessions.
    pub fn active_count(&self) -> usize {
        self.sessions.len()
    }
}
