// ==========================================================================
// Tracker client - WebSocket connection to a Claude Sync tracker server
// Handles registration, heartbeat, peer list requests, and relay messages.
// The tracker enables WAN discovery and relayed connections between devices
// that cannot establish direct TCP connections.
// ==========================================================================

use futures::stream::{SplitSink, SplitStream};
use futures::{SinkExt, StreamExt};
use serde::{Deserialize, Serialize};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use tokio::sync::{mpsc, Mutex};
use tokio_tungstenite::tungstenite::Message as WsMessage;

/// Heartbeat interval for tracker connection (seconds).
const HEARTBEAT_INTERVAL_SECS: u64 = 30;

// -- Tracker Protocol Messages -----------------------------------------------
// These types match the tracker server's protocol (claude-sync-tracker).

/// All possible messages exchanged with the tracker server.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type")]
pub enum TrackerMessage {
    /// Register this device with the tracker.
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

    /// Acknowledgment from tracker after registration.
    #[serde(rename = "tracker_register_ack")]
    RegisterAck {
        success: bool,
        #[serde(skip_serializing_if = "Option::is_none")]
        public_addr: Option<String>,
        tracker_time: i64,
        #[serde(skip_serializing_if = "Option::is_none")]
        error: Option<String>,
    },

    /// Periodic heartbeat to keep the tracker registration alive.
    #[serde(rename = "tracker_heartbeat")]
    Heartbeat {
        device_id: String,
        fingerprint: String,
        file_count: u32,
    },

    /// Request the list of all online peers from the tracker.
    #[serde(rename = "tracker_peer_list_request")]
    PeerListRequest,

    /// Response containing all online peers.
    #[serde(rename = "tracker_peer_list_response")]
    PeerListResponse { peers: Vec<TrackerPeerInfo> },

    /// Notification that a new peer has come online.
    #[serde(rename = "tracker_peer_online")]
    PeerOnline {
        device_id: String,
        name: String,
        platform: String,
        public_addr: String,
    },

    /// Notification that a peer has gone offline.
    #[serde(rename = "tracker_peer_offline")]
    PeerOffline { device_id: String },

    /// Request a relay connection to a specific peer.
    #[serde(rename = "tracker_relay_request")]
    RelayRequest {
        target_device_id: String,
        source_device_id: String,
    },

    /// Acknowledgment of a relay request.
    #[serde(rename = "tracker_relay_ack")]
    RelayAck {
        accepted: bool,
        #[serde(skip_serializing_if = "Option::is_none")]
        relay_id: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        error: Option<String>,
    },

    /// Data forwarded through a relay connection.
    #[serde(rename = "tracker_relay_data")]
    RelayData {
        relay_id: String,
        from_device_id: String,
        payload_base64: String,
    },
}

/// Information about a peer as reported by the tracker.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TrackerPeerInfo {
    pub device_id: String,
    pub name: String,
    pub platform: String,
    pub public_addr: String,
    pub fingerprint: String,
    pub file_count: u32,
    pub capabilities: Vec<String>,
    pub last_seen: i64,
}

/// Events emitted by the tracker client for the application to handle.
pub enum TrackerEvent {
    /// A new peer came online on the tracker.
    PeerOnline(TrackerPeerInfo),
    /// A peer went offline on the tracker.
    PeerOffline(String),
    /// Data received through a relay connection.
    RelayData {
        relay_id: String,
        from_device_id: String,
        payload_base64: String,
    },
    /// Full peer list received from tracker.
    PeerListReceived(Vec<TrackerPeerInfo>),
    /// Connection to tracker was lost.
    Disconnected,
}

/// Type alias for the WebSocket stream type.
type WsStream = tokio_tungstenite::WebSocketStream<
    tokio_tungstenite::MaybeTlsStream<tokio::net::TcpStream>,
>;

/// Type alias for the WebSocket write half (send side).
type WsSender = SplitSink<WsStream, WsMessage>;

