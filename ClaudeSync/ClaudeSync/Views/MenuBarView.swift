// MenuBarView.swift
// ClaudeSync
//
// The main content view displayed in the menu bar popover.
// Shows online status, the list of discovered peers, and local config count.
// Provides quick access to settings and refresh actions.
// Includes auto-sync indicators, WAN peer sections, and tracker status.

import SwiftUI

struct MenuBarView: View {
    @EnvironmentObject var networkManager: NetworkManager

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Header with app name, online status, and watching indicator.
            headerSection

            Divider()

            // Compact status dashboard showing LAN, Tracker, and Watcher at a glance.
            StatusDashboard()
                .environmentObject(networkManager)

            Divider()

            // Auto-sync toggle bar (compact).
            autoSyncBar

            Divider()

            // LAN peer list or empty state.
            if networkManager.peers.isEmpty && networkManager.wanPeers.isEmpty {
                emptyPeerState
            } else {
                peerListSection

                // WAN peers section (shown only when WAN peers exist).
                if !networkManager.wanPeers.isEmpty {
                    Divider()
                    wanPeerListSection
                }
            }

            Divider()

            // Live activity feed showing sync events.
            ActivitySection(activityLog: networkManager.activityLog)
                .environmentObject(networkManager)

            Divider()

            // Footer with local config info and actions.
            footerSection
        }
        .frame(width: 360)
    }

    // MARK: - Header

    private var headerSection: some View {
        HStack {
            Text("Claude Sync")
                .font(.headline)
                .fontWeight(.semibold)

            Spacer()

            // Watching indicator: green eye when file watcher is active.
            if networkManager.isWatching {
                HStack(spacing: 4) {
                    Image(systemName: "eye.fill")
                        .font(.caption)
                        .foregroundStyle(.green)
                    Text("Watching")
                        .font(.caption2)
                        .foregroundStyle(.green)
                }
                .padding(.trailing, 8)
            }

            HStack(spacing: 4) {
                Circle()
                    .fill(networkManager.isOnline ? Color.green : Color.red)
                    .frame(width: 8, height: 8)
                Text(networkManager.isOnline ? "Online" : "Offline")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            // Tracker connection indicator.
            if !networkManager.syncConfig.trackers.isEmpty {
                HStack(spacing: 3) {
                    Circle()
                        .fill(networkManager.isTrackerConnected ? Color.green : Color.red)
                        .frame(width: 6, height: 6)
                    Image(systemName: "globe")
                        .font(.caption2)
                        .foregroundStyle(networkManager.isTrackerConnected ? .green : .secondary)
                }
                .help(networkManager.isTrackerConnected ? "Tracker connected" : "Tracker disconnected")
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
    }

    // MARK: - Auto-Sync Bar

    /// Compact toggle bar for enabling/disabling auto-sync directly from the menu.
    private var autoSyncBar: some View {
        HStack {
            Image(systemName: "arrow.triangle.2.circlepath")
                .font(.caption)
                .foregroundStyle(networkManager.isAutoSyncEnabled ? .green : .secondary)

            Text("Auto-Sync")
                .font(.caption)
                .foregroundStyle(.secondary)

            Spacer()

            Toggle("", isOn: Binding(
                get: { networkManager.isAutoSyncEnabled },
                set: { _ in
                    Task { await networkManager.toggleAutoSync() }
                }
            ))
            .toggleStyle(.switch)
            .controlSize(.mini)
            .labelsHidden()
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 6)
        .background(networkManager.isAutoSyncEnabled ? Color.green.opacity(0.05) : Color.clear)
    }

    // MARK: - LAN Peer List

    private var peerListSection: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("YOUR MACHINES")
                .font(.caption2)
                .foregroundStyle(.tertiary)
                .fontWeight(.medium)
                .padding(.horizontal, 16)
                .padding(.top, 8)
                .padding(.bottom, 4)

            ScrollView {
                LazyVStack(spacing: 1) {
                    ForEach(networkManager.peers) { peer in
                        PeerRowView(peer: peer)
                            .environmentObject(networkManager)
                    }
                }
            }
            .frame(maxHeight: 400)
        }
    }

    // MARK: - WAN Peer List

    /// Separate section for peers discovered via tracker (WAN connections).
    private var wanPeerListSection: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Image(systemName: "globe")
                    .font(.caption2)
                    .foregroundStyle(.purple)
                Text("WAN PEERS")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                    .fontWeight(.medium)
            }
            .padding(.horizontal, 16)
            .padding(.top, 8)
            .padding(.bottom, 4)

            ScrollView {
                LazyVStack(spacing: 1) {
                    ForEach(networkManager.wanPeers) { peer in
                        PeerRowView(peer: peer)
                            .environmentObject(networkManager)
                    }
                }
            }
            .frame(maxHeight: 200)
        }
    }

    // MARK: - Empty State

    private var emptyPeerState: some View {
        VStack(spacing: 8) {
            Image(systemName: "network.slash")
                .font(.title2)
                .foregroundStyle(.tertiary)

            Text("No peers found")
                .font(.subheadline)
                .foregroundStyle(.secondary)

            Text("Other machines running ClaudeSync on this network will appear here.")
                .font(.caption)
                .foregroundStyle(.tertiary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 32)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 32)
    }

    // MARK: - Footer

    private var footerSection: some View {
        VStack(spacing: 8) {
            HStack {
                if networkManager.isScanning {
                    ProgressView()
                        .controlSize(.small)
                    Text("Scanning...")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else {
                    Image(systemName: "doc.on.doc")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Text("This machine: \(networkManager.localConfigCount) configs")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                Spacer()

                Button {
                    Task {
                        await networkManager.refreshLocalConfig()
                    }
                } label: {
                    Image(systemName: "arrow.clockwise")
                        .font(.caption)
                }
                .buttonStyle(.borderless)
                .help("Refresh local config")
            }

            // Show last error if any.
            if let error = networkManager.lastError {
                HStack(spacing: 4) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .font(.caption2)
                        .foregroundStyle(.red)
                    Text(error)
                        .font(.caption2)
                        .foregroundStyle(.red)
                        .lineLimit(2)

                    Spacer()

                    Button {
                        networkManager.lastError = nil
                    } label: {
                        Image(systemName: "xmark.circle")
                            .font(.caption2)
                    }
                    .buttonStyle(.borderless)
                }
            }

            HStack {
                SettingsLink {
                    Label("Settings", systemImage: "gearshape")
                        .font(.caption)
                }
                .buttonStyle(.borderless)

                Spacer()

                Button("Quit") {
                    NSApplication.shared.terminate(nil)
                }
                .font(.caption)
                .buttonStyle(.borderless)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
    }
}

// MARK: - Peer Row View

/// A single row in the peer list showing peer status and quick actions.
/// Includes auto-sync indicators and connection type badges.
struct PeerRowView: View {
    @EnvironmentObject var networkManager: NetworkManager
    let peer: Peer

    @State private var isExpanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Main row content.
            Button {
                withAnimation(.easeInOut(duration: 0.2)) {
                    isExpanded.toggle()
                }
            } label: {
                HStack(spacing: 10) {
                    // Platform icon with connection type overlay.
                    ZStack(alignment: .bottomTrailing) {
                        Image(systemName: peer.platformIcon)
                            .font(.title3)
                            .foregroundStyle(.primary)
                            .frame(width: 24)

                        // Connection type icon (wifi for LAN, globe for WAN).
                        Image(systemName: peer.connectionTypeIcon)
                            .font(.system(size: 8))
                            .foregroundStyle(peer.connectionType == .lan ? .blue : .purple)
                            .offset(x: 2, y: 2)
                    }

                    // Name, status, and auto-sync info.
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 4) {
                            Text(peer.name)
                                .font(.subheadline)
                                .fontWeight(.medium)
                                .lineLimit(1)

                            // Pulsing green dot for peers with active auto-sync subscription.
                            if peer.isAutoSyncSubscribed {
                                Circle()
                                    .fill(Color.green)
                                    .frame(width: 6, height: 6)
                                    .overlay(
                                        Circle()
                                            .fill(Color.green.opacity(0.4))
                                            .frame(width: 10, height: 10)
                                            .opacity(peer.isAutoSyncSubscribed ? 1 : 0)
                                            .animation(
                                                .easeInOut(duration: 1.0).repeatForever(autoreverses: true),
                                                value: peer.isAutoSyncSubscribed
                                            )
                                    )
                            }

                            // Connection type label badge.
                            Text(peer.connectionTypeLabel)
                                .font(.system(size: 9, weight: .medium))
                                .padding(.horizontal, 4)
                                .padding(.vertical, 1)
                                .background(
                                    peer.connectionType == .lan
                                        ? Color.blue.opacity(0.15)
                                        : peer.connectionType == .wan
                                            ? Color.purple.opacity(0.15)
                                            : Color.yellow.opacity(0.15)
                                )
                                .foregroundStyle(
                                    peer.connectionType == .lan
                                        ? .blue
                                        : peer.connectionType == .wan
                                            ? .purple
                                            : .yellow
                                )
                                .clipShape(RoundedRectangle(cornerRadius: 3))
                        }

                        // Status subtitle with optional auto-sync timestamp.
                        HStack(spacing: 4) {
                            statusSubtitle

                            if let lastSync = peer.lastAutoSyncDescription {
                                Text("\u{00B7}")
                                    .font(.caption)
                                    .foregroundStyle(.tertiary)
                                Text("Last sync: \(lastSync)")
                                    .font(.caption2)
                                    .foregroundStyle(.green.opacity(0.8))
                            }
                        }
                    }

                    Spacer()

                    // Status indicator.
                    if peer.status.isActive {
                        ProgressView()
                            .controlSize(.small)
                    } else {
                        Image(systemName: peer.status.iconName)
                            .font(.caption)
                            .foregroundStyle(peer.status.indicatorColor)
                    }
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 8)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            // Expanded detail with actions.
            if isExpanded {
                PeerActionsView(peer: peer)
                    .environmentObject(networkManager)
                    .padding(.horizontal, 16)
                    .padding(.bottom, 8)
            }
        }
        .background(isExpanded ? Color.primary.opacity(0.04) : Color.clear)
    }

    @ViewBuilder
    private var statusSubtitle: some View {
        switch peer.status {
        case .discovered, .connected:
            if peer.differingFileCount > 0 {
                Text("\(peer.configCount) configs \u{00B7} \(peer.differingFileCount) differ")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                Text("\(peer.configCount) configs")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        case .synced:
            Text("In sync \u{00B7} \(peer.configCount) configs")
                .font(.caption)
                .foregroundStyle(.green)
        case .offline:
            Text("Last seen: \(peer.lastSeenDescription)")
                .font(.caption)
                .foregroundStyle(.tertiary)
        case .error:
            Text("Connection error")
                .font(.caption)
                .foregroundStyle(.red)
        case .connecting, .comparing, .syncing:
            Text(peer.status.displayName)
                .font(.caption)
                .foregroundStyle(.orange)
        }
    }
}

