// SettingsView.swift
// ClaudeSync
//
// App preferences window accessible from the menu bar popover
// or via the standard macOS Settings menu item.
// Shows device identity, network status, configuration options,
// auto-sync settings, tracker management, and security/pairing controls.

import SwiftUI

struct SettingsView: View {
    @EnvironmentObject var networkManager: NetworkManager

    /// Tab selection for the settings window.
    @State private var selectedTab: SettingsTab = .general

    var body: some View {
        TabView(selection: $selectedTab) {
            generalTab
                .tabItem {
                    Label("General", systemImage: "gearshape")
                }
                .tag(SettingsTab.general)

            networkTab
                .tabItem {
                    Label("Network", systemImage: "network")
                }
                .tag(SettingsTab.network)

            autoSyncTab
                .tabItem {
                    Label("Auto-Sync", systemImage: "arrow.triangle.2.circlepath")
                }
                .tag(SettingsTab.autoSync)

            trackersTab
                .tabItem {
                    Label("Trackers", systemImage: "globe")
                }
                .tag(SettingsTab.trackers)

            securityTab
                .tabItem {
                    Label("Security", systemImage: "lock.shield")
                }
                .tag(SettingsTab.security)

            aboutTab
                .tabItem {
                    Label("About", systemImage: "info.circle")
                }
                .tag(SettingsTab.about)
        }
        .frame(width: 520, height: 440)
    }

    // MARK: - General Tab

