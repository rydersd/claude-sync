// ==========================================================================
// Peer list component - renders discovered peers and handles selection
// ==========================================================================

import type { Peer } from '../lib/types';

/** Currently selected peer ID, or null if none selected */
let selectedPeerId: string | null = null;

/** Callback invoked when user selects a peer */
let onPeerSelectedCallback: ((peer: Peer) => void) | null = null;

/**
 * Register a callback for when a peer is selected from the list.
 */
export function onPeerSelected(callback: (peer: Peer) => void): void {
  onPeerSelectedCallback = callback;
}

/**
 * Get the currently selected peer ID.
 */
export function getSelectedPeerId(): string | null {
  return selectedPeerId;
}

/**
 * Clear the current peer selection.
 */
export function clearSelection(): void {
  selectedPeerId = null;
  const items = document.querySelectorAll('.peer-item');
  items.forEach(item => item.classList.remove('selected'));
}

/**
 * Render the peer list into the #peer-list container.
 * @param peers - Array of discovered peers to display
 */
export function renderPeerList(peers: Peer[]): void {
  const container = document.getElementById('peer-list');
  const noPeersEl = document.getElementById('no-peers');
  if (!container) return;

  // If no peers discovered, show the empty/searching state
  if (peers.length === 0) {
    if (noPeersEl) {
      noPeersEl.style.display = 'block';
    }
    // Remove any existing peer items but keep the empty state element
    const existingItems = container.querySelectorAll('.peer-item');
    existingItems.forEach(item => item.remove());
    return;
  }

  // Hide empty state
  if (noPeersEl) {
    noPeersEl.style.display = 'none';
  }

  // Build the peer items HTML
  const fragment = document.createDocumentFragment();
  for (const peer of peers) {
    const item = createPeerItem(peer);
    fragment.appendChild(item);
  }

  // Remove old peer items, keep empty state element
  const existingItems = container.querySelectorAll('.peer-item');
  existingItems.forEach(item => item.remove());

  // Append new items
  container.appendChild(fragment);
}

/**
 * Create a single peer item DOM element.
 */
function createPeerItem(peer: Peer): HTMLElement {
  const item = document.createElement('div');
  item.className = 'peer-item';
  item.dataset.peerId = peer.device_id;

  // Restore selection state if this peer was previously selected
  if (peer.device_id === selectedPeerId) {
    item.classList.add('selected');
  }

  // Truncate fingerprint for display
  const shortFingerprint = peer.fingerprint.substring(0, 8);

  item.innerHTML = `
    <div class="peer-info">
      <span class="peer-name">${escapeHtml(peer.name)}</span>
      <span class="peer-meta">${escapeHtml(peer.address)} &middot; ${shortFingerprint}</span>
    </div>
    <div class="peer-badge">
      <span class="badge badge-platform">${escapeHtml(peer.platform)}</span>
      <span class="badge badge-files">${peer.file_count} files</span>
    </div>
  `;

  // Handle click to select this peer
  item.addEventListener('click', () => {
    // Deselect all peers
    const allItems = document.querySelectorAll('.peer-item');
    allItems.forEach(el => el.classList.remove('selected'));

    // Select this one
    item.classList.add('selected');
    selectedPeerId = peer.device_id;

    // Notify the callback
    if (onPeerSelectedCallback) {
      onPeerSelectedCallback(peer);
    }
  });

  return item;
}

/**
 * Simple HTML escaping to prevent XSS from peer-provided data.
 */
function escapeHtml(text: string): string {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}
