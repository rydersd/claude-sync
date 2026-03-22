// ==========================================================================
// Settings panel component - tabbed settings for auto-sync, trackers, security
// ==========================================================================

import {
    getSyncConfig,
    saveSyncConfig,
    getPairedDevices,
    unpairDevice,
    generatePairingCode,
    connectToTracker,
    disconnectFromTracker,
    getTrackerStatus,
} from '../lib/tauri-bridge';
import type { SyncConfig, PairedDevice } from '../lib/types';

/** Current active tab */
let activeTab: 'auto-sync' | 'trackers' | 'security' = 'auto-sync';

/** Cached config from backend */
let cachedConfig: SyncConfig | null = null;

/** Cached paired devices list */
let cachedDevices: PairedDevice[] = [];

/** Whether the settings panel is currently visible */
let isVisible = false;

/**
 * Render the settings panel into the given container element.
 * Panel starts hidden; call showSettingsPanel() to display it.
 */
export function renderSettingsPanel(container: HTMLElement): void {
    container.innerHTML = `
        <div class="settings-panel" id="settings-panel-inner" style="display:none">
            <div class="panel-header">
                <h2>Settings</h2>
                <button id="close-settings-btn" class="btn-icon" title="Close">&times;</button>
            </div>
            <div class="settings-tabs" id="settings-tabs">
                <button class="tab active" data-tab="auto-sync">Auto-Sync</button>
                <button class="tab" data-tab="trackers">Trackers</button>
                <button class="tab" data-tab="security">Security</button>
            </div>
            <div class="tab-content" id="settings-content">
                <!-- Content loaded per tab -->
            </div>
        </div>
    `;

    // Wire up close button.
    const closeBtn = document.getElementById('close-settings-btn');
    if (closeBtn) {
        closeBtn.addEventListener('click', hideSettingsPanel);
    }

    // Wire up tab buttons.
    const tabs = document.querySelectorAll('#settings-tabs .tab');
    tabs.forEach(tab => {
        tab.addEventListener('click', (e) => {
            const target = e.currentTarget as HTMLElement;
            const tabName = target.dataset.tab as typeof activeTab;
            if (tabName) {
                switchTab(tabName);
            }
        });
    });
}

/**
 * Show the settings panel and load initial data.
 */
export async function showSettingsPanel(): Promise<void> {
    const panel = document.getElementById('settings-panel-inner');
    if (panel) panel.style.display = 'block';
    isVisible = true;

    // Load config and devices in parallel.
    await Promise.all([loadConfig(), loadPairedDevices()]);

    // Render the active tab content.
    renderActiveTab();
}

/**
 * Hide the settings panel.
 */
export function hideSettingsPanel(): void {
    const panel = document.getElementById('settings-panel-inner');
    if (panel) panel.style.display = 'none';
    isVisible = false;
}

/**
 * Toggle settings panel visibility.
 */
export async function toggleSettingsPanel(): Promise<void> {
    if (isVisible) {
        hideSettingsPanel();
    } else {
        await showSettingsPanel();
    }
}

/**
 * Check if settings panel is currently visible.
 */
export function isSettingsPanelVisible(): boolean {
    return isVisible;
}

// -- Data Loading -----------------------------------------------------------

async function loadConfig(): Promise<void> {
    try {
        cachedConfig = await getSyncConfig();
    } catch (err) {
        console.error('Failed to load sync config:', err);
        // Provide a default config for display.
        cachedConfig = {
            trackers: [],
            auto_sync: { enabled: false, debounce_ms: 500 },
            security: { require_pairing: true, allow_unpaired_lan: true },
        };
    }
}

async function loadPairedDevices(): Promise<void> {
    try {
        cachedDevices = await getPairedDevices();
    } catch (err) {
        console.error('Failed to load paired devices:', err);
        cachedDevices = [];
    }
}

// -- Tab Management ---------------------------------------------------------

function switchTab(tab: typeof activeTab): void {
    activeTab = tab;

    // Update tab button styles.
    const tabs = document.querySelectorAll('#settings-tabs .tab');
    tabs.forEach(t => {
        const el = t as HTMLElement;
        el.classList.toggle('active', el.dataset.tab === tab);
    });

    renderActiveTab();
}

function renderActiveTab(): void {
    const container = document.getElementById('settings-content');
    if (!container || !cachedConfig) return;

    switch (activeTab) {
        case 'auto-sync':
            renderAutoSyncSettings(container, cachedConfig);
            break;
        case 'trackers':
            renderTrackerSettings(container, cachedConfig);
            break;
        case 'security':
            renderSecuritySettings(container, cachedConfig, cachedDevices);
            break;
    }
}