/// Type alias for the WebSocket read half (receive side).
type WsReader = SplitStream<WsStream>;

/// Client for connecting to a Claude Sync tracker server over WebSocket.
/// Manages registration, heartbeat, peer discovery, and relay forwarding.
pub struct TrackerClient {
    /// WebSocket URL of the tracker server (e.g., "wss://tracker.example.com/ws")
    tracker_url: String,
    /// This device's unique ID
    device_id: String,
    /// This device's human-readable name
    device_name: String,
    /// WebSocket send half (None when disconnected)
    ws_sender: Option<Arc<Mutex<WsSender>>>,
    /// Channel for sending events to the application
    event_tx: mpsc::UnboundedSender<TrackerEvent>,
    /// Atomic flag indicating whether we're connected
    is_connected: Arc<AtomicBool>,
    /// Handle to the heartbeat task (so we can abort on disconnect)
    heartbeat_handle: Option<tokio::task::JoinHandle<()>>,
    /// Handle to the receive loop task
    receive_handle: Option<tokio::task::JoinHandle<()>>,
}

impl TrackerClient {
    /// Create a new tracker client (not yet connected).
    pub fn new(
        tracker_url: String,
        device_id: String,
        device_name: String,
        event_tx: mpsc::UnboundedSender<TrackerEvent>,
    ) -> Self {
        Self {
            tracker_url,
            device_id,
            device_name,
            ws_sender: None,
            event_tx,
            is_connected: Arc::new(AtomicBool::new(false)),
            heartbeat_handle: None,
            receive_handle: None,
        }
    }

    /// Connect to the tracker, register this device, and start the
    /// heartbeat and receive loops. Returns the public address assigned
    /// by the tracker (if any).
    pub async fn connect(
        &mut self,
        fingerprint: &str,
        file_count: u32,
        listen_port: u16,
    ) -> Result<String, Box<dyn std::error::Error>> {
        if self.is_connected.load(Ordering::SeqCst) {
            return Err("Already connected to tracker".into());
        }

        log::info!("Connecting to tracker at {}", self.tracker_url);

        // Establish WebSocket connection
        let (ws_stream, _response) = tokio_tungstenite::connect_async(&self.tracker_url).await?;
        let (ws_write, ws_read) = ws_stream.split();
        let ws_sender = Arc::new(Mutex::new(ws_write));

        self.ws_sender = Some(Arc::clone(&ws_sender));
        self.is_connected.store(true, Ordering::SeqCst);

        // Send registration message
        let platform = crate::device_identity::get_platform();
        let register_msg = TrackerMessage::Register {
            device_id: self.device_id.clone(),
            name: self.device_name.clone(),
            platform,
            protocol_version: crate::protocol::PROTOCOL_VERSION,
            capabilities: vec![
                "file_watch".to_string(),
                "keepalive".to_string(),
                "tls".to_string(),
            ],
            listen_port,
            fingerprint: fingerprint.to_string(),
            file_count,
        };

        self.send_message(&register_msg).await?;

        // Start the receive loop in a background task
        let event_tx = self.event_tx.clone();
        let is_connected = Arc::clone(&self.is_connected);
        let receive_handle = tokio::spawn(async move {
            Self::receive_loop(ws_read, event_tx, is_connected).await;
        });
        self.receive_handle = Some(receive_handle);

        // Start the heartbeat timer
        let heartbeat_sender = Arc::clone(&ws_sender);
        let heartbeat_connected = Arc::clone(&self.is_connected);
        let heartbeat_device_id = self.device_id.clone();
        let heartbeat_fingerprint = fingerprint.to_string();
        let heartbeat_file_count = file_count;

        let heartbeat_handle = tokio::spawn(async move {
            Self::heartbeat_loop(
                heartbeat_sender,
                heartbeat_connected,
                heartbeat_device_id,
                heartbeat_fingerprint,
                heartbeat_file_count,
            )
            .await;
        });
        self.heartbeat_handle = Some(heartbeat_handle);

        log::info!("Connected to tracker and registered");

        // The public_addr will be delivered via RegisterAck through the event channel.
        // For now, return a placeholder indicating connection was established.
        Ok(self.tracker_url.clone())
    }

