use std::collections::{HashMap, VecDeque};

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use tokio::sync::mpsc;

use crate::protocol::{TrackerMessage, TrackerPeerInfo};

/// Maximum number of activity events to retain in the ring buffer.
const MAX_ACTIVITY_EVENTS: usize = 200;

/// A peer that has registered with the tracker.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RegisteredPeer {
    pub device_id: String,
    pub name: String,
    pub platform: String,
    pub protocol_version: u32,
    pub capabilities: Vec<String>,
    /// The public IP:port as seen by the tracker (determined from the WebSocket connection).
    pub public_addr: String,
    pub listen_port: u16,
    pub fingerprint: String,
    pub file_count: u32,
    pub registered_at: DateTime<Utc>,
    pub last_heartbeat: DateTime<Utc>,
}

/// A single activity event for the dashboard activity log.
#[derive(Debug, Clone, Serialize)]
pub struct ActivityEvent {
    pub timestamp: String,
    pub event_type: String,
    pub description: String,
}

/// Aggregate stats exposed via the /stats REST endpoint.
#[derive(Debug, Serialize)]
pub struct TrackerStats {
    pub total_peers: usize,
    pub active_relays: usize,
    pub uptime_seconds: u64,
    pub total_peers_seen: u64,
    pub messages_relayed: u64,
}

/// Manages the set of currently registered peers and their WebSocket channels.
pub struct PeerRegistry {
    peers: HashMap<String, RegisteredPeer>,
    /// Maps device_id -> unbounded sender for pushing JSON messages over the peer's WebSocket.
    websocket_senders: HashMap<String, mpsc::UnboundedSender<String>>,
    /// Instant the registry was created (for uptime calculation).
    started_at: std::time::Instant,
    /// Running count of all peers that have ever registered (not just currently connected).
    total_peers_seen: u64,
    /// Running count of relay data messages forwarded.
    messages_relayed: u64,
    /// Ring buffer of recent activity events for the dashboard.
    activity_log: VecDeque<ActivityEvent>,
}

impl PeerRegistry {
    pub fn new() -> Self {
        Self {
            peers: HashMap::new(),
            websocket_senders: HashMap::new(),
            started_at: std::time::Instant::now(),
            total_peers_seen: 0,
            messages_relayed: 0,
            activity_log: VecDeque::with_capacity(MAX_ACTIVITY_EVENTS),
        }
    }

    /// Record an activity event in the ring buffer.
    pub fn record_activity(&mut self, event_type: &str, description: &str) {
        if self.activity_log.len() >= MAX_ACTIVITY_EVENTS {
            self.activity_log.pop_front();
        }
        self.activity_log.push_back(ActivityEvent {
            timestamp: Utc::now().to_rfc3339(),
            event_type: event_type.to_string(),
            description: description.to_string(),
        });
    }

    /// Return the recent activity events (newest first).
    pub fn recent_activity(&self, limit: usize) -> Vec<ActivityEvent> {
        self.activity_log
            .iter()
            .rev()
            .take(limit)
            .cloned()
            .collect()
    }

    /// Increment the messages-relayed counter.
    pub fn increment_messages_relayed(&mut self) {
        self.messages_relayed += 1;
    }

    /// Register a new peer or update an existing one.
    ///
    /// Returns the peer's public address on success.
    pub fn register(
        &mut self,
        peer: RegisteredPeer,
        ws_tx: mpsc::UnboundedSender<String>,
    ) -> Result<String, String> {
        let device_id = peer.device_id.clone();
        let public_addr = peer.public_addr.clone();
        let name = peer.name.clone();
        let platform = peer.platform.clone();

        // Only count genuinely new peers (not re-registrations of existing ones).
        if !self.peers.contains_key(&device_id) {
            self.total_peers_seen += 1;
        }

        // Store the peer and its WebSocket sender.
        self.peers.insert(device_id.clone(), peer);
        self.websocket_senders.insert(device_id.clone(), ws_tx);

        log::info!(
            "Peer registered: {} ({}) from {} [{}]",
            name,
            device_id,
            public_addr,
            platform,
        );

        self.record_activity(
            "peer_connected",
            &format!("{} ({}) connected from {}", name, platform, public_addr),
        );

        // Notify all *other* connected peers that a new peer is online.
        let notification = TrackerMessage::PeerOnline {
            device_id: device_id.clone(),
            name,
            platform,
            public_addr: public_addr.clone(),
        };
        self.broadcast_except(&device_id, &notification);

        Ok(public_addr)
    }