// -- Auto-Sync Settings -----------------------------------------------------

function renderAutoSyncSettings(container: HTMLElement, config: SyncConfig): void {
    container.innerHTML = `
        <div class="settings-section">
            <div class="setting-row">
                <div class="setting-info">
                    <span class="setting-label">Auto-Sync on Startup</span>
                    <span class="setting-description">Automatically start file watching when the app launches.</span>
                </div>
                <label class="toggle-switch">
                    <input type="checkbox" id="auto-sync-enabled"
                        ${config.auto_sync.enabled ? 'checked' : ''}>
                    <span class="toggle-slider"></span>
                </label>
            </div>

            <div class="setting-row">
                <div class="setting-info">
                    <span class="setting-label">Debounce Interval</span>
                    <span class="setting-description">Wait time after a file change before syncing (ms).</span>
                </div>
                <div class="setting-control">
                    <input type="range" id="debounce-slider"
                        min="500" max="5000" step="100"
                        value="${config.auto_sync.debounce_ms}">
                    <span class="setting-value" id="debounce-value">${config.auto_sync.debounce_ms}ms</span>
                </div>
            </div>
        </div>
    `;

    // Wire up auto-sync toggle.
    const enabledCheck = document.getElementById('auto-sync-enabled') as HTMLInputElement | null;
    if (enabledCheck) {
        enabledCheck.addEventListener('change', async () => {
            if (cachedConfig) {
                cachedConfig.auto_sync.enabled = enabledCheck.checked;
                await saveConfig();
            }
        });
    }

    // Wire up debounce slider.
    const slider = document.getElementById('debounce-slider') as HTMLInputElement | null;
    const valueLabel = document.getElementById('debounce-value');
    if (slider) {
        slider.addEventListener('input', () => {
            if (valueLabel) valueLabel.textContent = `${slider.value}ms`;
        });
        slider.addEventListener('change', async () => {
            if (cachedConfig) {
                cachedConfig.auto_sync.debounce_ms = parseInt(slider.value, 10);
                await saveConfig();
            }
        });
    }
}

// -- Tracker Settings -------------------------------------------------------

function renderTrackerSettings(container: HTMLElement, config: SyncConfig): void {
    const trackerListHtml = config.trackers.length === 0
        ? `<div class="empty-state">
               <p>No tracker servers configured.</p>
               <p class="hint">Trackers enable discovery of peers outside your local network.</p>
           </div>`
        : config.trackers.map((tracker, index) => `
            <div class="tracker-item" data-index="${index}">
                <div class="tracker-info">
                    <span class="tracker-status-dot ${tracker.enabled ? 'active' : ''}"></span>
                    <div class="tracker-details">
                        <span class="tracker-name">${escapeHtml(tracker.name)}</span>
                        <span class="tracker-url">${escapeHtml(tracker.url)}</span>
                    </div>
                </div>
                <div class="tracker-actions">
                    <label class="toggle-switch toggle-small">
                        <input type="checkbox" class="tracker-toggle"
                            data-index="${index}" ${tracker.enabled ? 'checked' : ''}>
                        <span class="toggle-slider"></span>
                    </label>
                    <button class="btn-icon btn-remove-tracker" data-index="${index}"
                        title="Remove tracker">&times;</button>
                </div>
            </div>
        `).join('');

    container.innerHTML = `
        <div class="settings-section">
            <div class="tracker-list">
                ${trackerListHtml}
            </div>
            <div class="add-tracker-form" id="add-tracker-form" style="display:none">
                <input type="text" id="new-tracker-name" placeholder="Tracker name"
                    class="input-field">
                <input type="text" id="new-tracker-url" placeholder="wss://tracker.example.com/ws"
                    class="input-field mono">
                <div class="form-actions">
                    <button id="cancel-add-tracker" class="btn btn-secondary">Cancel</button>
                    <button id="confirm-add-tracker" class="btn btn-primary">Add</button>
                </div>
            </div>
            <button id="show-add-tracker" class="btn btn-secondary" style="margin-top:8px">
                + Add Tracker
            </button>
        </div>
    `;

    // Wire up "Add Tracker" button.
    const showAddBtn = document.getElementById('show-add-tracker');
    const addForm = document.getElementById('add-tracker-form');
    if (showAddBtn && addForm) {
        showAddBtn.addEventListener('click', () => {
            addForm.style.display = 'block';
            showAddBtn.style.display = 'none';
        });
    }

    // Wire up cancel.
    const cancelBtn = document.getElementById('cancel-add-tracker');
    if (cancelBtn && addForm && showAddBtn) {
        cancelBtn.addEventListener('click', () => {
            addForm.style.display = 'none';
            showAddBtn.style.display = 'block';
        });
    }

    // Wire up confirm add.
    const confirmBtn = document.getElementById('confirm-add-tracker');
    if (confirmBtn) {
        confirmBtn.addEventListener('click', async () => {
            const nameInput = document.getElementById('new-tracker-name') as HTMLInputElement;
            const urlInput = document.getElementById('new-tracker-url') as HTMLInputElement;
            if (nameInput?.value && urlInput?.value && cachedConfig) {
                cachedConfig.trackers.push({
                    name: nameInput.value,
                    url: urlInput.value,
                    enabled: true,
                });
                await saveConfig();
                // Try to connect to the new tracker.
                try {
                    await connectToTracker(urlInput.value);
                } catch (err) {
                    console.error('Failed to connect to new tracker:', err);
                }
                renderActiveTab();
            }
        });
    }

    // Wire up tracker toggles.
    const toggles = container.querySelectorAll('.tracker-toggle');
    toggles.forEach(toggle => {
        toggle.addEventListener('change', async (e) => {
            const input = e.target as HTMLInputElement;
            const index = parseInt(input.dataset.index ?? '0', 10);
            if (cachedConfig && cachedConfig.trackers[index]) {
                cachedConfig.trackers[index].enabled = input.checked;
                await saveConfig();
            }
        });
    });

    // Wire up remove buttons.
    const removeBtns = container.querySelectorAll('.btn-remove-tracker');
    removeBtns.forEach(btn => {
        btn.addEventListener('click', async (e) => {
            const target = e.currentTarget as HTMLElement;
            const index = parseInt(target.dataset.index ?? '0', 10);
            if (cachedConfig) {
                cachedConfig.trackers.splice(index, 1);
                await saveConfig();
                renderActiveTab();
            }
        });
    });
}

