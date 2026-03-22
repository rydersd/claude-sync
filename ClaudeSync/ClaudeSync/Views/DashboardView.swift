// DashboardView.swift
// ClaudeSync
//
// Main window dashboard showing sync status, connected peers, and activity.
// This is the primary UI surface — a real macOS window instead of just
// a menu bar popover.

import SwiftUI

struct DashboardView: View {
    @EnvironmentObject var networkManager: NetworkManager

    var body: some View {
        VStack(spacing: 0) {
            // Top status bar.
            statusBar

            Divider()

            // Main content: sidebar + detail.
            HSplitView {
                // Left: peers + controls.
                peersPanel
                    .frame(minWidth: 240, idealWidth: 280, maxWidth: 340)

                // Right: activity feed.
                activityPanel
                    .frame(minWidth: 300)
            }
        }
        .frame(minWidth: 640, minHeight: 420)
    }

    // MARK: - Status Bar

    private var statusBar: some View {
        HStack(spacing: 16) {
            // Online indicator.
            HStack(spacing: 6) {
                Circle()
                    .fill(networkManager.isOnline ? Color.green : Color.red)
                    .frame(width: 8, height: 8)
                Text(networkManager.isOnline ? "Online" : "Offline")
                    .font(.subheadline)
                    .foregroundStyle(networkManager.isOnline ? .primary : .secondary)
            }

            Divider().frame(height: 16)

            // LAN peers count.
            Label("\(networkManager.peers.count) LAN", systemImage: "wifi")
                .font(.subheadline)
                .foregroundStyle(networkManager.peers.isEmpty ? .secondary : .primary)

            // WAN peers count (if tracker configured).
            if !networkManager.syncConfig.trackers.isEmpty {
                Divider().frame(height: 16)

                HStack(spacing: 6) {
                    Circle()
                        .fill(networkManager.isTrackerConnected ? Color.purple : Color.red)
                        .frame(width: 8, height: 8)
                    if networkManager.isTrackerConnected {
                        Label("\(networkManager.wanPeers.count) WAN", systemImage: "globe")
                            .font(.subheadline)
                    } else {
                        Label("Tracker offline", systemImage: "globe")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                    }
                }
            }

            // File watcher status.
            if networkManager.isAutoSyncEnabled {
                Divider().frame(height: 16)

                HStack(spacing: 6) {
                    Image(systemName: "eye.fill")
                        .foregroundStyle(networkManager.isWatching ? .green : .yellow)
                    Text(networkManager.isWatching ? "Watching" : "Starting...")
                        .font(.subheadline)
                        .foregroundStyle(networkManager.isWatching ? .green : .secondary)
                }
            }

            Spacer()

            // Config count.
            Label("\(networkManager.localConfigCount) configs", systemImage: "doc.on.doc")
                .font(.subheadline)
                .foregroundStyle(.secondary)

            // Auto-sync toggle.
            Toggle(isOn: Binding(
                get: { networkManager.isAutoSyncEnabled },
                set: { _ in Task { await networkManager.toggleAutoSync() } }
            )) {
                Label("Auto-Sync", systemImage: "arrow.triangle.2.circlepath")
                    .font(.subheadline)
            }
            .toggleStyle(.switch)
            .controlSize(.small)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(.bar)
    }

    // MARK: - Peers Panel (left sidebar)

    private var peersPanel: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Section header.
            HStack {
                Text("PEERS")
                    .font(.caption)
                    .fontWeight(.semibold)
                    .foregroundStyle(.secondary)

                Spacer()

                if networkManager.isScanning {
                    ProgressView()
                        .controlSize(.small)
                } else {
                    Button {
                        Task { await networkManager.refreshLocalConfig() }
                    } label: {
                        Image(systemName: "arrow.clockwise")
                            .font(.caption)
                    }
                    .buttonStyle(.borderless)
                    .help("Refresh")
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)

            Divider()

            if networkManager.peers.isEmpty && networkManager.wanPeers.isEmpty {
                // Empty state.
                VStack(spacing: 8) {
                    Image(systemName: "network.slash")
                        .font(.title)
                        .foregroundStyle(.tertiary)
                    Text("No peers found")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                    Text("Other machines running ClaudeSync will appear here.")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                List {
                    // LAN peers.
                    if !networkManager.peers.isEmpty {
                        Section("LAN") {
                            ForEach(networkManager.peers) { peer in
                                DashboardPeerRow(peer: peer)
                                    .environmentObject(networkManager)
                            }
                        }
                    }

                    // WAN peers.
                    if !networkManager.wanPeers.isEmpty {
                        Section("WAN") {
                            ForEach(networkManager.wanPeers) { peer in
                                DashboardPeerRow(peer: peer)
                                    .environmentObject(networkManager)
                            }
                        }
                    }
                }
                .listStyle(.sidebar)
            }

            Divider()

            // Error bar.
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
                .padding(.horizontal, 12)
                .padding(.vertical, 6)
                .background(Color.red.opacity(0.08))
            }
        }
    }

