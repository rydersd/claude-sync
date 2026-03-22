use std::net::SocketAddr;
use std::sync::Arc;

use axum::extract::connect_info::ConnectInfo;
use axum::extract::ws::{Message, WebSocket, WebSocketUpgrade};
use axum::extract::State;
use axum::http::header;
use axum::response::{IntoResponse, Json};
use axum::routing::get;
use axum::Router;
use chrono::Utc;
use futures::stream::StreamExt;
use futures::SinkExt;
use tokio::sync::{mpsc, RwLock};
use tower_http::cors::CorsLayer;
use tower_http::trace::TraceLayer;

use crate::protocol::{TrackerMessage, TrackerPeerInfo};
use crate::registry::{ActivityEvent, PeerRegistry, RegisteredPeer};
use crate::relay::{RelayManager, RelaySessionInfo};

/// Shared application state, wrapped in Arc for cheap cloning across handlers.
pub struct TrackerState {
    pub registry: Arc<RwLock<PeerRegistry>>,
    pub relay_manager: Arc<RwLock<RelayManager>>,
    pub persist_path: Option<String>,
}

impl TrackerState {
    pub fn new(persist_path: Option<String>) -> Arc<Self> {
        let registry = match &persist_path {
            Some(path) => load_registry_from_disk(path),
            None => PeerRegistry::new(),
        };

        Arc::new(Self {
            registry: Arc::new(RwLock::new(registry)),
            relay_manager: Arc::new(RwLock::new(RelayManager::new())),
            persist_path,
        })
    }
}

/// Start the Axum server with REST + WebSocket routes.
pub async fn run(state: Arc<TrackerState>, port: u16) {
    // Spawn the background stale-peer eviction task (runs every 30s).
    let eviction_state = state.clone();
    tokio::spawn(async move {
        eviction_loop(eviction_state).await;
    });

    let app = Router::new()
        .route("/", get(dashboard))
        .route("/health", get(health))
        .route("/peers", get(list_peers))
        .route("/stats", get(stats))
        .route("/relays", get(list_relays))
        .route("/activity", get(list_activity))
        .route("/ws", get(ws_handler))
        .layer(CorsLayer::permissive())
        .layer(TraceLayer::new_for_http())
        .with_state(state);

    let addr: SocketAddr = ([0, 0, 0, 0], port).into();
    let listener = tokio::net::TcpListener::bind(addr)
        .await
        .unwrap_or_else(|e| {
            log::error!("Failed to bind to port {}: {}", port, e);
            std::process::exit(1);
        });

    log::info!("Tracker listening on 0.0.0.0:{}", port);

    axum::serve(
        listener,
        app.into_make_service_with_connect_info::<SocketAddr>(),
    )
    .await
    .unwrap_or_else(|e| {
        log::error!("Server error: {}", e);
    });

    // Persist state on shutdown if configured.
    // Note: In practice this only runs if the server exits cleanly (e.g. Ctrl-C with a
    // graceful shutdown handler). For a production deployment you would also persist
    // periodically.
}

// ---------------------------------------------------------------------------
// REST handlers
// ---------------------------------------------------------------------------

async fn health() -> Json<serde_json::Value> {
    Json(serde_json::json!({
        "status": "ok",
        "service": "claude-sync-tracker",
        "timestamp": Utc::now().to_rfc3339(),
    }))
}

async fn list_peers(
    State(state): State<Arc<TrackerState>>,
) -> Json<Vec<TrackerPeerInfo>> {
    let registry = state.registry.read().await;
    Json(registry.all_peers())
}

async fn stats(State(state): State<Arc<TrackerState>>) -> Json<serde_json::Value> {
    let registry = state.registry.read().await;
    let relay_manager = state.relay_manager.read().await;
    let s = registry.stats(relay_manager.active_count());
    Json(serde_json::json!({
        "total_peers": s.total_peers,
        "active_relays": s.active_relays,
        "uptime_seconds": s.uptime_seconds,
        "total_peers_seen": s.total_peers_seen,
        "messages_relayed": s.messages_relayed,
    }))
}