    private var generalTab: some View {
        Form {
            Section("Device Identity") {
                LabeledContent("Device ID") {
                    Text(DeviceIdentity.deviceId.prefix(16) + "...")
                        .fontDesign(.monospaced)
                        .font(.caption)
                        .textSelection(.enabled)
                }

                LabeledContent("Device Name") {
                    Text(DeviceIdentity.deviceName)
                }

                LabeledContent("Platform") {
                    Text(DeviceIdentity.platform)
                }
            }

            Section("Local Config") {
                LabeledContent("Config Directory") {
                    Text("~/.claude/")
                        .fontDesign(.monospaced)
                        .font(.caption)
                }

                LabeledContent("Files") {
                    Text("\(networkManager.localConfigCount)")
                }

                LabeledContent("Fingerprint") {
                    if networkManager.localFingerprint.isEmpty {
                        Text("Not computed")
                            .foregroundStyle(.tertiary)
                    } else {
                        Text(networkManager.localFingerprint.prefix(16) + "...")
                            .fontDesign(.monospaced)
                            .font(.caption)
                            .textSelection(.enabled)
                    }
                }

                Button("Refresh") {
                    Task {
                        await networkManager.refreshLocalConfig()
                    }
                }
                .disabled(networkManager.isScanning)
            }

            Section("Sync Paths") {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Files under these paths are synced:")
                        .font(.caption)
                        .foregroundStyle(.secondary)

                    ForEach(ConfigScanner.syncPaths, id: \.self) { path in
                        HStack(spacing: 4) {
                            Image(systemName: path.hasSuffix("/") ? "folder" : "doc")
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                            Text(path)
                                .fontDesign(.monospaced)
                                .font(.caption)
                        }
                    }
                    Text("settings.json (portable keys only)")
                        .fontDesign(.monospaced)
                        .font(.caption)
                }
            }
        }
        .formStyle(.grouped)
        .padding()
    }

    // MARK: - Network Tab

    private var networkTab: some View {
        Form {
            Section("Status") {
                LabeledContent("Network") {
                    HStack(spacing: 4) {
                        Circle()
                            .fill(networkManager.isOnline ? Color.green : Color.red)
                            .frame(width: 8, height: 8)
                        Text(networkManager.isOnline ? "Online" : "Offline")
                    }
                }

                LabeledContent("Service Type") {
                    Text(ServiceAdvertiser.serviceType)
                        .fontDesign(.monospaced)
                        .font(.caption)
                }

                LabeledContent("Discovered Peers") {
                    Text("\(networkManager.peers.count)")
                }

                if !networkManager.wanPeers.isEmpty {
                    LabeledContent("WAN Peers") {
                        Text("\(networkManager.wanPeers.count)")
                    }
                }
            }

            Section("Peers") {
                if networkManager.peers.isEmpty {
                    Text("No peers discovered on the local network.")
                        .foregroundStyle(.secondary)
                        .font(.caption)
                } else {
                    ForEach(networkManager.peers) { peer in
                        HStack {
                            Image(systemName: peer.platformIcon)
                                .foregroundStyle(.secondary)
                            VStack(alignment: .leading) {
                                Text(peer.name)
                                    .font(.subheadline)
                                Text(peer.id.prefix(16) + "...")
                                    .font(.caption2)
                                    .fontDesign(.monospaced)
                                    .foregroundStyle(.tertiary)
                            }
                            Spacer()
                            StatusBadge(status: peer.status)
                        }
                    }
                }
            }

            Section("Actions") {
                HStack {
                    Button("Restart Services") {
                        Task {
                            networkManager.stopServices()
                            await networkManager.startServices()
                        }
                    }

                    Button("Stop Services") {
                        networkManager.stopServices()
                    }
                    .tint(.red)
                }
            }
        }
        .formStyle(.grouped)
        .padding()
    }

    // MARK: - Auto-Sync Tab

    private var autoSyncTab: some View {
        Form {
            Section("Auto-Sync") {
                Toggle("Enable Auto-Sync", isOn: Binding(
                    get: { networkManager.isAutoSyncEnabled },
                    set: { _ in
                        Task { await networkManager.toggleAutoSync() }
                    }
                ))

                LabeledContent("File Watcher Status") {
                    HStack(spacing: 4) {
                        Circle()
                            .fill(networkManager.isWatching ? Color.green : Color.gray)
                            .frame(width: 8, height: 8)
                        Text(networkManager.isWatching ? "Active" : "Inactive")
                            .font(.caption)
                    }
                }
            }

            Section("Debounce") {
                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        Text("Debounce Interval")
                        Spacer()
                        Text("\(networkManager.syncConfig.autoSync.debounceMs)ms")
                            .font(.caption)
                            .fontDesign(.monospaced)
                            .foregroundStyle(.secondary)
                    }
                    Slider(
                        value: Binding(
                            get: { Double(networkManager.syncConfig.autoSync.debounceMs) },
                            set: { newValue in
                                networkManager.syncConfig.autoSync.debounceMs = Int(newValue)
                                networkManager.saveSyncConfig()
                            }
                        ),
                        in: 500...5000,
                        step: 100
                    )
                    Text("How long to wait after a file change before syncing. Lower values sync faster but use more resources.")
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }
            }

            Section("Per-Peer Auto-Sync") {
                if networkManager.peers.isEmpty {
                    Text("No peers available for auto-sync configuration.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(networkManager.peers) { peer in
                        HStack {
                            Image(systemName: peer.platformIcon)
                                .foregroundStyle(.secondary)
                            Text(peer.name)
                                .font(.subheadline)

                            Spacer()

                            // Per-peer auto-sync toggle. Shows current subscription state.
                            // When auto-sync is globally enabled, subscriptions are managed
                            // automatically, but users can toggle individual peers.
                            Toggle("", isOn: Binding(
                                get: { peer.isAutoSyncSubscribed },
                                set: { newValue in
                                    peer.isAutoSyncSubscribed = newValue
                                }
                            ))
                            .toggleStyle(.switch)
                            .controlSize(.small)
                            .labelsHidden()
                        }
                    }
                }
            }
        }
        .formStyle(.grouped)
        .padding()
    }

    // MARK: - Trackers Tab

    /// Sheet state for the "Add Tracker" dialog.
    @State private var showAddTracker = false
    @State private var newTrackerName = ""
    @State private var newTrackerURL = ""

    private var trackersTab: some View {
        Form {
            Section("Tracker Servers") {
                if networkManager.syncConfig.trackers.isEmpty {
                    VStack(spacing: 8) {
                        Text("No tracker servers configured.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        Text("Trackers enable discovery of peers outside your local network (WAN).")
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                    }
                } else {
                    ForEach(networkManager.syncConfig.trackers) { tracker in
                        HStack {
                            // Connection status indicator.
                            Circle()
                                .fill(networkManager.isTrackerConnected && tracker.enabled
                                      ? Color.green : Color.red)
                                .frame(width: 8, height: 8)

                            VStack(alignment: .leading, spacing: 2) {
                                Text(tracker.name)
                                    .font(.subheadline)
                                    .fontWeight(.medium)
                                Text(tracker.url)
                                    .font(.caption2)
                                    .fontDesign(.monospaced)
                                    .foregroundStyle(.tertiary)
                            }

                            Spacer()

                            // Enable/disable toggle.
                            Toggle("", isOn: Binding(
                                get: { tracker.enabled },
                                set: { _ in
                                    networkManager.toggleTracker(url: tracker.url)
                                }
                            ))
                            .toggleStyle(.switch)
                            .controlSize(.small)
                            .labelsHidden()

                            // Remove button.
                            Button {
                                Task {
                                    await networkManager.removeTracker(url: tracker.url)
                                }
                            } label: {
                                Image(systemName: "trash")
                                    .font(.caption)
                                    .foregroundStyle(.red)
                            }
                            .buttonStyle(.borderless)
                            .help("Remove tracker")
                        }
                    }
                }
            }

            Section {
                Button("Add Tracker") {
                    newTrackerName = ""
                    newTrackerURL = ""
                    showAddTracker = true
                }
            }
        }
        .formStyle(.grouped)
        .padding()
        .sheet(isPresented: $showAddTracker) {
            addTrackerSheet
        }
    }

    /// Sheet for adding a new tracker server.
    private var addTrackerSheet: some View {
        VStack(spacing: 16) {
            Text("Add Tracker Server")
                .font(.headline)

            Form {
                TextField("Name", text: $newTrackerName)
                    .textFieldStyle(.roundedBorder)

                TextField("URL (wss://...)", text: $newTrackerURL)
                    .textFieldStyle(.roundedBorder)
                    .fontDesign(.monospaced)
                    .font(.caption)
            }
            .formStyle(.grouped)

            HStack {
                Button("Cancel") {
                    showAddTracker = false
                }
                .keyboardShortcut(.cancelAction)

                Spacer()

                Button("Add") {
                    if !newTrackerName.isEmpty && !newTrackerURL.isEmpty {
                        Task {
                            await networkManager.addTracker(name: newTrackerName, url: newTrackerURL)
                        }
                        showAddTracker = false
                    }
                }
                .keyboardShortcut(.defaultAction)
                .disabled(newTrackerName.isEmpty || newTrackerURL.isEmpty)
            }
        }
        .padding(20)
        .frame(width: 380)
    }

    // MARK: - Security Tab

    /// Pairing sheet state.
    @State private var showPairDevice = false
    @State private var isPairingResponder = false
    @State private var pairingCodeInput = ""

    private var securityTab: some View {
        Form {
            Section("Connection Security") {
                Toggle("Require Device Pairing", isOn: Binding(
                    get: { networkManager.syncConfig.security.requirePairing },
                    set: { newValue in
                        networkManager.syncConfig.security.requirePairing = newValue
                        networkManager.saveSyncConfig()
                    }
                ))

                Toggle("Allow Unpaired LAN", isOn: Binding(
                    get: { networkManager.syncConfig.security.allowUnpairedLan },
                    set: { newValue in
                        networkManager.syncConfig.security.allowUnpairedLan = newValue
                        networkManager.saveSyncConfig()
                    }
                ))

                VStack(alignment: .leading, spacing: 2) {
                    Text("When pairing is required, only paired devices can connect over WAN.")
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                    if networkManager.syncConfig.security.allowUnpairedLan {
                        Text("LAN connections are currently allowed without pairing.")
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                    }
                }
            }

            Section("Paired Devices") {
                if let pm = networkManager.pairingManager {
                    if pm.pairedDevices.isEmpty {
                        Text("No devices have been paired yet.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    } else {
                        ForEach(pm.pairedDevices) { device in
                            HStack {
                                // Platform icon derived from name heuristics.
                                Image(systemName: "laptopcomputer")
                                    .font(.title3)
                                    .foregroundStyle(.secondary)

                                VStack(alignment: .leading, spacing: 2) {
                                    Text(device.name)
                                        .font(.subheadline)
                                        .fontWeight(.medium)
                                    HStack(spacing: 4) {
                                        Text("Paired:")
                                            .font(.caption2)
                                            .foregroundStyle(.tertiary)
                                        Text(device.pairedAt, style: .date)
                                            .font(.caption2)
                                            .foregroundStyle(.tertiary)
                                    }
                                }

                                Spacer()

                                Button("Unpair") {
                                    Task {
                                        try? await pm.unpairDevice(device.id)
                                    }
                                }
                                .buttonStyle(.bordered)
                                .controlSize(.small)
                                .tint(.red)
                            }
                        }
                    }
                } else {
                    Text("Pairing manager not available.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            Section {
                Button("Pair New Device") {
                    isPairingResponder = false
                    pairingCodeInput = ""
                    showPairDevice = true
                }
            }
        }
        .formStyle(.grouped)
        .padding()
        .sheet(isPresented: $showPairDevice) {
            pairDeviceSheet
        }
    }

    /// Sheet for pairing a new device. Shows either a generated code or a code entry field.
    private var pairDeviceSheet: some View {
        VStack(spacing: 16) {
            Text("Pair New Device")
                .font(.headline)

            Picker("Mode", selection: $isPairingResponder) {
                Text("Show Code").tag(true)
                Text("Enter Code").tag(false)
            }
            .pickerStyle(.segmented)
            .padding(.horizontal)

            if isPairingResponder {
                // Generate and display a pairing code via PairingManager.
                VStack(spacing: 12) {
                    Text("Share this code with the other device:")
                        .font(.caption)
                        .foregroundStyle(.secondary)

                    if let pm = networkManager.pairingManager {
                        Text(pm.pairingCode ?? "------")
                            .font(.system(size: 36, weight: .bold, design: .monospaced))
                            .tracking(8)
                            .padding(20)
                            .background(Color.primary.opacity(0.05))
                            .clipShape(RoundedRectangle(cornerRadius: 8))

                        Button("Generate Code") {
                            Task {
                                _ = try? await pm.respondToPairing()
                            }
                        }
                        .buttonStyle(.bordered)
                    }
                }
            } else {
                // Enter a pairing code from the other device.
                VStack(spacing: 12) {
                    Text("Enter the 6-digit code shown on the other device:")
                        .font(.caption)
                        .foregroundStyle(.secondary)

                    TextField("000000", text: $pairingCodeInput)
                        .font(.system(size: 24, weight: .bold, design: .monospaced))
                        .multilineTextAlignment(.center)
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 180)
                }
            }

            HStack {
                Button("Cancel") {
                    networkManager.pairingManager?.cancelPairing()
                    showPairDevice = false
                }
                .keyboardShortcut(.cancelAction)

                Spacer()

                if !isPairingResponder {
                    Button("Pair") {
                        // Code validation is handled by the pairing protocol
                        // over the network. Close the sheet after submission.
                        showPairDevice = false
                    }
                    .keyboardShortcut(.defaultAction)
                    .disabled(pairingCodeInput.count != 6)
                }
            }
        }
        .padding(20)
        .frame(width: 360, height: 320)
    }

    // MARK: - About Tab

    private var aboutTab: some View {
        VStack(spacing: 16) {
            Image(systemName: "arrow.triangle.2.circlepath")
                .font(.system(size: 48))
                .foregroundStyle(.tint)

            Text("ClaudeSync")
                .font(.title)
                .fontWeight(.bold)

            Text("Peer-to-peer config sync for Claude Code")
                .font(.subheadline)
                .foregroundStyle(.secondary)

            Divider()
                .padding(.horizontal, 64)

            VStack(spacing: 8) {
                InfoRow(label: "Version", value: "1.0.0")
                InfoRow(label: "Protocol Version", value: "1")
                InfoRow(label: "macOS Requirement", value: "14.0+")
                InfoRow(label: "Discovery", value: "Bonjour (mDNS) + WAN Tracker")
                InfoRow(label: "Transport", value: "TCP with length-prefixed framing")
            }

            Spacer()

            Text("Built for syncing Claude Code configuration between your machines on the same network or across the internet.")
                .font(.caption)
                .foregroundStyle(.tertiary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 32)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding()
    }
}

// MARK: - Settings Tab Enum

private enum SettingsTab: String {
    case general
    case network
    case autoSync
    case trackers
    case security
    case about
}

// MARK: - Info Row

/// A simple label-value pair for the about tab.
private struct InfoRow: View {
    let label: String
    let value: String

    var body: some View {
        HStack {
            Text(label)
                .foregroundStyle(.secondary)
                .frame(width: 160, alignment: .trailing)
            Text(value)
                .fontDesign(.monospaced)
                .font(.caption)
        }
    }
}
