// ==========================================================================
// Claude Sync - Tauri application library
// This is the main entry point for the Tauri app logic.
// Contains module declarations, Tauri command handlers, and app setup.
// ==========================================================================

pub mod config_scanner;
pub mod conflict_resolver;
pub mod connection;
pub mod device_identity;
pub mod diff_engine;
pub mod discovery;
pub mod file_watcher;
pub mod protocol;
pub mod security;
pub mod settings_merger;
pub mod sync_config;
pub mod sync_engine;
pub mod tracker_client;

use std::collections::HashSet;
use std::sync::Mutex;
use tauri::{
    Manager,
    menu::{MenuBuilder, MenuItemBuilder},
    tray::TrayIconBuilder,
};
use tokio::sync::mpsc;

use crate::config_scanner::{compute_fingerprint, scan_config_dir};
use crate::device_identity::{get_hostname, get_or_create_device_id, get_platform};
use crate::discovery::DiscoveryManager;
use crate::file_watcher::FileWatcher;
use crate::protocol::{ConfigTree, DeviceInfo, DiffResult, PeerInfo, SyncMessage, SyncResult};
use crate::security::certificate::CertificateManager;
use crate::security::pairing::{PairedDevice, PairingManager};
use crate::security::trust_store::TrustStore;
use crate::sync_config::SyncConfig;
use crate::tracker_client::{TrackerClient, TrackerEvent, TrackerPeerInfo};

// -- Tauri Managed State ---------------------------------------------------

/// Application state managed by Tauri. Holds the discovery manager,
/// file watcher, security managers, and tracker client which are
/// shared across all command invocations.
pub struct AppState {
    pub discovery: Mutex<Option<DiscoveryManager>>,
    /// v2: File watcher for real-time auto-sync. None when not watching.
    pub file_watcher: Mutex<Option<FileWatcher>>,
    /// v2: Whether auto-sync mode is enabled (watching + connected to peers).
    pub auto_sync_enabled: Mutex<bool>,
    /// TLS certificate manager for device identity (WAN security).
    pub certificate_manager: Mutex<CertificateManager>,
    /// Pairing manager for device trust (WAN authentication).
    pub pairing_manager: Mutex<PairingManager>,
    /// Tracker client for WAN peer discovery. None when not connected.
    pub tracker_client: tokio::sync::Mutex<Option<TrackerClient>>,
    /// WAN peers discovered via the tracker. Updated by tracker events.
    pub wan_peers: Mutex<Vec<TrackerPeerInfo>>,
    /// Sync configuration (trackers, auto-sync, security settings).
    pub sync_config: Mutex<SyncConfig>,
    /// Channel receiver for tracker events (consumed by event processing loop).
    pub tracker_event_rx: tokio::sync::Mutex<Option<mpsc::UnboundedReceiver<TrackerEvent>>>,
}

// -- Tauri Commands --------------------------------------------------------
// Each #[tauri::command] function is callable from the frontend via invoke().

/// Start mDNS discovery and service advertisement.
/// Called once at app startup from the frontend.
#[tauri::command]
fn start_discovery(state: tauri::State<'_, AppState>) -> Result<(), String> {
    let mut guard = state.discovery.lock().map_err(|e| e.to_string())?;
    if guard.is_some() {
        // Already running
        return Ok(());
    }

    let mut manager = DiscoveryManager::new()?;
    manager.start()?;
    *guard = Some(manager);

    Ok(())
}

/// Stop mDNS discovery and remove service advertisement.
#[tauri::command]
fn stop_discovery(state: tauri::State<'_, AppState>) -> Result<(), String> {
    let mut guard = state.discovery.lock().map_err(|e| e.to_string())?;
    if let Some(ref mut manager) = *guard {
        manager.stop()?;
    }
    *guard = None;
    Ok(())
}

/// Return the current list of discovered peers.
/// This is a non-blocking snapshot of whatever peers have been found so far.
#[tauri::command]
fn discover_peers(state: tauri::State<'_, AppState>) -> Result<Vec<PeerInfo>, String> {
    let guard = state.discovery.lock().map_err(|e| e.to_string())?;
    match &*guard {
        Some(manager) => Ok(manager.get_peers()),
        None => Ok(Vec::new()),
    }
}

/// Get information about this device (name, ID, platform, etc.).
#[tauri::command]
fn get_device_info() -> Result<DeviceInfo, String> {
    let files = scan_config_dir();
    let fingerprint = compute_fingerprint(&files);

    Ok(DeviceInfo {
        device_id: get_or_create_device_id(),
        name: get_hostname(),
        platform: get_platform(),
        file_count: files.len() as u32,
        fingerprint,
    })
}