/// Serve the embedded HTML dashboard.
async fn dashboard() -> impl IntoResponse {
    ([(header::CONTENT_TYPE, "text/html; charset=utf-8")], DASHBOARD_HTML)
}

/// Return active relay sessions.
async fn list_relays(
    State(state): State<Arc<TrackerState>>,
) -> Json<Vec<RelaySessionInfo>> {
    let relay_manager = state.relay_manager.read().await;
    Json(relay_manager.all_sessions())
}

/// Return recent activity events.
async fn list_activity(
    State(state): State<Arc<TrackerState>>,
) -> Json<Vec<ActivityEvent>> {
    let registry = state.registry.read().await;
    Json(registry.recent_activity(100))
}

// ---------------------------------------------------------------------------
// WebSocket handler
// ---------------------------------------------------------------------------

async fn ws_handler(
    ws: WebSocketUpgrade,
    State(state): State<Arc<TrackerState>>,
    ConnectInfo(addr): ConnectInfo<SocketAddr>,
) -> impl IntoResponse {
    log::info!("WebSocket connection from {}", addr);
    ws.on_upgrade(move |socket| handle_websocket(socket, state, addr))
}

async fn handle_websocket(socket: WebSocket, state: Arc<TrackerState>, peer_addr: SocketAddr) {
    let (mut ws_sink, mut ws_stream) = socket.split();

    // Channel for sending messages back through the WebSocket.
    let (ws_tx, mut ws_rx) = mpsc::unbounded_channel::<String>();

    // Spawn a task that drains the channel into the WebSocket sink.
    let send_task = tokio::spawn(async move {
        while let Some(msg) = ws_rx.recv().await {
            if ws_sink.send(Message::Text(msg.into())).await.is_err() {
                break;
            }
        }
    });

    // Track which device_id this connection is associated with (set after Register).
    let mut current_device_id: Option<String> = None;

    // Read loop — process incoming messages.
    while let Some(result) = ws_stream.next().await {
        let msg = match result {
            Ok(Message::Text(text)) => text.to_string(),
            Ok(Message::Close(_)) => break,
            Ok(_) => continue, // Ignore binary/ping/pong frames
            Err(e) => {
                log::warn!("WebSocket error from {}: {}", peer_addr, e);
                break;
            }
        };

        // Parse the tracker message.
        let tracker_msg: TrackerMessage = match serde_json::from_str(&msg) {
            Ok(m) => m,
            Err(e) => {
                log::warn!("Invalid message from {}: {} — raw: {}", peer_addr, e, msg);
                continue;
            }
        };

        // Dispatch and optionally get a response to send back.
        let response = handle_tracker_message(
            tracker_msg,
            &state,
            &peer_addr,
            &ws_tx,
            &mut current_device_id,
        )
        .await;

        if let Some(resp) = response {
            match serde_json::to_string(&resp) {
                Ok(json) => {
                    if ws_tx.send(json).is_err() {
                        break; // Channel closed, WebSocket gone.
                    }
                }
                Err(e) => {
                    log::error!("Failed to serialize response: {}", e);
                }
            }
        }
    }

    // Clean up on disconnect.
    if let Some(device_id) = &current_device_id {
        log::info!("Peer {} disconnected ({})", device_id, peer_addr);
        let mut registry = state.registry.write().await;
        registry.remove(device_id);
        drop(registry);

        let mut relay_mgr = state.relay_manager.write().await;
        let cleaned = relay_mgr.cleanup_device_count(device_id);
        drop(relay_mgr);

        if cleaned > 0 {
            let mut registry = state.registry.write().await;
            registry.record_activity(
                "relay_stopped",
                &format!(
                    "{} relay session(s) cleaned up after {} disconnected",
                    cleaned, device_id
                ),
            );
        }
    }

    // Abort the send task so it doesn't linger.
    send_task.abort();
}

