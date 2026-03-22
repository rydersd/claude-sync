// ==========================================================================
// Auto-sync panel component - shows watching status and auto-sync controls
// ==========================================================================

import { startFileWatching, stopFileWatching, getAutoSyncStatus } from '../lib/tauri-bridge';
import type { AutoSyncStatus } from '../lib/types';

/** Interval handle for status polling */
let pollIntervalId: number | null = null;

/** Current auto-sync status from the backend */
let currentStatus: AutoSyncStatus | null = null;

/**
 * Render the auto-sync panel into the given container element.
 * Sets up toggle button, watching indicator, and starts polling for status.
 */
export function renderAutoSyncPanel(container: HTMLElement): void {
    container.innerHTML = `
        <div class="auto-sync-panel">
            <div class="panel-header">
                <h2>Auto-Sync</h2>
                <div class="watching-indicator" id="watching-indicator">
                    <span class="status-dot"></span>
                    <span id="watching-label">Inactive</span>
                </div>
            </div>
            <div class="auto-sync-controls">
                <button id="toggle-auto-sync" class="btn btn-toggle-sync">
                    Enable Auto-Sync
                </button>
                <div class="auto-sync-details" id="auto-sync-details">
                    <div class="detail-row">
                        <span class="label">Connected peers:</span>
                        <span class="value" id="auto-sync-peer-count">0</span>
                    </div>
                    <div class="detail-row">
                        <span class="label">Last change:</span>
                        <span class="value" id="auto-sync-last-change">--</span>
                    </div>
                </div>
            </div>
            <div class="auto-sync-peer-list" id="auto-sync-peers">
                <!-- Populated dynamically when peers are connected -->
            </div>
        </div>
    `;

    // Wire up the toggle button.
    const toggleBtn = document.getElementById('toggle-auto-sync');
    if (toggleBtn) {
        toggleBtn.addEventListener('click', toggleAutoSync);
    }

    // Start polling for auto-sync status.
    refreshStatus();
    pollIntervalId = window.setInterval(refreshStatus, 3000);
}

/**
 * Clean up the auto-sync panel (stop polling).
 */
export function destroyAutoSyncPanel(): void {
    if (pollIntervalId !== null) {
        window.clearInterval(pollIntervalId);
        pollIntervalId = null;
    }
}

/**
 * Toggle auto-sync on/off via the backend.
 */
async function toggleAutoSync(): Promise<void> {
    const toggleBtn = document.getElementById('toggle-auto-sync') as HTMLButtonElement | null;
    if (toggleBtn) {
        toggleBtn.disabled = true;
        toggleBtn.textContent = 'Updating...';
    }

    try {
        if (currentStatus?.enabled) {
            await stopFileWatching();
        } else {
            await startFileWatching();
        }
        // Refresh immediately after toggle.
        await refreshStatus();
    } catch (err) {
        console.error('Failed to toggle auto-sync:', err);
    } finally {
        if (toggleBtn) {
            toggleBtn.disabled = false;
        }
    }
}

/**
 * Fetch the current auto-sync status from the backend and update the UI.
 */
async function refreshStatus(): Promise<void> {
    try {
        currentStatus = await getAutoSyncStatus();
        updateUI(currentStatus);
    } catch (err) {
        // Auto-sync commands may not be implemented yet; show disabled state.
        console.debug('Auto-sync status unavailable:', err);
        updateUI(null);
    }
}

/**
 * Update the DOM to reflect the current auto-sync status.
 */
function updateUI(status: AutoSyncStatus | null): void {
    const indicator = document.getElementById('watching-indicator');
    const label = document.getElementById('watching-label');
    const toggleBtn = document.getElementById('toggle-auto-sync');
    const peerCount = document.getElementById('auto-sync-peer-count');
    const lastChange = document.getElementById('auto-sync-last-change');
    const details = document.getElementById('auto-sync-details');

    if (!status) {
        // Feature not available - show disabled state.
        if (indicator) indicator.className = 'watching-indicator';
        if (label) label.textContent = 'Unavailable';
        if (toggleBtn) {
            toggleBtn.textContent = 'Enable Auto-Sync';
            (toggleBtn as HTMLButtonElement).disabled = true;
        }
        if (details) details.style.display = 'none';
        return;
    }

    // Update watching indicator.
    if (indicator) {
        indicator.className = status.watching
            ? 'watching-indicator active'
            : 'watching-indicator';
    }
    if (label) {
        label.textContent = status.watching ? 'Watching' : 'Inactive';
    }

    // Update toggle button.
    if (toggleBtn) {
        toggleBtn.textContent = status.enabled ? 'Disable Auto-Sync' : 'Enable Auto-Sync';
        toggleBtn.className = status.enabled
            ? 'btn btn-toggle-sync active'
            : 'btn btn-toggle-sync';
        (toggleBtn as HTMLButtonElement).disabled = false;
    }

    // Update details.
    if (details) {
        details.style.display = status.enabled ? 'block' : 'none';
    }
    if (peerCount) {
        peerCount.textContent = `${status.connected_peers.length}`;
    }
    if (lastChange) {
        if (status.last_change_detected) {
            const elapsed = Date.now() - status.last_change_detected;
            lastChange.textContent = formatElapsed(elapsed);
        } else {
            lastChange.textContent = '--';
        }
    }
}

/**
 * Format a millisecond duration into a human-readable string.
 */
function formatElapsed(ms: number): string {
    const seconds = Math.floor(ms / 1000);
    if (seconds < 60) return 'Just now';
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    return `${days}d ago`;
}
