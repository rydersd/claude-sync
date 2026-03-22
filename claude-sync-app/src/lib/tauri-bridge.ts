// ==========================================================================
// IPC bridge to Rust backend via Tauri commands
// Each function maps to a #[tauri::command] handler in src-tauri/src/main.rs
// ==========================================================================

import { invoke } from '@tauri-apps/api/core';
import type {
  Peer,
  DiffResult,
  SyncResult,
  DeviceInfo,
  ConfigTree,
  AutoSyncStatus,
  TrackerPeer,
  SyncConfig,
  PairedDevice,
} from './types';

/**
 * Discover peers advertising the _claude-sync._tcp service on the LAN.
 * Returns the current snapshot of discovered peers (non-blocking).
 */
export async function discoverPeers(): Promise<Peer[]> {
  return invoke<Peer[]>('discover_peers');
}

/**
 * Get information about this device (name, ID, platform, file count, fingerprint).
 */
export async function getDeviceInfo(): Promise<DeviceInfo> {
  return invoke<DeviceInfo>('get_device_info');
}

/**
 * Scan the local ~/.claude/ directory and return the config file tree.
 */
export async function getConfigTree(): Promise<ConfigTree> {
  return invoke<ConfigTree>('get_config_tree');
}

/**
 * Connect to a peer and compute the diff between local and remote configs.
 * @param peerId - The device_id of the peer to compare against
 */
export async function getPeerDiff(peerId: string): Promise<DiffResult> {
  return invoke<DiffResult>('get_peer_diff', { peerId });
}

/**
 * Execute a sync operation with a peer.
 * @param peerId - The device_id of the peer to sync with
 * @param direction - 'push' sends local configs to peer, 'pull' receives from peer
 */
export async function syncWithPeer(peerId: string, direction: 'push' | 'pull'): Promise<SyncResult> {
  return invoke<SyncResult>('sync_with_peer', { peerId, direction });
}

/**
 * Start the mDNS discovery and advertising services.
 * Called once at app startup. The Rust backend will continuously
 * browse for peers and advertise this device.
 */
export async function startDiscovery(): Promise<void> {
  return invoke<void>('start_discovery');
}

/**
 * Stop the mDNS discovery and advertising services.
 * Called on app shutdown / cleanup.
 */
export async function stopDiscovery(): Promise<void> {
  return invoke<void>('stop_discovery');
}

/**
 * Open the ~/.claude/ config directory in the system file manager.
 * Lets users inspect, back up, or verify their files manually.
 */
export async function openConfigFolder(): Promise<void> {
  return invoke<void>('open_config_folder');
}

// -- v2: File Watching & Auto-Sync -----------------------------------------

/**
 * Start file watching on ~/.claude/ for real-time auto-sync.
 * Spawns a background file watcher that debounces changes and
 * broadcasts FileChanged messages to subscribed peers.
 */
export async function startFileWatching(): Promise<void> {
  return invoke<void>('start_file_watching');
}

/**
 * Stop file watching and disable auto-sync.
 */
export async function stopFileWatching(): Promise<void> {
  return invoke<void>('stop_file_watching');
}

/**
 * Check if the file watcher is currently active.
 */
export async function getWatchingStatus(): Promise<boolean> {
  return invoke<boolean>('get_watching_status');
}

/**
 * Get the current auto-sync status including watching state
 * and connected peer information.
 */
export async function getAutoSyncStatus(): Promise<AutoSyncStatus> {
  return invoke<AutoSyncStatus>('get_auto_sync_status');
}

// -- v2: Tracker & WAN Connectivity -----------------------------------------

/**
 * Connect to a tracker server for WAN peer discovery.
 * @param url - WebSocket URL of the tracker (e.g., "wss://tracker.example.com/ws")
 * @returns The tracker URL or public address on success
 */
export async function connectToTracker(url: string): Promise<string> {
  return invoke<string>('connect_to_tracker', { url });
}

/**
 * Disconnect from the currently connected tracker server.
 */
export async function disconnectFromTracker(): Promise<void> {
  return invoke<void>('disconnect_from_tracker');
}

/**
 * Check if the tracker client is currently connected.
 */
export async function getTrackerStatus(): Promise<boolean> {
  return invoke<boolean>('get_tracker_status');
}

/**
 * Get the list of WAN peers discovered via the tracker.
 */
export async function getWanPeers(): Promise<TrackerPeer[]> {
  return invoke<TrackerPeer[]>('get_wan_peers');
}

// -- v2: Configuration -------------------------------------------------------

/**
 * Get the current sync configuration.
 */
export async function getSyncConfig(): Promise<SyncConfig> {
  return invoke<SyncConfig>('get_sync_config');
}

/**
 * Save updated sync configuration.
 * @param config - The complete sync config to persist
 */
export async function saveSyncConfig(config: SyncConfig): Promise<void> {
  return invoke<void>('save_sync_config', { config });
}

// -- v2: Device Pairing ------------------------------------------------------

/**
 * Get the list of currently paired devices.
 */
export async function getPairedDevices(): Promise<PairedDevice[]> {
  return invoke<PairedDevice[]>('get_paired_devices');
}

/**
 * Generate a new 6-digit pairing code for display to the user.
 */
export async function generatePairingCode(): Promise<string> {
  return invoke<string>('generate_pairing_code');
}

/**
 * Complete the pairing process with a remote device.
 * Called after the 6-digit code has been verified on both sides.
 * @param deviceId - The remote device's unique ID
 * @param name - The remote device's display name
 * @param certFingerprint - The remote device's certificate fingerprint
 */
export async function completePairing(
  deviceId: string,
  name: string,
  certFingerprint: string,
): Promise<void> {
  return invoke<void>('complete_pairing', { deviceId, name, certFingerprint });
}

/**
 * Remove a paired device (unpair).
 * @param deviceId - The device_id to unpair
 */
export async function unpairDevice(deviceId: string): Promise<void> {
  return invoke<void>('unpair_device', { deviceId });
}

/**
 * Get this device's TLS certificate fingerprint.
 * Useful for displaying during the pairing process.
 */
export async function getDeviceFingerprint(): Promise<string> {
  return invoke<string>('get_device_fingerprint');
}