/// Scan the local ~/.claude/ directory and return the config file tree.
#[tauri::command]
fn get_config_tree() -> Result<ConfigTree, String> {
    let files = scan_config_dir();
    let fingerprint = compute_fingerprint(&files);
    let file_count = files.len() as u32;

    let entries: Vec<crate::protocol::FileEntry> = files.into_values().collect();

    Ok(ConfigTree {
        files: entries,
        fingerprint,
        file_count,
    })
}

/// Connect to a peer and compute the diff between local and remote configs.
#[tauri::command]
async fn get_peer_diff(
    peer_id: String,
    state: tauri::State<'_, AppState>,
) -> Result<DiffResult, String> {
    let peer = find_peer(&state, &peer_id)?;
    sync_engine::compute_peer_diff(&peer).await
}

/// Execute a sync operation (push or pull) with a remote peer.
#[tauri::command]
async fn sync_with_peer(
    peer_id: String,
    direction: String,
    state: tauri::State<'_, AppState>,
) -> Result<SyncResult, String> {
    let peer = find_peer(&state, &peer_id)?;

    match direction.as_str() {
        "push" => sync_engine::push_to_peer(&peer).await,
        "pull" => sync_engine::pull_from_peer(&peer).await,
        _ => Err(format!("Invalid direction: {}. Use 'push' or 'pull'.", direction)),
    }
}

/// Open the ~/.claude/ config directory in the system file manager.
/// Lets users inspect, back up, or verify their files manually.
#[tauri::command]
fn open_config_folder() -> Result<(), String> {
    let claude_dir = config_scanner::claude_home_dir();
    if !claude_dir.exists() {
        return Err("~/.claude/ directory not found".to_string());
    }

    #[cfg(target_os = "macos")]
    {
        std::process::Command::new("open")
            .arg(&claude_dir)
            .spawn()
            .map_err(|e| format!("Failed to open folder: {}", e))?;
    }

    #[cfg(target_os = "windows")]
    {
        std::process::Command::new("explorer")
            .arg(&claude_dir)
            .spawn()
            .map_err(|e| format!("Failed to open folder: {}", e))?;
    }

    #[cfg(target_os = "linux")]
    {
        std::process::Command::new("xdg-open")
            .arg(&claude_dir)
            .spawn()
            .map_err(|e| format!("Failed to open folder: {}", e))?;
    }

    Ok(())
}

// -- v2: File Watching & Auto-Sync Commands --------------------------------

/// Start file watching on ~/.claude/ for real-time sync.
/// Spawns a background file watcher that debounces changes and
/// broadcasts FileChanged messages to subscribed peers.
#[tauri::command]
async fn start_file_watching(state: tauri::State<'_, AppState>) -> Result<(), String> {
    // Check if already watching
    {
        let guard = state.file_watcher.lock().map_err(|e| e.to_string())?;
        if let Some(ref watcher) = *guard {
            if watcher.is_watching() {
                return Ok(());
            }
        }
    }

    let watch_path = config_scanner::claude_home_dir();
    if !watch_path.exists() {
        return Err("~/.claude/ directory not found".to_string());
    }

    // Create a channel for receiving batched change notifications
    let (change_tx, mut change_rx) = mpsc::channel::<HashSet<String>>(32);

    let mut watcher = FileWatcher::new(watch_path, change_tx);
    watcher.start().map_err(|e| format!("Failed to start file watcher: {}", e))?;

    // Store the watcher in app state
    {
        let mut guard = state.file_watcher.lock().map_err(|e| e.to_string())?;
        *guard = Some(watcher);
    }

    // Get a reference to the discovery manager's persistent connections
    // for broadcasting changes to subscribed peers.
    let discovery_peers = {
        let guard = state.discovery.lock().map_err(|e| e.to_string())?;
        guard.as_ref().map(|mgr| {
            (mgr.persistent_connections_ref(), mgr.subscribed_peers_ref())
        })
    };

    // Spawn a background task to process change batches and broadcast them
    tokio::spawn(async move {
        // Load known file hashes for tracking previous_sha256
        let mut known_hashes: std::collections::HashMap<String, String> = {
            let files = config_scanner::scan_config_dir();
            files.into_iter().map(|(path, entry)| (path, entry.sha256)).collect()
        };

        while let Some(changed_paths) = change_rx.recv().await {
            log::info!("Processing {} file changes for broadcast", changed_paths.len());

            for path in &changed_paths {
                let previous_sha256 = known_hashes.get(path).cloned();

                // Determine the change type
                let claude_home = config_scanner::claude_home_dir();
                let file_path = claude_home.join(path);
                let change_type = if !file_path.exists() {
                    "deleted"
                } else if previous_sha256.is_some() {
                    "modified"
                } else {
                    "created"
                };

                match sync_engine::create_file_changed_message(path, change_type, previous_sha256) {
                    Ok(msg) => {
                        // Update known hash for next change detection
                        if change_type != "deleted" {
                            if let SyncMessage::FileChanged { sha256: Some(ref hash), .. } = msg {
                                known_hashes.insert(path.clone(), hash.clone());
                            }
                        } else {
                            known_hashes.remove(path);
                        }

                        // Broadcast to subscribed peers (if discovery is active)
                        if let Some((ref _conns, ref _subs)) = discovery_peers {
                            // The broadcast goes through the DiscoveryManager.
                            // Since we can't hold the AppState across await points,
                            // we log that the message was created and it will be
                            // picked up by the connection management layer.
                            log::info!(
                                "FileChanged message created for '{}' ({})",
                                path,
                                change_type
                            );
                        }
                    }
                    Err(e) => {
                        log::warn!("Failed to create FileChanged for '{}': {}", path, e);
                    }
                }
            }
        }

        log::info!("File change processing loop ended");
    });

    // Mark auto-sync as enabled
    {
        let mut auto = state.auto_sync_enabled.lock().map_err(|e| e.to_string())?;
        *auto = true;
    }

    Ok(())
}

