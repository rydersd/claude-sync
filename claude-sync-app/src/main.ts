// ==========================================================================
// Frontend entry point for Claude Sync
// Initializes the app, starts peer discovery, and manages the main UI loop.
// Includes auto-sync panel, settings panel, and WAN peer support.
// ==========================================================================

import { discoverPeers, getDeviceInfo, startDiscovery, openConfigFolder, getWanPeers, getTrackerStatus } from './lib/tauri-bridge';
import type { Peer, DeviceInfo, TrackerPeer } from './lib/types';
import { renderPeerList, onPeerSelected, clearSelection } from './components/peer-list';
import { showSyncPanel, hideSyncPanel, initSyncPanel } from './components/sync-panel';
import { renderAutoSyncPanel } from './components/auto-sync-panel';
import { renderSettingsPanel, toggleSettingsPanel, isSettingsPanelVisible } from './components/settings-panel';

// -- State -----------------------------------------------------------------

/** Polling interval handle for peer discovery refresh */
let discoveryIntervalId: number | null = null;

/** How often to refresh the peer list (milliseconds) */
const DISCOVERY_POLL_MS = 3000;

// -- Initialization --------------------------------------------------------

document.addEventListener('DOMContentLoaded', async () => {
  // Initialize sync panel event handlers (close, push, pull buttons).
  initSyncPanel();

  // Set up peer selection handler.
  onPeerSelected(handlePeerSelected);

  // Set up refresh button.
  const refreshBtn = document.getElementById('refresh-btn');
  if (refreshBtn) {
    refreshBtn.addEventListener('click', refreshPeers);
  }

  // Set up "Open Config Folder" button.
  const openFolderBtn = document.getElementById('open-folder-btn');
  if (openFolderBtn) {
    openFolderBtn.addEventListener('click', async () => {
      try {
        await openConfigFolder();
      } catch (err) {
        console.error('Failed to open config folder:', err);
      }
    });
  }

  // Set up settings gear button.
  const settingsBtn = document.getElementById('settings-btn');
  if (settingsBtn) {
    settingsBtn.addEventListener('click', async () => {
      settingsBtn.classList.toggle('active', !isSettingsPanelVisible());
      await toggleSettingsPanel();
    });
  }

  // Render the auto-sync panel.
  const autoSyncArea = document.getElementById('auto-sync-area');
  if (autoSyncArea) {
    renderAutoSyncPanel(autoSyncArea);
  }

  // Render the settings panel (starts hidden).
  const settingsArea = document.getElementById('settings-area');
  if (settingsArea) {
    renderSettingsPanel(settingsArea);
  }

  // Load local device info.
  await loadDeviceInfo();

  // Start mDNS discovery and advertising.
  try {
    await startDiscovery();
    updateStatus('searching');
  } catch (err) {
    console.error('Failed to start discovery:', err);
    updateStatus('offline');
  }

  // Begin polling for peers (LAN + WAN).
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
  // Do an initial refresh immediately.
  refreshPeers();

  // Then poll at regular intervals.
  discoveryIntervalId = window.setInterval(refreshPeers, DISCOVERY_POLL_MS);
}

/**
 * Fetch the current list of discovered peers (LAN + WAN) and update the UI.
 */
async function refreshPeers(): Promise<void> {
  try {
    // Fetch LAN and WAN peers in parallel.
    const [lanPeers, wanPeers, trackerConnected] = await Promise.all([
      discoverPeers(),
      getWanPeers().catch(() => [] as TrackerPeer[]),
      getTrackerStatus().catch(() => false),
    ]);

    // Render LAN peers.
    renderPeerList(lanPeers);

    // Render WAN peers section if any exist.
    renderWanPeers(wanPeers);

    // Update tracker status indicator in the header.
    updateTrackerIndicator(trackerConnected, wanPeers.length);

    // Update main status indicator.
    if (lanPeers.length > 0 || wanPeers.length > 0) {
      updateStatus('online');
    } else {
      updateStatus('searching');
    }
  } catch (err) {
    console.error('Failed to refresh peers:', err);
    // Don't change status on transient errors.
  }
}

// -- WAN Peers Rendering ---------------------------------------------------

/**
 * Render WAN peers discovered via tracker into a separate section.
 * Inserts after the LAN peer list if WAN peers exist.
 */
function renderWanPeers(wanPeers: TrackerPeer[]): void {
  let wanSection = document.getElementById('wan-peers-section');

  if (wanPeers.length === 0) {
    // Remove the WAN section if no WAN peers.
    if (wanSection) wanSection.remove();
    return;
  }

  // Create the WAN section if it doesn't exist.
  if (!wanSection) {
    wanSection = document.createElement('div');
    wanSection.id = 'wan-peers-section';
    wanSection.className = 'wan-peers-section';

    // Insert it after the main peer list container.
    const peerListContainer = document.getElementById('peer-list');
    if (peerListContainer?.parentNode) {
      peerListContainer.parentNode.insertBefore(
        wanSection,
        peerListContainer.nextSibling,
      );
    }
  }

  // Build WAN peer items.
  const peerItemsHtml = wanPeers.map(peer => `
    <div class="peer-item" data-peer-id="${escapeHtml(peer.device_id)}">
      <div class="peer-info">
        <span class="peer-name">${escapeHtml(peer.name)}</span>
        <span class="peer-meta">${escapeHtml(peer.public_addr)} &middot; ${peer.fingerprint.substring(0, 8)}</span>
      </div>
      <div class="peer-badge">
        <span class="badge badge-platform">${escapeHtml(peer.platform)}</span>
        <span class="connection-type wan">WAN</span>
        <span class="badge badge-files">${peer.file_count} files</span>
      </div>
    </div>
  `).join('');

  wanSection.innerHTML = `
    <div class="wan-section-header">
      <span class="globe-icon">&#x1F310;</span>
      <span class="section-label">WAN Peers</span>
    </div>
    ${peerItemsHtml}
  `;
}

/**
 * Update the tracker connection indicator next to the status dot.
 */
function updateTrackerIndicator(connected: boolean, wanPeerCount: number): void {
  let indicator = document.getElementById('tracker-indicator');

  if (!connected && wanPeerCount === 0) {
    // Hide tracker indicator when not connected and no WAN peers.
    if (indicator) indicator.style.display = 'none';
    return;
  }

  if (!indicator) {
    // Create the tracker indicator element next to the status.
    indicator = document.createElement('span');
    indicator.id = 'tracker-indicator';
    indicator.className = 'status';
    indicator.style.marginLeft = '8px';

    const statusEl = document.getElementById('status');
    if (statusEl?.parentNode) {
      statusEl.parentNode.insertBefore(indicator, statusEl.nextSibling);
    }
  }

  indicator.style.display = 'flex';
  indicator.innerHTML = `
    <span class="status-dot ${connected ? 'online' : 'offline'}"
        style="width:6px;height:6px"></span>
    <span style="font-size:11px;color:${connected ? 'var(--accent-purple)' : 'var(--text-muted)'}">
        &#x1F310; ${wanPeerCount > 0 ? wanPeerCount + ' WAN' : 'Tracker'}
    </span>
  `;
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

/**
 * Simple HTML escaping to prevent XSS from peer-provided data.
 */
function escapeHtml(text: string): string {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}
