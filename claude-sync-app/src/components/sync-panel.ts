// ==========================================================================
// Sync panel component - handles diff display and sync actions
// ==========================================================================

import type { Peer, DiffResult, SyncResult } from '../lib/types';
import { getPeerDiff, syncWithPeer } from '../lib/tauri-bridge';
import { renderDiffFiles } from './diff-viewer';

/** The peer we're currently syncing with */
let currentPeer: Peer | null = null;

/** The most recent diff result for the selected peer */
let currentDiff: DiffResult | null = null;

/**
 * Show the sync panel for a given peer and compute the diff.
 */
export async function showSyncPanel(peer: Peer): Promise<void> {
  currentPeer = peer;

  // Show the panel
  const panel = document.getElementById('sync-panel');
  if (panel) panel.style.display = 'block';

  // Set peer name in header
  const nameEl = document.getElementById('peer-name');
  if (nameEl) nameEl.textContent = peer.name;

  // Show loading state
  showDiffLoading(true);
  hideSyncResult();

  // Disable action buttons while loading
  setButtonsEnabled(false);

  try {
    // Compute diff with the selected peer
    const diff = await getPeerDiff(peer.device_id);
    currentDiff = diff;

    // Hide loading
    showDiffLoading(false);

    if (diff.total_changes === 0) {
      // No differences - show clean state
      showDiffClean(true);
      showDiffContent(false);
      setButtonsEnabled(false);
    } else {
      // Show diff stats and file list
      showDiffClean(false);
      showDiffContent(true);

      updateDiffStats(diff);
      renderDiffFiles(diff);
      setButtonsEnabled(true);
    }
  } catch (err) {
    showDiffLoading(false);
    showSyncResultMessage(`Failed to compute diff: ${err}`, false);
  }
}

/**
 * Hide the sync panel.
 */
export function hideSyncPanel(): void {
  currentPeer = null;
  currentDiff = null;

  const panel = document.getElementById('sync-panel');
  if (panel) panel.style.display = 'none';
}

/**
 * Initialize the sync panel event handlers.
 * Called once during app startup.
 */
export function initSyncPanel(): void {
  // Close button
  const closeBtn = document.getElementById('close-sync-btn');
  if (closeBtn) {
    closeBtn.addEventListener('click', () => {
      hideSyncPanel();
    });
  }

  // Push button
  const pushBtn = document.getElementById('push-btn');
  if (pushBtn) {
    pushBtn.addEventListener('click', () => {
      if (currentPeer) {
        executeSyncAction('push');
      }
    });
  }

  // Pull button
  const pullBtn = document.getElementById('pull-btn');
  if (pullBtn) {
    pullBtn.addEventListener('click', () => {
      if (currentPeer) {
        executeSyncAction('pull');
      }
    });
  }
}

/**
 * Execute a push or pull sync operation with the current peer.
 */
async function executeSyncAction(direction: 'push' | 'pull'): Promise<void> {
  if (!currentPeer) return;

  // Disable buttons during sync
  setButtonsEnabled(false);
  hideSyncResult();
  showSyncProgress(true);
  updateProgressText(`${direction === 'push' ? 'Pushing' : 'Pulling'} configs...`);
  updateProgressBar(10);

  try {
    // Simulate initial progress (real progress would come from events)
    updateProgressBar(30);

    const result: SyncResult = await syncWithPeer(currentPeer.device_id, direction);

    updateProgressBar(100);

    if (result.success) {
      const verb = direction === 'push' ? 'Pushed' : 'Pulled';
      showSyncResultMessage(
        `${verb} ${result.files_transferred} file(s) successfully.`,
        true
      );
    } else {
      showSyncResultMessage(
        `Sync failed: ${result.error || 'Unknown error'}`,
        false
      );
    }
  } catch (err) {
    showSyncResultMessage(`Sync error: ${err}`, false);
  } finally {
    showSyncProgress(false);
    // Re-enable buttons so user can retry or perform another action
    if (currentDiff && currentDiff.total_changes > 0) {
      setButtonsEnabled(true);
    }
  }
}

// -- UI Helper Functions ---------------------------------------------------

function showDiffLoading(show: boolean): void {
  const el = document.getElementById('diff-loading');
  if (el) el.style.display = show ? 'block' : 'none';
}

function showDiffContent(show: boolean): void {
  const el = document.getElementById('diff-content');
  if (el) el.style.display = show ? 'block' : 'none';
}

function showDiffClean(show: boolean): void {
  const el = document.getElementById('diff-clean');
  if (el) el.style.display = show ? 'block' : 'none';
}

function updateDiffStats(diff: DiffResult): void {
  const addedEl = document.getElementById('stat-added');
  const modifiedEl = document.getElementById('stat-modified');
  const deletedEl = document.getElementById('stat-deleted');

  if (addedEl) addedEl.textContent = `${diff.added.length} added`;
  if (modifiedEl) modifiedEl.textContent = `${diff.modified.length} modified`;
  if (deletedEl) deletedEl.textContent = `${diff.deleted.length} deleted`;
}

function setButtonsEnabled(enabled: boolean): void {
  const pushBtn = document.getElementById('push-btn') as HTMLButtonElement | null;
  const pullBtn = document.getElementById('pull-btn') as HTMLButtonElement | null;

  if (pushBtn) pushBtn.disabled = !enabled;
  if (pullBtn) pullBtn.disabled = !enabled;
}

function showSyncProgress(show: boolean): void {
  const el = document.getElementById('sync-progress');
  if (el) el.style.display = show ? 'block' : 'none';

  if (!show) {
    updateProgressBar(0);
  }
}

function updateProgressBar(percent: number): void {
  const fill = document.getElementById('progress-fill');
  if (fill) fill.style.width = `${Math.min(100, Math.max(0, percent))}%`;
}

function updateProgressText(text: string): void {
  const el = document.getElementById('progress-text');
  if (el) el.textContent = text;
}

function showSyncResultMessage(message: string, success: boolean): void {
  const el = document.getElementById('sync-result');
  const textEl = document.getElementById('result-text');

  if (el) {
    el.style.display = 'block';
    el.className = `sync-result ${success ? 'success' : 'error'}`;
  }
  if (textEl) {
    textEl.textContent = message;
  }
}

function hideSyncResult(): void {
  const el = document.getElementById('sync-result');
  if (el) el.style.display = 'none';
}