// -- Security Settings ------------------------------------------------------

function renderSecuritySettings(
    container: HTMLElement,
    config: SyncConfig,
    devices: PairedDevice[],
): void {
    const deviceListHtml = devices.length === 0
        ? '<p class="empty-hint">No devices have been paired yet.</p>'
        : devices.map(device => `
            <div class="paired-device">
                <div class="device-icon">&#x1F4BB;</div>
                <div class="device-info-col">
                    <span class="device-name">${escapeHtml(device.name)}</span>
                    <span class="device-meta">Paired: ${formatDate(device.paired_at)}</span>
                </div>
                <button class="btn btn-unpair" data-device-id="${escapeHtml(device.device_id)}">
                    Unpair
                </button>
            </div>
        `).join('');

    container.innerHTML = `
        <div class="settings-section">
            <div class="setting-row">
                <div class="setting-info">
                    <span class="setting-label">Require Device Pairing</span>
                    <span class="setting-description">Only paired devices can connect over WAN.</span>
                </div>
                <label class="toggle-switch">
                    <input type="checkbox" id="require-pairing"
                        ${config.security.require_pairing ? 'checked' : ''}>
                    <span class="toggle-slider"></span>
                </label>
            </div>

            <div class="setting-row">
                <div class="setting-info">
                    <span class="setting-label">Allow Unpaired LAN</span>
                    <span class="setting-description">Allow local network connections without pairing.</span>
                </div>
                <label class="toggle-switch">
                    <input type="checkbox" id="allow-unpaired-lan"
                        ${config.security.allow_unpaired_lan ? 'checked' : ''}>
                    <span class="toggle-slider"></span>
                </label>
            </div>

            <h3 class="section-title">Paired Devices</h3>
            <div class="paired-device-list">
                ${deviceListHtml}
            </div>

            <div class="pairing-actions">
                <button id="pair-new-device" class="btn btn-secondary">Pair New Device</button>
            </div>

            <div class="pairing-dialog" id="pairing-dialog" style="display:none">
                <div class="pairing-tabs">
                    <button class="pairing-tab active" data-mode="show">Show Code</button>
                    <button class="pairing-tab" data-mode="enter">Enter Code</button>
                </div>
                <div class="pairing-content" id="pairing-content">
                    <!-- Populated per mode -->
                </div>
                <button id="cancel-pairing" class="btn btn-secondary">Cancel</button>
            </div>
        </div>
    `;

    // Wire up security toggles.
    const requirePairingCheck = document.getElementById('require-pairing') as HTMLInputElement | null;
    if (requirePairingCheck) {
        requirePairingCheck.addEventListener('change', async () => {
            if (cachedConfig) {
                cachedConfig.security.require_pairing = requirePairingCheck.checked;
                await saveConfig();
            }
        });
    }

    const allowUnpairedCheck = document.getElementById('allow-unpaired-lan') as HTMLInputElement | null;
    if (allowUnpairedCheck) {
        allowUnpairedCheck.addEventListener('change', async () => {
            if (cachedConfig) {
                cachedConfig.security.allow_unpaired_lan = allowUnpairedCheck.checked;
                await saveConfig();
            }
        });
    }

    // Wire up unpair buttons.
    const unpairBtns = container.querySelectorAll('.btn-unpair');
    unpairBtns.forEach(btn => {
        btn.addEventListener('click', async (e) => {
            const target = e.currentTarget as HTMLElement;
            const deviceId = target.dataset.deviceId;
            if (deviceId) {
                try {
                    await unpairDevice(deviceId);
                    await loadPairedDevices();
                    renderActiveTab();
                } catch (err) {
                    console.error('Failed to unpair device:', err);
                }
            }
        });
    });

    // Wire up "Pair New Device" button.
    const pairBtn = document.getElementById('pair-new-device');
    const pairingDialog = document.getElementById('pairing-dialog');
    if (pairBtn && pairingDialog) {
        pairBtn.addEventListener('click', () => {
            pairingDialog.style.display = 'block';
            pairBtn.style.display = 'none';
            showPairingMode('show');
        });
    }

    // Wire up pairing mode tabs.
    const pairingTabs = container.querySelectorAll('.pairing-tab');
    pairingTabs.forEach(tab => {
        tab.addEventListener('click', (e) => {
            const target = e.currentTarget as HTMLElement;
            const mode = target.dataset.mode as 'show' | 'enter';
            if (mode) showPairingMode(mode);
        });
    });

    // Wire up cancel pairing.
    const cancelPairing = document.getElementById('cancel-pairing');
    if (cancelPairing && pairingDialog && pairBtn) {
        cancelPairing.addEventListener('click', () => {
            pairingDialog.style.display = 'none';
            pairBtn.style.display = 'block';
        });
    }
}