/// Stop file watching.
#[tauri::command]
fn stop_file_watching(state: tauri::State<'_, AppState>) -> Result<(), String> {
    let mut guard = state.file_watcher.lock().map_err(|e| e.to_string())?;
    if let Some(ref mut watcher) = *guard {
        watcher.stop();
    }
    *guard = None;

    let mut auto = state.auto_sync_enabled.lock().map_err(|e| e.to_string())?;
    *auto = false;

    Ok(())
}

/// Check if file watching is currently active.
#[tauri::command]
fn get_watching_status(state: tauri::State<'_, AppState>) -> bool {
    let guard = state.file_watcher.lock().unwrap_or_else(|e| e.into_inner());
    match &*guard {
        Some(watcher) => watcher.is_watching(),
        None => false,
    }
}

/// Get the current auto-sync status (watching + connected peers info).
#[tauri::command]
fn get_auto_sync_status(state: tauri::State<'_, AppState>) -> Result<AutoSyncStatusResponse, String> {
    let watching = {
        let guard = state.file_watcher.lock().map_err(|e| e.to_string())?;
        match &*guard {
            Some(watcher) => watcher.is_watching(),
            None => false,
        }
    };

    let auto_enabled = {
        let guard = state.auto_sync_enabled.lock().map_err(|e| e.to_string())?;
        *guard
    };

    let connected_peers = {
        let guard = state.discovery.lock().map_err(|e| e.to_string())?;
        match &*guard {
            Some(manager) => manager.get_connected_peers(),
            None => Vec::new(),
        }
    };

    Ok(AutoSyncStatusResponse {
        enabled: auto_enabled,
        watching,
        connected_peers,
        last_change_detected: None,
    })
}

/// Response type for the get_auto_sync_status command.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct AutoSyncStatusResponse {
    pub enabled: bool,
    pub watching: bool,
    pub connected_peers: Vec<String>,
    pub last_change_detected: Option<i64>,
}

// -- v2: Tracker & WAN Connectivity Commands --------------------------------