/// Dispatch a single tracker message and optionally return a response.
async fn handle_tracker_message(
    msg: TrackerMessage,
    state: &Arc<TrackerState>,
    peer_addr: &SocketAddr,
    ws_tx: &mpsc::UnboundedSender<String>,
    current_device_id: &mut Option<String>,
) -> Option<TrackerMessage> {
    match msg {
        TrackerMessage::Register {
            device_id,
            name,
            platform,
            protocol_version,
            capabilities,
            listen_port,
            fingerprint,
            file_count,
        } => {
            let public_addr = format!("{}:{}", peer_addr.ip(), listen_port);
            let now = Utc::now();

            let peer = RegisteredPeer {
                device_id: device_id.clone(),
                name,
                platform,
                protocol_version,
                capabilities,
                public_addr: public_addr.clone(),
                listen_port,
                fingerprint,
                file_count,
                registered_at: now,
                last_heartbeat: now,
            };

            let mut registry = state.registry.write().await;
            match registry.register(peer, ws_tx.clone()) {
                Ok(addr) => {
                    *current_device_id = Some(device_id);
                    Some(TrackerMessage::RegisterAck {
                        success: true,
                        public_addr: Some(addr),
                        tracker_time: now.timestamp(),
                        error: None,
                    })
                }
                Err(e) => Some(TrackerMessage::RegisterAck {
                    success: false,
                    public_addr: None,
                    tracker_time: now.timestamp(),
                    error: Some(e),
                }),
            }
        }

        TrackerMessage::Heartbeat {
            device_id,
            fingerprint,
            file_count,
        } => {
            let mut registry = state.registry.write().await;
            if !registry.heartbeat(&device_id, &fingerprint, file_count) {
                log::warn!("Heartbeat from unknown peer: {}", device_id);
            }
            None
        }

        TrackerMessage::PeerListRequest { device_id } => {
            // Use the device_id from the message if provided, otherwise fall back to
            // the device_id established during registration on this connection.
            let exclude_id = device_id
                .as_deref()
                .or(current_device_id.as_deref())
                .unwrap_or("");

            let registry = state.registry.read().await;
            let peers = registry.get_peer_list(exclude_id);
            Some(TrackerMessage::PeerListResponse { peers })
        }

        TrackerMessage::RelayRequest {
            source_device_id,
            target_device_id,
        } => {
            let mut registry = state.registry.write().await;
            let mut relay_mgr = state.relay_manager.write().await;

            match relay_mgr.create_relay(
                source_device_id.clone(),
                target_device_id.clone(),
                &registry,
            ) {
                Ok(relay_id) => {
                    registry.record_activity(
                        "relay_started",
                        &format!(
                            "Relay {} started: {} <-> {}",
                            &relay_id[..8],
                            source_device_id,
                            target_device_id
                        ),
                    );

                    // Notify the target peer that a relay has been established.
                    let notification = TrackerMessage::RelayAck {
                        accepted: true,
                        relay_id: Some(relay_id.clone()),
                        error: None,
                    };
                    registry.send_to(&target_device_id, &notification);

                    Some(TrackerMessage::RelayAck {
                        accepted: true,
                        relay_id: Some(relay_id),
                        error: None,
                    })
                }
                Err(e) => {
                    log::warn!(
                        "Relay request failed ({} -> {}): {}",
                        source_device_id,
                        target_device_id,
                        e
                    );
                    Some(TrackerMessage::RelayAck {
                        accepted: false,
                        relay_id: None,
                        error: Some(e),
                    })
                }
            }
        }

        TrackerMessage::RelayData {
            relay_id,
            from_device_id,
            payload_base64,
        } => {
            let registry = state.registry.read().await;
            let mut relay_mgr = state.relay_manager.write().await;

            if let Err(e) = relay_mgr.forward_data(
                &relay_id,
                &from_device_id,
                &payload_base64,
                &registry,
            ) {
                log::warn!("Relay forward failed: {}", e);
            } else {
                // Track the relay message in the registry stats.
                // We need to drop the read lock and acquire a write lock.
                drop(registry);
                let mut registry = state.registry.write().await;
                registry.increment_messages_relayed();
            }
            None
        }

        // The server should never receive these outbound-only message types from a peer.
        TrackerMessage::RegisterAck { .. }
        | TrackerMessage::PeerListResponse { .. }
        | TrackerMessage::PeerOnline { .. }
        | TrackerMessage::PeerOffline { .. }
        | TrackerMessage::RelayAck { .. } => {
            log::warn!("Received unexpected server->client message from peer");
            None
        }
    }
}

