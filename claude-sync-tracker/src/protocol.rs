use serde::{Deserialize, Serialize};

/// Information about a peer as seen by the tracker, sent in peer list responses.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TrackerPeerInfo {
    pub device_id: String,
    pub name: String,
    pub platform: String,
    pub public_addr: String,
    pub fingerprint: String,
    pub file_count: u32,
    pub capabilities: Vec<String>,
    /// Unix timestamp (seconds) of last heartbeat or registration.
    pub last_seen: i64,
}

/// All tracker protocol message types.
///
/// Messages are JSON-encoded and sent over WebSocket text frames.
/// The `type` field determines which variant is used (internally tagged).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type")]
pub enum TrackerMessage {
    /// Peer registers with the tracker.
    #[serde(rename = "tracker_register")]
    Register {
        device_id: String,
        name: String,
        platform: String,
        protocol_version: u32,
        capabilities: Vec<String>,
        listen_port: u16,
        fingerprint: String,
        file_count: u32,
    },

    /// Tracker confirms registration.
    #[serde(rename = "tracker_register_ack")]
    RegisterAck {
        success: bool,
        #[serde(skip_serializing_if = "Option::is_none")]
        public_addr: Option<String>,
        /// Tracker's current time as Unix timestamp (seconds).
        tracker_time: i64,
        #[serde(skip_serializing_if = "Option::is_none")]
        error: Option<String>,
    },

    /// Peer heartbeat — sent every 30s, peers evicted after 90s of silence.
    #[serde(rename = "tracker_heartbeat")]
    Heartbeat {
        device_id: String,
        fingerprint: String,
        file_count: u32,
    },

    /// Peer requests the list of other registered peers.
    #[serde(rename = "tracker_peer_list_request")]
    PeerListRequest {
        /// The requesting peer's device ID so the tracker can exclude them.
        #[serde(default)]
        device_id: Option<String>,
    },

    /// Tracker responds with the peer list.
    #[serde(rename = "tracker_peer_list_response")]
    PeerListResponse { peers: Vec<TrackerPeerInfo> },

    /// Real-time notification: a peer came online.
    #[serde(rename = "tracker_peer_online")]
    PeerOnline {
        device_id: String,
        name: String,
        platform: String,
        public_addr: String,
    },

    /// Real-time notification: a peer went offline.
    #[serde(rename = "tracker_peer_offline")]
    PeerOffline { device_id: String },

    /// Request to relay traffic through the tracker (NAT traversal).
    #[serde(rename = "tracker_relay_request")]
    RelayRequest {
        target_device_id: String,
        source_device_id: String,
    },

    /// Relay request acknowledgment.
    #[serde(rename = "tracker_relay_ack")]
    RelayAck {
        accepted: bool,
        #[serde(skip_serializing_if = "Option::is_none")]
        relay_id: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        error: Option<String>,
    },

    /// Encrypted relay data forwarded between peers.
    /// The payload is opaque to the tracker (end-to-end encrypted by peers).
    #[serde(rename = "tracker_relay_data")]
    RelayData {
        relay_id: String,
        from_device_id: String,
        /// Base64-encoded encrypted payload.
        payload_base64: String,
    },
}