    // MARK: - Activity Panel (right side)

    private var activityPanel: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Section header.
            HStack {
                Text("ACTIVITY")
                    .font(.caption)
                    .fontWeight(.semibold)
                    .foregroundStyle(.secondary)

                if networkManager.activityLog.hasUnread {
                    Circle()
                        .fill(Color.blue)
                        .frame(width: 6, height: 6)
                }

                Spacer()

                Text("\(networkManager.activityLog.events.count) events")
                    .font(.caption)
                    .foregroundStyle(.tertiary)

                Button {
                    networkManager.activityLog.clear()
                } label: {
                    Text("Clear")
                        .font(.caption)
                }
                .buttonStyle(.borderless)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)

            Divider()

            if networkManager.activityLog.events.isEmpty {
                VStack(spacing: 8) {
                    Image(systemName: "list.bullet.rectangle")
                        .font(.title)
                        .foregroundStyle(.tertiary)
                    Text("No activity yet")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                    Text("Sync events, tracker updates, and file changes will appear here.")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                List(networkManager.activityLog.events.prefix(50)) { event in
                    DashboardActivityRow(event: event)
                }
                .listStyle(.plain)
            }
        }
    }
}

// MARK: - Dashboard Peer Row

/// A peer row for the dashboard sidebar.
struct DashboardPeerRow: View {
    @EnvironmentObject var networkManager: NetworkManager
    let peer: Peer

    var body: some View {
        HStack(spacing: 10) {
            // Platform icon with connection overlay.
            ZStack(alignment: .bottomTrailing) {
                Image(systemName: peer.platformIcon)
                    .font(.title3)
                    .foregroundStyle(.primary)
                    .frame(width: 24)

                Image(systemName: peer.connectionTypeIcon)
                    .font(.system(size: 7))
                    .foregroundStyle(peer.connectionType == .lan ? .blue : .purple)
                    .offset(x: 2, y: 2)
            }

            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 4) {
                    Text(peer.name)
                        .font(.subheadline)
                        .fontWeight(.medium)
                        .lineLimit(1)

                    if peer.isAutoSyncSubscribed {
                        Circle()
                            .fill(Color.green)
                            .frame(width: 6, height: 6)
                    }
                }

                // Status line.
                peerStatusText
            }

            Spacer()

            // Action buttons.
            if peer.status == .discovered || peer.status == .error {
                Button("Connect") {
                    Task { await networkManager.connectToPeer(peer) }
                }
                .controlSize(.small)
            } else if peer.status == .connected || peer.status == .synced {
                HStack(spacing: 4) {
                    if peer.remoteManifest != nil && peer.differingFileCount > 0 {
                        Button {
                            Task { await networkManager.pushToPeer(peer) }
                        } label: {
                            Image(systemName: "arrow.up.circle")
                        }
                        .help("Push to peer")
                        .buttonStyle(.borderless)

                        Button {
                            Task { await networkManager.pullFromPeer(peer) }
                        } label: {
                            Image(systemName: "arrow.down.circle")
                        }
                        .help("Pull from peer")
                        .buttonStyle(.borderless)
                    }

                    Button {
                        Task { await networkManager.compareWithPeer(peer) }
                    } label: {
                        Image(systemName: "arrow.triangle.2.circlepath")
                    }
                    .help("Compare")
                    .buttonStyle(.borderless)
                }
            }

            // Status indicator.
            if peer.status.isActive {
                ProgressView()
                    .controlSize(.small)
            } else {
                Circle()
                    .fill(peer.status.indicatorColor)
                    .frame(width: 8, height: 8)
            }
        }
        .padding(.vertical, 2)
    }

    @ViewBuilder
    private var peerStatusText: some View {
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

// MARK: - Dashboard Activity Row

/// A single event row in the activity panel.
struct DashboardActivityRow: View {
    let event: ActivityEvent

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: event.iconName)
                .font(.system(size: 11))
                .foregroundStyle(event.iconColor)
                .frame(width: 16, height: 16)

            VStack(alignment: .leading, spacing: 1) {
                Text(event.message)
                    .font(.subheadline)
                    .lineLimit(2)

                if let detail = event.detail {
                    Text(detail)
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                        .lineLimit(1)
                }
            }

            Spacer()

            Text(event.relativeTime)
                .font(.caption)
                .foregroundStyle(.quaternary)
                .monospacedDigit()
        }
        .padding(.vertical, 2)
    }
}