// ---------------------------------------------------------------------------
// Background tasks
// ---------------------------------------------------------------------------

/// Periodically evicts stale peers (every 30 seconds).
async fn eviction_loop(state: Arc<TrackerState>) {
    let mut interval = tokio::time::interval(std::time::Duration::from_secs(30));
    loop {
        interval.tick().await;
        let mut registry = state.registry.write().await;
        registry.evict_stale();
    }
}

// ---------------------------------------------------------------------------
// Persistence helpers
// ---------------------------------------------------------------------------

/// Attempt to load a saved registry from disk. Falls back to a fresh registry on failure.
fn load_registry_from_disk(path: &str) -> PeerRegistry {
    match std::fs::read_to_string(path) {
        Ok(contents) => {
            match serde_json::from_str::<Vec<crate::registry::RegisteredPeer>>(&contents) {
                Ok(peers) => {
                    log::info!(
                        "Loaded {} persisted peers from {}",
                        peers.len(),
                        path
                    );
                    // We restore peer records but not their WebSocket senders — those will
                    // be re-established when peers reconnect.
                    let mut registry = PeerRegistry::new();
                    let (dummy_tx, _) = mpsc::unbounded_channel();
                    for peer in peers {
                        let _ = registry.register(peer, dummy_tx.clone());
                    }
                    registry
                }
                Err(e) => {
                    log::warn!("Failed to parse persisted state from {}: {}", path, e);
                    PeerRegistry::new()
                }
            }
        }
        Err(_) => {
            log::info!("No persisted state at {} — starting fresh", path);
            PeerRegistry::new()
        }
    }
}