/**
 * Show the pairing UI in "show code" or "enter code" mode.
 */
function showPairingMode(mode: 'show' | 'enter'): void {
    // Update active tab.
    const tabs = document.querySelectorAll('.pairing-tab');
    tabs.forEach(t => {
        const el = t as HTMLElement;
        el.classList.toggle('active', el.dataset.mode === mode);
    });

    const content = document.getElementById('pairing-content');
    if (!content) return;

    if (mode === 'show') {
        content.innerHTML = `
            <p class="pairing-instruction">Share this code with the other device:</p>
            <div class="pairing-code" id="pairing-code-display">------</div>
            <button id="generate-code-btn" class="btn btn-primary">Generate Code</button>
        `;

        const genBtn = document.getElementById('generate-code-btn');
        if (genBtn) {
            genBtn.addEventListener('click', async () => {
                try {
                    const code = await generatePairingCode();
                    const display = document.getElementById('pairing-code-display');
                    if (display) display.textContent = code;
                } catch (err) {
                    console.error('Failed to generate pairing code:', err);
                }
            });
        }
    } else {
        content.innerHTML = `
            <p class="pairing-instruction">Enter the 6-digit code from the other device:</p>
            <input type="text" id="pairing-code-input" class="pairing-code-input"
                maxlength="6" placeholder="000000" pattern="[0-9]*" inputmode="numeric">
            <button id="submit-code-btn" class="btn btn-primary" disabled>Pair</button>
        `;

        const input = document.getElementById('pairing-code-input') as HTMLInputElement | null;
        const submitBtn = document.getElementById('submit-code-btn') as HTMLButtonElement | null;
        if (input && submitBtn) {
            input.addEventListener('input', () => {
                // Only allow digits and enable button when 6 digits entered.
                input.value = input.value.replace(/\D/g, '').substring(0, 6);
                submitBtn.disabled = input.value.length !== 6;
            });

            submitBtn.addEventListener('click', () => {
                // Pairing submission is handled by the protocol layer.
                // Close the dialog after the code is submitted.
                const dialog = document.getElementById('pairing-dialog');
                const pairBtn = document.getElementById('pair-new-device');
                if (dialog) dialog.style.display = 'none';
                if (pairBtn) pairBtn.style.display = 'block';
            });
        }
    }
}

// -- Helpers ----------------------------------------------------------------

async function saveConfig(): Promise<void> {
    if (cachedConfig) {
        try {
            await saveSyncConfig(cachedConfig);
        } catch (err) {
            console.error('Failed to save sync config:', err);
        }
    }
}

function formatDate(epochSeconds: number): string {
    const date = new Date(epochSeconds * 1000);
    return date.toLocaleDateString(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
    });
}

function escapeHtml(text: string): string {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
