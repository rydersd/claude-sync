// PeerListView.swift
// ClaudeSync
//
// Standalone peer list view used for larger presentations (e.g. settings window).
// Shows all discovered peers with their status, config counts, and actions.
// Groups peers by online/offline status.

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

            if networkManager.peers.isEmpty {
                ContentUnavailableView {
                    Label("No Peers", systemImage: "network.slash")
                } description: {
                    Text("No other ClaudeSync instances found on the local network.")
                }
            }
        }
        .padding()
    }
}

// MARK: - Peer Card View

/// A card-style view for a single peer with full details and actions.
struct PeerCardView: View {
    @EnvironmentObject var networkManager: NetworkManager
    let peer: Peer

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            // Header row with name and status.
            HStack {
                Image(systemName: peer.platformIcon)
                    .font(.title2)

                VStack(alignment: .leading, spacing: 2) {
                    Text(peer.name)
                        .font(.headline)
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