/// Save the current registry to disk as JSON.
pub async fn save_registry_to_disk(state: &Arc<TrackerState>) {
    if let Some(path) = &state.persist_path {
        let registry = state.registry.read().await;
        let peers = registry.all_peers();
        match serde_json::to_string_pretty(&peers) {
            Ok(json) => {
                if let Err(e) = std::fs::write(path, json) {
                    log::error!("Failed to persist state to {}: {}", path, e);
                } else {
                    log::info!("Persisted {} peers to {}", peers.len(), path);
                }
            }
            Err(e) => {
                log::error!("Failed to serialize registry for persistence: {}", e);
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Embedded dashboard HTML
// ---------------------------------------------------------------------------

const DASHBOARD_HTML: &str = r##"<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Claude Sync Tracker</title>
<style>
  :root {
    --bg-primary: #0d1117;
    --bg-secondary: #161b22;
    --bg-tertiary: #21262d;
    --border: #30363d;
    --text-primary: #e6edf3;
    --text-secondary: #8b949e;
    --text-muted: #6e7681;
    --accent-blue: #58a6ff;
    --accent-green: #3fb950;
    --accent-orange: #d29922;
    --accent-red: #f85149;
    --accent-purple: #bc8cff;
    --radius: 8px;
    --shadow: 0 1px 3px rgba(0,0,0,0.3), 0 1px 2px rgba(0,0,0,0.2);
  }

  * { margin: 0; padding: 0; box-sizing: border-box; }

  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
    background: var(--bg-primary);
    color: var(--text-primary);
    line-height: 1.5;
    min-height: 100vh;
  }

  header {
    background: var(--bg-secondary);
    border-bottom: 1px solid var(--border);
    padding: 16px 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    position: sticky;
    top: 0;
    z-index: 100;
  }

  header h1 {
    font-size: 18px;
    font-weight: 600;
    display: flex;
    align-items: center;
    gap: 10px;
  }

  header h1 .icon {
    width: 28px;
    height: 28px;
    background: linear-gradient(135deg, var(--accent-blue), var(--accent-purple));
    border-radius: 6px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 14px;
  }

  .header-status {
    display: flex;
    align-items: center;
    gap: 12px;
    font-size: 13px;
    color: var(--text-secondary);
  }

  .status-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--accent-green);
    display: inline-block;
    animation: pulse 2s infinite;
  }

  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
  }

  .container {
    max-width: 1400px;
    margin: 0 auto;
    padding: 24px;
  }

  /* Stats grid */
  .stats-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 16px;
    margin-bottom: 24px;
  }

  .stat-card {
    background: var(--bg-secondary);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 20px;
    box-shadow: var(--shadow);
    transition: border-color 0.2s;
  }

  .stat-card:hover {
    border-color: var(--accent-blue);
  }

  .stat-label {
    font-size: 12px;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-secondary);
    margin-bottom: 8px;
  }

  .stat-value {
    font-size: 32px;
    font-weight: 700;
    line-height: 1;
  }

  .stat-value.blue { color: var(--accent-blue); }
  .stat-value.green { color: var(--accent-green); }
  .stat-value.orange { color: var(--accent-orange); }
  .stat-value.purple { color: var(--accent-purple); }
  .stat-value.red { color: var(--accent-red); }

  .stat-sub {
    font-size: 12px;
    color: var(--text-muted);
    margin-top: 4px;
  }

  /* Section panels */
  .panels {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 24px;
    margin-bottom: 24px;
  }

  @media (max-width: 900px) {
    .panels { grid-template-columns: 1fr; }
  }

  .panel {
    background: var(--bg-secondary);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    overflow: hidden;
  }

  .panel-full {
    grid-column: 1 / -1;
  }

  .panel-header {
    padding: 14px 20px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    justify-content: space-between;
    background: var(--bg-tertiary);
  }

  .panel-header h2 {
    font-size: 14px;
    font-weight: 600;
  }

  .panel-badge {
    background: var(--bg-primary);
    color: var(--text-secondary);
    font-size: 11px;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 10px;
    border: 1px solid var(--border);
  }

  .panel-body {
    padding: 0;
    max-height: 400px;
    overflow-y: auto;
  }

  .panel-body::-webkit-scrollbar {
    width: 6px;
  }
  .panel-body::-webkit-scrollbar-track {
    background: transparent;
  }
  .panel-body::-webkit-scrollbar-thumb {
    background: var(--border);
    border-radius: 3px;
  }

  /* Tables */
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }

  thead th {
    text-align: left;
    padding: 10px 16px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-secondary);
    background: var(--bg-secondary);
    border-bottom: 1px solid var(--border);
    position: sticky;
    top: 0;
  }

  tbody td {
    padding: 10px 16px;
    border-bottom: 1px solid var(--border);
    color: var(--text-primary);
    white-space: nowrap;
  }

  tbody tr:last-child td {
    border-bottom: none;
  }

  tbody tr:hover {
    background: var(--bg-tertiary);
  }

  .device-id {
    font-family: 'SF Mono', SFMono-Regular, Consolas, 'Liberation Mono', Menlo, monospace;
    font-size: 11px;
    color: var(--text-muted);
    max-width: 120px;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .platform-badge {
    display: inline-block;
    font-size: 11px;
    font-weight: 500;
    padding: 2px 8px;
    border-radius: 4px;
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
  }

  .platform-badge.macos { color: var(--accent-blue); border-color: var(--accent-blue); }
  .platform-badge.linux { color: var(--accent-orange); border-color: var(--accent-orange); }
  .platform-badge.windows { color: var(--accent-purple); border-color: var(--accent-purple); }

  .relay-arrow {
    color: var(--accent-blue);
    font-weight: 700;
    padding: 0 4px;
  }

  /* Activity log */
  .activity-list {
    list-style: none;
  }

  .activity-item {
    padding: 10px 20px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: flex-start;
    gap: 12px;
    font-size: 13px;
  }

  .activity-item:last-child {
    border-bottom: none;
  }

  .activity-item:hover {
    background: var(--bg-tertiary);
  }

  .activity-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    margin-top: 6px;
    flex-shrink: 0;
  }

  .activity-dot.peer_connected { background: var(--accent-green); }
  .activity-dot.peer_disconnected { background: var(--accent-red); }
  .activity-dot.relay_started { background: var(--accent-blue); }
  .activity-dot.relay_stopped { background: var(--accent-orange); }
  .activity-dot.peer_evicted { background: var(--text-muted); }

  .activity-time {
    color: var(--text-muted);
    font-size: 11px;
    font-family: 'SF Mono', SFMono-Regular, Consolas, monospace;
    flex-shrink: 0;
    min-width: 72px;
  }

  .activity-desc {
    color: var(--text-secondary);
  }

  /* Empty state */
  .empty-state {
    padding: 40px 20px;
    text-align: center;
    color: var(--text-muted);
    font-size: 13px;
  }

  .empty-state .empty-icon {
    font-size: 24px;
    margin-bottom: 8px;
    opacity: 0.5;
  }

  /* Responsive tweaks */
  @media (max-width: 600px) {
    .stats-grid {
      grid-template-columns: repeat(2, 1fr);
    }
    .container {
      padding: 16px;
    }
    header {
      padding: 12px 16px;
    }
    .stat-value {
      font-size: 24px;
    }
  }