    /// Disconnect from the tracker server.
    pub async fn disconnect(&mut self) {
        self.is_connected.store(false, Ordering::SeqCst);

        // Abort background tasks
        if let Some(handle) = self.heartbeat_handle.take() {
            handle.abort();
        }
        if let Some(handle) = self.receive_handle.take() {
            handle.abort();
        }

        // Close the WebSocket
        if let Some(ref sender) = self.ws_sender {
            let mut guard: tokio::sync::MutexGuard<'_, WsSender> = sender.lock().await;
            let _ = guard.close().await;
        }
        self.ws_sender = None;

        log::info!("Disconnected from tracker");
    }

    /// Request the full peer list from the tracker.
    pub async fn request_peer_list(&self) -> Result<(), Box<dyn std::error::Error>> {
        self.send_message(&TrackerMessage::PeerListRequest).await
    }

    /// Request a relay connection to a specific peer through the tracker.
    pub async fn request_relay(
        &self,
        target_device_id: &str,
    ) -> Result<(), Box<dyn std::error::Error>> {
        let msg = TrackerMessage::RelayRequest {
            target_device_id: target_device_id.to_string(),
            source_device_id: self.device_id.clone(),
        };
        self.send_message(&msg).await
    }

    /// Send data through an established relay connection.
    pub async fn send_relay_data(
        &self,
        relay_id: &str,
        payload_base64: &str,
    ) -> Result<(), Box<dyn std::error::Error>> {
        let msg = TrackerMessage::RelayData {
            relay_id: relay_id.to_string(),
            from_device_id: self.device_id.clone(),
            payload_base64: payload_base64.to_string(),
        };
        self.send_message(&msg).await
    }

    /// Check if the client is currently connected to the tracker.
    pub fn is_connected(&self) -> bool {
        self.is_connected.load(Ordering::SeqCst)
    }

    /// Send a TrackerMessage over the WebSocket connection.
    async fn send_message(
        &self,
        msg: &TrackerMessage,
    ) -> Result<(), Box<dyn std::error::Error>> {
        let sender = self
            .ws_sender
            .as_ref()
            .ok_or("Not connected to tracker")?;

        let json = serde_json::to_string(msg)?;
        let mut guard: tokio::sync::MutexGuard<'_, WsSender> = sender.lock().await;
        guard.send(WsMessage::Text(json.into())).await?;

        Ok(())
    }

    /// Background heartbeat loop. Sends a heartbeat message every
    /// HEARTBEAT_INTERVAL_SECS seconds until disconnected.
    async fn heartbeat_loop(
        sender: Arc<Mutex<WsSender>>,
        is_connected: Arc<AtomicBool>,
        device_id: String,
        fingerprint: String,
        file_count: u32,
    ) {
        let mut interval = tokio::time::interval(
            tokio::time::Duration::from_secs(HEARTBEAT_INTERVAL_SECS),
        );

        loop {
            interval.tick().await;

            if !is_connected.load(Ordering::SeqCst) {
                break;
            }

            let heartbeat = TrackerMessage::Heartbeat {
                device_id: device_id.clone(),
                fingerprint: fingerprint.clone(),
                file_count,
            };

            let json = match serde_json::to_string(&heartbeat) {
                Ok(j) => j,
                Err(e) => {
                    log::warn!("Failed to serialize heartbeat: {}", e);
                    continue;
                }
            };

            let mut guard: tokio::sync::MutexGuard<'_, WsSender> = sender.lock().await;
            if let Err(e) = guard.send(WsMessage::Text(json.into())).await {
                log::warn!("Failed to send heartbeat: {}", e);
                break;
            }
        }
    }

