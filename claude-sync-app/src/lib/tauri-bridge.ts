// ==========================================================================
// IPC bridge to Rust backend via Tauri commands
// Each function maps to a #[tauri::command] handler in src-tauri/src/main.rs
// ==========================================================================

import { invoke } from '@tauri-apps/api/core';
import type { Peer, DiffResult, SyncResult, DeviceInfo, ConfigTree } from './types';

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