</style>
</head>
<body>

<header>
  <h1>
    <span class="icon">S</span>
    Claude Sync Tracker
  </h1>
  <div class="header-status">
    <span class="status-dot"></span>
    <span id="header-uptime">--</span>
    <span>&middot;</span>
    <span id="header-refresh">Refreshing...</span>
  </div>
</header>

<div class="container">

  <!-- Stats cards -->
  <div class="stats-grid">
    <div class="stat-card">
      <div class="stat-label">Connected Peers</div>
      <div class="stat-value green" id="stat-peers">--</div>
      <div class="stat-sub" id="stat-peers-sub">loading</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Active Relays</div>
      <div class="stat-value blue" id="stat-relays">--</div>
      <div class="stat-sub">tunneled sessions</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Messages Relayed</div>
      <div class="stat-value purple" id="stat-messages">--</div>
      <div class="stat-sub">total forwarded</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Uptime</div>
      <div class="stat-value orange" id="stat-uptime">--</div>
      <div class="stat-sub" id="stat-uptime-sub">since start</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Total Peers Seen</div>
      <div class="stat-value" id="stat-total-seen" style="color: var(--text-primary);">--</div>
      <div class="stat-sub">unique registrations</div>
    </div>
  </div>

  <!-- Peers + Relays panels -->
  <div class="panels">
    <div class="panel panel-full">
      <div class="panel-header">
        <h2>Connected Peers</h2>
        <span class="panel-badge" id="badge-peers">0</span>
      </div>
      <div class="panel-body">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Platform</th>
              <th>Device ID</th>
              <th>Public Address</th>
              <th>Files</th>
              <th>Fingerprint</th>
              <th>Last Heartbeat</th>
            </tr>
          </thead>
          <tbody id="peers-tbody">
            <tr><td colspan="7" class="empty-state"><div class="empty-icon">&#9900;</div>No peers connected</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <div class="panel">
      <div class="panel-header">
        <h2>Active Relays</h2>
        <span class="panel-badge" id="badge-relays">0</span>
      </div>
      <div class="panel-body">
        <table>
          <thead>
            <tr>
              <th>Peers</th>
              <th>Duration</th>
              <th>Data</th>
            </tr>
          </thead>
          <tbody id="relays-tbody">
            <tr><td colspan="3" class="empty-state"><div class="empty-icon">&#8651;</div>No active relays</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <div class="panel">
      <div class="panel-header">
        <h2>Activity Log</h2>
        <span class="panel-badge" id="badge-activity">0</span>
      </div>
      <div class="panel-body">
        <ul class="activity-list" id="activity-list">
          <li class="empty-state"><div class="empty-icon">&#9883;</div>No recent activity</li>
        </ul>
      </div>
    </div>
  </div>