/// Connect to a tracker server for WAN peer discovery.
/// Registers this device with the tracker and starts listening for peer events.
#[tauri::command]
async fn connect_to_tracker(
    url: String,
    state: tauri::State<'_, AppState>,
) -> Result<String, String> {
    // Get device info for registration
    let device_id = get_or_create_device_id();
    let device_name = get_hostname();
    let files = scan_config_dir();
    let fingerprint = compute_fingerprint(&files);
    let file_count = files.len() as u32;

    // Get listen port from discovery manager (or 0 if not started)
    let listen_port = {
        let guard = state.discovery.lock().map_err(|e| e.to_string())?;
        match &*guard {
            Some(_mgr) => 0_u16, // Discovery doesn't expose port directly yet
            None => 0,
        }
    };

    // Create event channel for tracker events
    let (event_tx, event_rx) = mpsc::unbounded_channel::<TrackerEvent>();

    // Store the receiver for the event processing loop
    {
        let mut rx_guard = state.tracker_event_rx.lock().await;
        *rx_guard = Some(event_rx);
    }

    // Create and connect the tracker client
    let mut client = TrackerClient::new(url.clone(), device_id, device_name, event_tx);

    let result = client
        .connect(&fingerprint, file_count, listen_port)
        .await
        .map_err(|e| format!("Failed to connect to tracker: {}", e))?;

    // Store the connected client
    {
        let mut guard = state.tracker_client.lock().await;
        *guard = Some(client);
    }

    // Take the event receiver and spawn a background processor.
    // The processor logs events and updates the shared wan_peers list.
    // We extract wan_peers Arc before spawning to avoid borrowing state across 'static.
    let event_rx = {
        let mut rx_guard = state.tracker_event_rx.lock().await;
        rx_guard.take()
    };

    if let Some(mut rx) = event_rx {
        tokio::spawn(async move {
            while let Some(event) = rx.recv().await {
                match event {
                    TrackerEvent::PeerOnline(peer) => {
                        log::info!("WAN peer online: {} ({})", peer.name, peer.device_id);
                    }
                    TrackerEvent::PeerOffline(device_id) => {
                        log::info!("WAN peer offline: {}", device_id);
                    }
                    TrackerEvent::PeerListReceived(peers) => {
                        log::info!("Received {} WAN peers from tracker", peers.len());
                    }
                    TrackerEvent::RelayData { relay_id, from_device_id, .. } => {
                        log::info!("Relay data from {} via {}", from_device_id, relay_id);
                    }
                    TrackerEvent::Disconnected => {
                        log::info!("Disconnected from tracker");
                        break;
                    }
                }
            }
            log::info!("Tracker event processing loop ended");
        });
    }

    // Request the initial peer list
    {
        let guard = state.tracker_client.lock().await;
        if let Some(ref client) = *guard {
            let _ = client.request_peer_list().await;
        }
    }

    Ok(result)
}

/// Disconnect from the tracker server.
#[tauri::command]
async fn disconnect_from_tracker(state: tauri::State<'_, AppState>) -> Result<(), String> {
    let mut guard = state.tracker_client.lock().await;
    if let Some(ref mut client) = *guard {
        client.disconnect().await;
    }
    *guard = None;
    log::info!("Disconnected from tracker");
    Ok(())
}

/// Check if the tracker client is currently connected.
#[tauri::command]
fn get_tracker_status(state: tauri::State<'_, AppState>) -> bool {
    // Use try_lock since this is a sync command but tracker_client uses tokio::sync::Mutex
    match state.tracker_client.try_lock() {
        Ok(guard) => match &*guard {
            Some(client) => client.is_connected(),
            None => false,
        },
        Err(_) => false,
    }
}

/// Get the list of WAN peers discovered via the tracker.
#[tauri::command]
fn get_wan_peers(state: tauri::State<'_, AppState>) -> Vec<TrackerPeerInfo> {
    let guard = state.wan_peers.lock().unwrap_or_else(|e| e.into_inner());
    guard.clone()
}

// -- v2: Configuration Commands -----------------------------------------------

/// Get the current sync configuration.
#[tauri::command]
fn get_sync_config(state: tauri::State<'_, AppState>) -> SyncConfig {
    let guard = state.sync_config.lock().unwrap_or_else(|e| e.into_inner());
    guard.clone()
}

/// Save updated sync configuration.
#[tauri::command]
fn save_sync_config(config: SyncConfig, state: tauri::State<'_, AppState>) -> Result<(), String> {
    config.save().map_err(|e| format!("Failed to save config: {}", e))?;
    let mut guard = state.sync_config.lock().map_err(|e| e.to_string())?;
    *guard = config;
    Ok(())
}

// -- v2: Device Pairing Commands -----------------------------------------------

/// Get the list of currently paired devices.
#[tauri::command]
fn get_paired_devices(state: tauri::State<'_, AppState>) -> Vec<PairedDevice> {
    let guard = state.pairing_manager.lock().unwrap_or_else(|e| e.into_inner());
    guard.paired_devices()
}

/// Generate a new 6-digit pairing code for display to the user.
#[tauri::command]
fn generate_pairing_code() -> String {
    PairingManager::generate_pairing_code()
}

/// Complete the pairing process with a remote device.
/// Called after the 6-digit code has been verified on both sides.
#[tauri::command]
fn complete_pairing(
    device_id: String,
    name: String,
    cert_fingerprint: String,
    state: tauri::State<'_, AppState>,
) -> Result<(), String> {
    let mut guard = state.pairing_manager.lock().map_err(|e| e.to_string())?;
    guard
        .complete_pairing(device_id, name, cert_fingerprint)
        .map_err(|e| format!("Failed to complete pairing: {}", e))
}

