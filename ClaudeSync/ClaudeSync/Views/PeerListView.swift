// PeerListView.swift
// ClaudeSync
//
// Standalone peer list view used for larger presentations (e.g. settings window).
// Shows all discovered peers with their status, config counts, and actions.
// Groups peers by online/offline status and distinguishes LAN vs WAN connections.

import SwiftUI

struct PeerListView: View {
    @EnvironmentObject var networkManager: NetworkManager

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            // Online peers section.
            let onlinePeers = networkManager.peers.filter { $0.status != .offline }
            let offlinePeers = networkManager.peers.filter { $0.status == .offline }

            if !onlinePeers.isEmpty {
                Section {
                    ForEach(onlinePeers) { peer in
                        PeerCardView(peer: peer)
                    }
                } header: {
                    Text("Online")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .fontWeight(.medium)
                }
            }

            // WAN peers section (separate from LAN).
            if !networkManager.wanPeers.isEmpty {
                Section {
                    ForEach(networkManager.wanPeers) { peer in
                        PeerCardView(peer: peer)
                    }
                } header: {
                    HStack(spacing: 4) {
                        Image(systemName: "globe")
                            .font(.caption2)
                            .foregroundStyle(.purple)
                        Text("WAN Peers")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .fontWeight(.medium)
                    }
                }
            }

            if !offlinePeers.isEmpty {
                Section {
                    ForEach(offlinePeers) { peer in
                        PeerCardView(peer: peer)
                    }
                } header: {
                    Text("Offline")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                        .fontWeight(.medium)
                }
            }

            if networkManager.peers.isEmpty && networkManager.wanPeers.isEmpty {
                ContentUnavailableView {
                    Label("No Peers", systemImage: "network.slash")
                } description: {
                    Text("No other ClaudeSync instances found on the local network or via trackers.")
                }
            }
        }
        .padding()
    }
}

// MARK: - Peer Card View

/// A card-style view for a single peer with full details and actions.
/// Shows connection type (LAN/WAN/Relay) and auto-sync status.
struct PeerCardView: View {
    @EnvironmentObject var networkManager: NetworkManager
    let peer: Peer

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            // Header row with name, connection type, and status.
            HStack {
                // Platform icon with connection type distinction.
                ZStack(alignment: .bottomTrailing) {
                    Image(systemName: peer.connectionType == .lan ? peer.platformIcon : "globe")
                        .font(.title2)
                        .foregroundStyle(peer.connectionType == .wan ? .purple : .primary)

                    // Small auto-sync indicator dot.
                    if peer.isAutoSyncSubscribed {
                        Circle()
                            .fill(Color.green)
                            .frame(width: 8, height: 8)
                            .overlay(
                                Circle()
                                    .stroke(Color.white, lineWidth: 1.5)
                            )
                            .offset(x: 3, y: 3)
                    }
                }

                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 6) {
                        Text(peer.name)
                            .font(.headline)

                        // Connection type badge.
                        Text(peer.connectionTypeLabel)
                            .font(.system(size: 9, weight: .semibold))
                            .padding(.horizontal, 5)
                            .padding(.vertical, 2)
                            .background(connectionTypeColor.opacity(0.15))
                            .foregroundStyle(connectionTypeColor)
                            .clipShape(RoundedRectangle(cornerRadius: 3))
                    }

                    Text(peer.platform)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                Spacer()

                StatusBadge(status: peer.status)
            }

            // Details row.
            HStack(spacing: 16) {
                Label("\(peer.configCount) configs", systemImage: "doc.on.doc")
                    .font(.caption)
                    .foregroundStyle(.secondary)

                if peer.differingFileCount > 0 {
                    Label("\(peer.differingFileCount) differ", systemImage: "exclamationmark.triangle")
                        .font(.caption)
                        .foregroundStyle(.orange)
                }

                // Auto-sync last sync timestamp.
                if let lastSync = peer.lastAutoSyncDescription {
                    Label("Last sync: \(lastSync)", systemImage: "arrow.triangle.2.circlepath")
                        .font(.caption2)
                        .foregroundStyle(.green)
                }

                Spacer()

                Text("Last seen: \(peer.lastSeenDescription)")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }

            // Action buttons.
            if peer.status != .offline {
                HStack(spacing: 8) {
                    if peer.status == .discovered || peer.status == .error {
                        Button("Connect") {
                            Task { await networkManager.connectToPeer(peer) }
                        }
                        .buttonStyle(.bordered)
                        .controlSize(.small)
                    }

                    if peer.status.canSync {
                        Button("Compare") {
                            Task { await networkManager.compareWithPeer(peer) }
                        }
                        .buttonStyle(.bordered)
                        .controlSize(.small)

                        if peer.remoteManifest != nil && peer.differingFileCount > 0 {
                            Button("Push") {
                                Task { await networkManager.pushToPeer(peer) }
                            }
                            .buttonStyle(.borderedProminent)
                            .controlSize(.small)

                            Button("Pull") {
                                Task { await networkManager.pullFromPeer(peer) }
                            }
                            .buttonStyle(.bordered)
                            .controlSize(.small)
                        }
                    }
                }
            }
        }
        .padding(12)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 8))
    }

    /// Color for the connection type badge based on LAN/WAN/Relay.
    private var connectionTypeColor: Color {
        switch peer.connectionType {
        case .lan: return .blue
        case .wan: return .purple
        case .relay: return .yellow
        }
    }
}

// MARK: - Status Badge

/// A small badge showing the sync status with colored indicator and label.
struct StatusBadge: View {
    let status: SyncStatus

    var body: some View {
        HStack(spacing: 4) {
            if status.isActive {
                ProgressView()
                    .controlSize(.mini)
            } else {
                Circle()
                    .fill(status.indicatorColor)
                    .frame(width: 8, height: 8)
            }
            Text(status.displayName)
                .font(.caption2)
                .fontWeight(.medium)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(status.indicatorColor.opacity(0.1), in: Capsule())
    }
}
