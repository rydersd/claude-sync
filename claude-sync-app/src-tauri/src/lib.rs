// ==========================================================================
// Claude Sync - Tauri application library
// This is the main entry point for the Tauri app logic.
// Contains module declarations, Tauri command handlers, and app setup.
// ==========================================================================

pub mod config_scanner;
pub mod connection;
pub mod device_identity;
pub mod diff_engine;
pub mod discovery;
pub mod protocol;
pub mod settings_merger;
pub mod sync_engine;

use std::sync::Mutex;
use tauri::{
    Manager,
    menu::{MenuBuilder, MenuItemBuilder},
    tray::TrayIconBuilder,
};

use crate::config_scanner::{compute_fingerprint, scan_config_dir};
use crate::device_identity::{get_hostname, get_or_create_device_id, get_platform};
use crate::discovery::DiscoveryManager;
use crate::protocol::{ConfigTree, DeviceInfo, DiffResult, PeerInfo, SyncResult};

// -- Tauri Managed State ---------------------------------------------------

/// Application state managed by Tauri. Holds the discovery manager
/// which is shared across all command invocations.
pub struct AppState {
    pub discovery: Mutex<Option<DiscoveryManager>>,
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
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(AppState {
            discovery: Mutex::new(None),
        })
        .invoke_handler(tauri::generate_handler![
            start_discovery,
            stop_discovery,
            discover_peers,
            get_device_info,
            get_config_tree,
            get_peer_diff,
            sync_with_peer,
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