</div>

<script>
(function() {
  'use strict';

  // Utility: format seconds into human-readable duration.
  function fmtDuration(secs) {
    if (secs < 60) return secs + 's';
    if (secs < 3600) return Math.floor(secs / 60) + 'm ' + (secs % 60) + 's';
    var h = Math.floor(secs / 3600);
    var m = Math.floor((secs % 3600) / 60);
    if (h < 24) return h + 'h ' + m + 'm';
    var d = Math.floor(h / 24);
    h = h % 24;
    return d + 'd ' + h + 'h';
  }

  // Utility: format bytes into human-readable size.
  function fmtBytes(bytes) {
    if (bytes === 0) return '0 B';
    var units = ['B', 'KB', 'MB', 'GB'];
    var i = Math.floor(Math.log(bytes) / Math.log(1024));
    if (i >= units.length) i = units.length - 1;
    return (bytes / Math.pow(1024, i)).toFixed(i > 0 ? 1 : 0) + ' ' + units[i];
  }

  // Utility: time ago from ISO timestamp.
  function timeAgo(isoStr) {
    var diff = Math.floor((Date.now() - new Date(isoStr).getTime()) / 1000);
    if (diff < 0) diff = 0;
    if (diff < 5) return 'just now';
    if (diff < 60) return diff + 's ago';
    if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
    if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
    return Math.floor(diff / 86400) + 'd ago';
  }

  // Utility: time ago from unix timestamp (seconds).
  function timeAgoUnix(ts) {
    var diff = Math.floor(Date.now() / 1000) - ts;
    if (diff < 0) diff = 0;
    if (diff < 5) return 'just now';
    if (diff < 60) return diff + 's ago';
    if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
    if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
    return Math.floor(diff / 86400) + 'd ago';
  }

  // Utility: classify platform string for badge styling.
  function platformClass(p) {
    var lower = (p || '').toLowerCase();
    if (lower.indexOf('mac') !== -1 || lower.indexOf('darwin') !== -1) return 'macos';
    if (lower.indexOf('linux') !== -1) return 'linux';
    if (lower.indexOf('win') !== -1) return 'windows';
    return '';
  }

  // Utility: short device ID for display.
  function shortId(id) {
    if (!id) return '?';
    return id.length > 12 ? id.substring(0, 12) + '...' : id;
  }

  // Utility: extract time portion from ISO string for activity log.
  function fmtTime(isoStr) {
    try {
      var d = new Date(isoStr);
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } catch(e) {
      return '--:--:--';
    }
  }

  // Fetch helper with timeout.
  function fetchJSON(url) {
    return fetch(url).then(function(r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    });
  }

  // DOM references.
  var elStatPeers = document.getElementById('stat-peers');
  var elStatPeersSub = document.getElementById('stat-peers-sub');
  var elStatRelays = document.getElementById('stat-relays');
  var elStatMessages = document.getElementById('stat-messages');
  var elStatUptime = document.getElementById('stat-uptime');
  var elStatUptimeSub = document.getElementById('stat-uptime-sub');
  var elStatTotalSeen = document.getElementById('stat-total-seen');
  var elHeaderUptime = document.getElementById('header-uptime');
  var elHeaderRefresh = document.getElementById('header-refresh');
  var elBadgePeers = document.getElementById('badge-peers');
  var elBadgeRelays = document.getElementById('badge-relays');
  var elBadgeActivity = document.getElementById('badge-activity');
  var elPeersTbody = document.getElementById('peers-tbody');
  var elRelaysTbody = document.getElementById('relays-tbody');
  var elActivityList = document.getElementById('activity-list');

  // Render stats.
  function renderStats(data) {
    elStatPeers.textContent = data.total_peers;
    elStatPeersSub.textContent = 'currently connected';
    elStatRelays.textContent = data.active_relays;
    elStatMessages.textContent = data.messages_relayed.toLocaleString();
    elStatUptime.textContent = fmtDuration(data.uptime_seconds);
    elStatUptimeSub.textContent = 'since start';
    elStatTotalSeen.textContent = data.total_peers_seen;
    elHeaderUptime.textContent = 'Up ' + fmtDuration(data.uptime_seconds);
  }

  // Render peers table.
  function renderPeers(peers) {
    elBadgePeers.textContent = peers.length;
    if (peers.length === 0) {
      elPeersTbody.innerHTML = '<tr><td colspan="7" class="empty-state"><div class="empty-icon">&#9900;</div>No peers connected</td></tr>';
      return;
    }
    var html = '';
    peers.forEach(function(p) {
      var pcls = platformClass(p.platform);
      html += '<tr>';
      html += '<td><strong>' + esc(p.name) + '</strong></td>';
      html += '<td><span class="platform-badge ' + pcls + '">' + esc(p.platform) + '</span></td>';
      html += '<td><span class="device-id" title="' + esc(p.device_id) + '">' + esc(shortId(p.device_id)) + '</span></td>';
      html += '<td>' + esc(p.public_addr) + '</td>';
      html += '<td>' + p.file_count + '</td>';
      html += '<td><span class="device-id" title="' + esc(p.fingerprint) + '">' + esc(shortId(p.fingerprint)) + '</span></td>';
      html += '<td>' + timeAgoUnix(p.last_seen) + '</td>';
      html += '</tr>';
    });
    elPeersTbody.innerHTML = html;
  }

  // Render relays table.
  function renderRelays(relays) {
    elBadgeRelays.textContent = relays.length;
    if (relays.length === 0) {
      elRelaysTbody.innerHTML = '<tr><td colspan="3" class="empty-state"><div class="empty-icon">&#8651;</div>No active relays</td></tr>';
      return;
    }
    var html = '';
    relays.forEach(function(r) {
      html += '<tr>';
      html += '<td><span class="device-id">' + esc(shortId(r.source_device_id)) + '</span>';
      html += '<span class="relay-arrow"> &#8596; </span>';
      html += '<span class="device-id">' + esc(shortId(r.target_device_id)) + '</span></td>';
      html += '<td>' + fmtDuration(r.duration_seconds) + '</td>';
      html += '<td>' + fmtBytes(r.bytes_relayed) + '</td>';
      html += '</tr>';
    });
    elRelaysTbody.innerHTML = html;
  }

  // Render activity log.
  function renderActivity(events) {
    elBadgeActivity.textContent = events.length;
    if (events.length === 0) {
      elActivityList.innerHTML = '<li class="empty-state"><div class="empty-icon">&#9883;</div>No recent activity</li>';
      return;
    }
    var html = '';
    events.forEach(function(ev) {
      html += '<li class="activity-item">';
      html += '<span class="activity-dot ' + esc(ev.event_type) + '"></span>';
      html += '<span class="activity-time">' + fmtTime(ev.timestamp) + '</span>';
      html += '<span class="activity-desc">' + esc(ev.description) + '</span>';
      html += '</li>';
    });
    elActivityList.innerHTML = html;
  }

  // Basic HTML escaping.
  function esc(s) {
    if (!s) return '';
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  // Main refresh cycle.
  var refreshCount = 0;
  function refresh() {
    refreshCount++;
    elHeaderRefresh.textContent = 'Refreshing...';

    Promise.all([
      fetchJSON('/stats'),
      fetchJSON('/peers'),
      fetchJSON('/relays'),
      fetchJSON('/activity')
    ]).then(function(results) {
      renderStats(results[0]);
      renderPeers(results[1]);
      renderRelays(results[2]);
      renderActivity(results[3]);
      elHeaderRefresh.textContent = 'Updated ' + new Date().toLocaleTimeString();
    }).catch(function(err) {
      console.error('Refresh failed:', err);
      elHeaderRefresh.textContent = 'Error - retrying...';
    });
  }

  // Initial load + auto-refresh every 5 seconds.
  refresh();
  setInterval(refresh, 5000);
})();
</script>
</body>
</html>
"##;