    /// Update the heartbeat timestamp for a peer.
    ///
    /// Also refreshes fingerprint and file_count. Returns `true` if the peer was found.
    pub fn heartbeat(&mut self, device_id: &str, fingerprint: &str, file_count: u32) -> bool {
        if let Some(peer) = self.peers.get_mut(device_id) {
            peer.last_heartbeat = Utc::now();
            peer.fingerprint = fingerprint.to_string();
            peer.file_count = file_count;
            true
        } else {
            false
        }
    }

    /// Remove a peer from the registry and notify others.
    pub fn remove(&mut self, device_id: &str) {
        if let Some(peer) = self.peers.remove(device_id) {
            self.websocket_senders.remove(device_id);

            log::info!("Peer removed: {}", device_id);

            self.record_activity(
                "peer_disconnected",
                &format!("{} ({}) disconnected", peer.name, peer.platform),
            );

            let notification = TrackerMessage::PeerOffline {
                device_id: device_id.to_string(),
            };
            self.broadcast_except(device_id, &notification);
        }
    }

    /// Get a list of all registered peers, excluding the one with `exclude_device_id`.
    pub fn get_peer_list(&self, exclude_device_id: &str) -> Vec<TrackerPeerInfo> {
        self.peers
            .values()
            .filter(|p| p.device_id != exclude_device_id)
            .map(|p| TrackerPeerInfo {
                device_id: p.device_id.clone(),
                name: p.name.clone(),
                platform: p.platform.clone(),
                public_addr: p.public_addr.clone(),
                fingerprint: p.fingerprint.clone(),
                file_count: p.file_count,
                capabilities: p.capabilities.clone(),
                last_seen: p.last_heartbeat.timestamp(),
            })
            .collect()
    }

    /// Get all registered peers (for REST endpoint).
    pub fn all_peers(&self) -> Vec<TrackerPeerInfo> {
        self.peers
            .values()
            .map(|p| TrackerPeerInfo {
                device_id: p.device_id.clone(),
                name: p.name.clone(),
                platform: p.platform.clone(),
                public_addr: p.public_addr.clone(),
                fingerprint: p.fingerprint.clone(),
                file_count: p.file_count,
                capabilities: p.capabilities.clone(),
                last_seen: p.last_heartbeat.timestamp(),
            })
            .collect()
    }

    /// Evict peers whose last heartbeat is older than 90 seconds.
    pub fn evict_stale(&mut self) {
        let now = Utc::now();
        let stale_ids: Vec<String> = self
            .peers
            .iter()
            .filter(|(_, p)| {
                let elapsed = now.signed_duration_since(p.last_heartbeat);
                elapsed.num_seconds() > 90
            })
            .map(|(id, _)| id.clone())
            .collect();

        for id in stale_ids {
            log::info!("Evicting stale peer: {}", id);
            self.remove(&id);
        }
    }

    /// Get aggregate stats.
    pub fn stats(&self, active_relays: usize) -> TrackerStats {
        TrackerStats {
            total_peers: self.peers.len(),
            active_relays,
            uptime_seconds: self.started_at.elapsed().as_secs(),
            total_peers_seen: self.total_peers_seen,
            messages_relayed: self.messages_relayed,
        }
    }

    /// Send a message to a specific peer by device_id.
    ///
    /// Returns `true` if the send was successful.
    pub fn send_to(&self, device_id: &str, message: &TrackerMessage) -> bool {
        if let Some(tx) = self.websocket_senders.get(device_id) {
            match serde_json::to_string(message) {
                Ok(json) => tx.send(json).is_ok(),
                Err(e) => {
                    log::error!("Failed to serialize message for {}: {}", device_id, e);
                    false
                }
            }
        } else {
            false
        }
    }

    /// Check whether a device is currently registered.
    pub fn is_registered(&self, device_id: &str) -> bool {
        self.peers.contains_key(device_id)
    }

    /// Broadcast a message to all peers except the one specified.
    fn broadcast_except(&self, exclude_device_id: &str, message: &TrackerMessage) {
        let json = match serde_json::to_string(message) {
            Ok(j) => j,
            Err(e) => {
                log::error!("Failed to serialize broadcast message: {}", e);
                return;
            }
        };

        for (id, tx) in &self.websocket_senders {
            if id != exclude_device_id {
                // Best-effort delivery — if the channel is full or closed we just skip.
                let _ = tx.send(json.clone());
            }
        }
    }
}