// MARK: - Peer Actions View

/// Action buttons shown when a peer row is expanded.
struct PeerActionsView: View {
    @EnvironmentObject var networkManager: NetworkManager
    let peer: Peer

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            // Connection actions.
            if peer.status == .discovered || peer.status == .error {
                actionButton(
                    title: "Connect",
                    icon: "link",
                    action: {
                        Task { await networkManager.connectToPeer(peer) }
                    }
                )
            }

            if peer.status == .connected || peer.status == .synced {
                HStack(spacing: 8) {
                    actionButton(
                        title: "Compare",
                        icon: "arrow.triangle.2.circlepath",
                        action: {
                            Task { await networkManager.compareWithPeer(peer) }
                        }
                    )

                    if peer.remoteManifest != nil && peer.differingFileCount > 0 {
                        actionButton(
                            title: "Push",
                            icon: "arrow.up.circle",
                            action: {
                                Task { await networkManager.pushToPeer(peer) }
                            }
                        )

                        actionButton(
                            title: "Pull",
                            icon: "arrow.down.circle",
                            action: {
                                Task { await networkManager.pullFromPeer(peer) }
                            }
                        )
                    }
                }
            }

            // File diff summary when manifest is available.
            if let remoteManifest = peer.remoteManifest {
                let diffs = DiffEngine.differences(
                    local: networkManager.localHashes,
                    remote: remoteManifest
                )
                if !diffs.isEmpty {
                    VStack(alignment: .leading, spacing: 2) {
                        let localOnly = diffs.filter { $0.status == .localOnly }.count
                        let remoteOnly = diffs.filter { $0.status == .remoteOnly }.count
                        let modified = diffs.filter { $0.status == .modified }.count

                        if localOnly > 0 {
                            Label("\(localOnly) local only", systemImage: "plus.circle")
                                .font(.caption2)
                                .foregroundStyle(.green)
                        }
                        if remoteOnly > 0 {
                            Label("\(remoteOnly) remote only", systemImage: "arrow.down.circle")
                                .font(.caption2)
                                .foregroundStyle(.blue)
                        }
                        if modified > 0 {
                            Label("\(modified) modified", systemImage: "pencil.circle")
                                .font(.caption2)
                                .foregroundStyle(.orange)
                        }
                    }
                    .padding(.top, 4)
                }
            }
        }
    }

    private func actionButton(title: String, icon: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Label(title, systemImage: icon)
                .font(.caption)
        }
        .buttonStyle(.bordered)
        .controlSize(.small)
        .disabled(peer.status.isActive)
    }
}