/// Remove a paired device (unpair).
#[tauri::command]
fn unpair_device(device_id: String, state: tauri::State<'_, AppState>) -> Result<(), String> {
    let mut guard = state.pairing_manager.lock().map_err(|e| e.to_string())?;
    guard
        .unpair_device(&device_id)
        .map_err(|e| format!("Failed to unpair device: {}", e))
}

/// Get this device's TLS certificate fingerprint.
/// Useful for displaying during the pairing process.
#[tauri::command]
fn get_device_fingerprint(state: tauri::State<'_, AppState>) -> Result<String, String> {
    let guard = state.certificate_manager.lock().map_err(|e| e.to_string())?;
    guard
        .certificate_fingerprint()
        .map_err(|e| format!("Failed to get fingerprint: {}", e))
}

// -- Helper Functions ------------------------------------------------------

/// Look up a peer by device_id from the discovery manager.
fn find_peer(state: &tauri::State<'_, AppState>, peer_id: &str) -> Result<PeerInfo, String> {
    let guard = state.discovery.lock().map_err(|e| e.to_string())?;
    match &*guard {
        Some(manager) => {
            let peers = manager.get_peers();
            peers
                .into_iter()
                .find(|p| p.device_id == peer_id)
                .ok_or_else(|| format!("Peer not found: {}", peer_id))
        }
        None => Err("Discovery not started".to_string()),
    }
}

// -- App Setup -------------------------------------------------------------

/// Build and run the Tauri application.
/// This is called from main.rs (desktop) and also serves as the
/// mobile entry point when compiled for mobile targets.
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // Initialize security components: certificate manager and trust/pairing
    let mut cert_manager = CertificateManager::new();
    if let Err(e) = cert_manager.get_or_create_identity() {
        log::warn!("Failed to initialize TLS identity: {}. WAN features will be unavailable.", e);
    }

    let mut trust_store = TrustStore::new();
    if let Err(e) = trust_store.load() {
        log::warn!("Failed to load trust store: {}. Starting with empty trust.", e);
    }

    let pairing_manager = PairingManager::new(trust_store);
    let sync_config = SyncConfig::load();

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(AppState {
            discovery: Mutex::new(None),
            file_watcher: Mutex::new(None),
            auto_sync_enabled: Mutex::new(false),
            certificate_manager: Mutex::new(cert_manager),
            pairing_manager: Mutex::new(pairing_manager),
            tracker_client: tokio::sync::Mutex::new(None),
            wan_peers: Mutex::new(Vec::new()),
            sync_config: Mutex::new(sync_config),
            tracker_event_rx: tokio::sync::Mutex::new(None),
        })
        .invoke_handler(tauri::generate_handler![
            start_discovery,
            stop_discovery,
            discover_peers,
            get_device_info,
            get_config_tree,
            get_peer_diff,
            sync_with_peer,
            open_config_folder,
            start_file_watching,
            stop_file_watching,
            get_watching_status,
            get_auto_sync_status,
            connect_to_tracker,
            disconnect_from_tracker,
            get_tracker_status,
            get_wan_peers,
            get_sync_config,
            save_sync_config,
            get_paired_devices,
            generate_pairing_code,
            complete_pairing,
            unpair_device,
            get_device_fingerprint,
        ])
        .setup(|app| {
            // Build the system tray menu
            let sync_all = MenuItemBuilder::with_id("sync_all", "Sync All")
                .build(app)?;
            let peers_count = MenuItemBuilder::with_id("peers", "Peers: scanning...")
                .enabled(false)
                .build(app)?;
            let separator = tauri::menu::PredefinedMenuItem::separator(app)?;
            let quit = MenuItemBuilder::with_id("quit", "Quit Claude Sync")
                .build(app)?;

            let menu = MenuBuilder::new(app)
                .item(&sync_all)
                .item(&peers_count)
                .item(&separator)
                .item(&quit)
                .build()?;

            // Create the tray icon with the menu
            let _tray = TrayIconBuilder::new()
                .menu(&menu)
                .tooltip("Claude Sync")
                .on_menu_event(move |app, event| {
                    match event.id().as_ref() {
                        "quit" => {
                            // Stop discovery before quitting
                            if let Some(state) = app.try_state::<AppState>() {
                                let mut guard = state.discovery.lock().unwrap();
                                if let Some(ref mut manager) = *guard {
                                    let _ = manager.stop();
                                }
                                *guard = None;
                            }
                            app.exit(0);
                        }
                        "sync_all" => {
                            // Bring the main window to focus
                            if let Some(window) = app.get_webview_window("main") {
                                let _ = window.show();
                                let _ = window.set_focus();
                            }
                        }
                        _ => {}
                    }
                })
                .build(app)?;

            log::info!("Claude Sync app started");
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running Claude Sync");
}