    /// Background receive loop. Reads messages from the WebSocket and
    /// dispatches them as TrackerEvents through the event channel.
    async fn receive_loop(
        mut ws_read: WsReader,
        event_tx: mpsc::UnboundedSender<TrackerEvent>,
        is_connected: Arc<AtomicBool>,
    ) {
        while let Some(msg_result) = ws_read.next().await {
            match msg_result {
                Ok(WsMessage::Text(text)) => {
                    match serde_json::from_str::<TrackerMessage>(&text) {
                        Ok(tracker_msg) => {
                            Self::handle_tracker_message(tracker_msg, &event_tx);
                        }
                        Err(e) => {
                            log::warn!("Failed to parse tracker message: {}", e);
                        }
                    }
                }
                Ok(WsMessage::Close(_)) => {
                    log::info!("Tracker WebSocket closed by server");
                    break;
                }
                Ok(WsMessage::Ping(data)) => {
                    // Pong is handled automatically by tungstenite
                    log::debug!("Received ping from tracker ({} bytes)", data.len());
                }
                Err(e) => {
                    log::warn!("WebSocket receive error: {}", e);
                    break;
                }
                _ => {
                    // Binary or other message types - ignore
                }
            }
        }

        // Mark as disconnected and notify the application
        is_connected.store(false, Ordering::SeqCst);
        let _ = event_tx.send(TrackerEvent::Disconnected);
        log::info!("Tracker receive loop ended");
    }

    /// Dispatch a parsed TrackerMessage to the appropriate TrackerEvent.
    fn handle_tracker_message(
        msg: TrackerMessage,
        event_tx: &mpsc::UnboundedSender<TrackerEvent>,
    ) {
        match msg {
            TrackerMessage::RegisterAck {
                success,
                public_addr,
                error,
                ..
            } => {
                if success {
                    log::info!(
                        "Registered with tracker. Public addr: {}",
                        public_addr.as_deref().unwrap_or("unknown")
                    );
                } else {
                    log::warn!(
                        "Tracker registration failed: {}",
                        error.as_deref().unwrap_or("unknown error")
                    );
                }
            }
            TrackerMessage::PeerListResponse { peers } => {
                log::info!("Received peer list with {} peers", peers.len());
                let _ = event_tx.send(TrackerEvent::PeerListReceived(peers));
            }
            TrackerMessage::PeerOnline {
                device_id,
                name,
                platform,
                public_addr,
            } => {
                log::info!("Tracker: peer online - {} ({})", name, device_id);
                let _ = event_tx.send(TrackerEvent::PeerOnline(TrackerPeerInfo {
                    device_id,
                    name,
                    platform,
                    public_addr,
                    fingerprint: String::new(),
                    file_count: 0,
                    capabilities: Vec::new(),
                    last_seen: chrono::Utc::now().timestamp(),
                }));
            }
            TrackerMessage::PeerOffline { device_id } => {
                log::info!("Tracker: peer offline - {}", device_id);
                let _ = event_tx.send(TrackerEvent::PeerOffline(device_id));
            }
            TrackerMessage::RelayAck {
                accepted,
                relay_id,
                error,
            } => {
                if accepted {
                    log::info!(
                        "Relay established: {}",
                        relay_id.as_deref().unwrap_or("unknown")
                    );
                } else {
                    log::warn!(
                        "Relay request rejected: {}",
                        error.as_deref().unwrap_or("unknown")
                    );
                }
            }
            TrackerMessage::RelayData {
                relay_id,
                from_device_id,
                payload_base64,
            } => {
                let _ = event_tx.send(TrackerEvent::RelayData {
                    relay_id,
                    from_device_id,
                    payload_base64,
                });
            }
            // Register, Heartbeat, PeerListRequest, RelayRequest are client->server only
            _ => {
                log::debug!("Ignoring unexpected tracker message type");
            }
        }
    }
}
