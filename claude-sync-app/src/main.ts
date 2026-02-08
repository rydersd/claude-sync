// ==========================================================================
// Frontend entry point for Claude Sync
// Initializes the app, starts peer discovery, and manages the main UI loop
// ==========================================================================

import { discoverPeers, getDeviceInfo, startDiscovery } from './lib/tauri-bridge';
import type { Peer, DeviceInfo } from './lib/types';
import { renderPeerList, onPeerSelected, clearSelection } from './components/peer-list';
import { showSyncPanel, hideSyncPanel, initSyncPanel } from './components/sync-panel';

// -- State -----------------------------------------------------------------

/** Polling interval handle for peer discovery refresh */
let discoveryIntervalId: number | null = null;

/** How often to refresh the peer list (milliseconds) */
const DISCOVERY_POLL_MS = 3000;

// -- Initialization --------------------------------------------------------

document.addEventListener('DOMContentLoaded', async () => {
  // Initialize sync panel event handlers (close, push, pull buttons)
  initSyncPanel();

  // Set up peer selection handler
  onPeerSelected(handlePeerSelected);

  // Set up refresh button
  const refreshBtn = document.getElementById('refresh-btn');
  if (refreshBtn) {
    refreshBtn.addEventListener('click', refreshPeers);
  }

  // Load local device info
  await loadDeviceInfo();

  // Start mDNS discovery and advertising
  try {
    await startDiscovery();
    updateStatus('searching');
  } catch (err) {
    console.error('Failed to start discovery:', err);
    updateStatus('offline');
  }

  // Begin polling for peers
  startPeerPolling();
});

// -- Device Info -----------------------------------------------------------

/**
 * Load and display information about this device.
 */
async function loadDeviceInfo(): Promise<void> {
  try {
    const info: DeviceInfo = await getDeviceInfo();

    setElementText('device-name', info.name);
    setElementText('device-platform', info.platform);
    setElementText('device-file-count', `${info.file_count}`);
    setElementText('device-fingerprint', info.fingerprint.substring(0, 16) + '...');
  } catch (err) {
    console.error('Failed to load device info:', err);
    setElementText('device-name', 'Error loading');
  }
}

// -- Peer Discovery --------------------------------------------------------

/**
 * Start the periodic polling loop for peer discovery.
 */
function startPeerPolling(): void {
  // Do an initial refresh immediately
  refreshPeers();

  // Then poll at regular intervals
  discoveryIntervalId = window.setInterval(refreshPeers, DISCOVERY_POLL_MS);
}

/**
 * Fetch the current list of discovered peers and update the UI.
 */
async function refreshPeers(): Promise<void> {
  try {
    const peers: Peer[] = await discoverPeers();

    // Update the peer list UI
    renderPeerList(peers);

    // Update status indicator
    if (peers.length > 0) {
      updateStatus('online');
    } else {
      updateStatus('searching');
    }
  } catch (err) {
    console.error('Failed to refresh peers:', err);
    // Don't change status on transient errors
  }
}

// -- Event Handlers --------------------------------------------------------

/**
 * Called when the user selects a peer from the list.
 * Opens the sync panel and computes the diff with that peer.
 */
function handlePeerSelected(peer: Peer): void {
  showSyncPanel(peer);
}

// -- UI Helpers ------------------------------------------------------------

/**
 * Update the status indicator in the header.
 * @param state - 'online', 'searching', or 'offline'
 */
function updateStatus(state: 'online' | 'searching' | 'offline'): void {
  const dot = document.querySelector('.status-dot') as HTMLElement | null;
  const text = document.getElementById('status-text');

  if (dot) {
    dot.className = `status-dot ${state}`;
  }

  if (text) {
    switch (state) {
      case 'online':
        text.textContent = 'Online';
        break;
      case 'searching':
        text.textContent = 'Searching...';
        break;
      case 'offline':
        text.textContent = 'Offline';
        break;
    }
  }
}

/**
 * Set the text content of an element by its ID.
 */
function setElementText(id: string, text: string): void {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}
