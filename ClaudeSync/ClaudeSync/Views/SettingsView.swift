// SettingsView.swift
// ClaudeSync
//
// App preferences window accessible from the menu bar popover
// or via the standard macOS Settings menu item.
// Shows device identity, network status, and configuration options.

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

            aboutTab
                .tabItem {
                    Label("About", systemImage: "info.circle")
                }
                .tag(SettingsTab.about)
        }
        .frame(width: 480, height: 360)
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

    // MARK: - About Tab

    private var aboutTab: some View {
        VStack(spacing: 16) {
            Image(systemName: "arrow.triangle.2.circlepath")
                .font(.system(size: 48))
                .foregroundStyle(.tint)

            Text("ClaudeSync")
                .font(.title)
                .fontWeight(.bold)

            Text("Peer-to-peer LAN config sync for Claude Code")
                .font(.subheadline)
                .foregroundStyle(.secondary)

            Divider()
                .padding(.horizontal, 64)

            VStack(spacing: 8) {
                InfoRow(label: "Version", value: "1.0.0")
                InfoRow(label: "Protocol Version", value: "1")
                InfoRow(label: "macOS Requirement", value: "14.0+")
                InfoRow(label: "Discovery", value: "Bonjour (mDNS)")
                InfoRow(label: "Transport", value: "TCP with length-prefixed framing")
            }

            Spacer()

            Text("Built for syncing Claude Code configuration between your machines on the same network.")
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
