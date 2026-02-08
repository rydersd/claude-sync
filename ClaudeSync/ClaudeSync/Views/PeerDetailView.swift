// PeerDetailView.swift
// ClaudeSync
//
// Detailed view for a specific peer showing full file comparison,
// sync actions, and connection details. Presented when tapping
// on a peer in the list or from the settings window.

import SwiftUI

struct PeerDetailView: View {
    @EnvironmentObject var networkManager: NetworkManager
    let peer: Peer

    @State private var diffs: [FileDiff] = []
    @State private var filterStatus: FileDiffStatus? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Peer info header.
            peerHeader

            Divider()

            // Filter bar for diff categories.
            if !diffs.isEmpty {
                filterBar
                Divider()
            }

            // File diff list.
            if diffs.isEmpty && peer.remoteManifest != nil {
                VStack(spacing: 8) {
                    Image(systemName: "checkmark.circle")
                        .font(.title)
                        .foregroundStyle(.green)
                    Text("All files are in sync")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .padding()
            } else if peer.remoteManifest == nil {
                VStack(spacing: 8) {
                    Image(systemName: "arrow.triangle.2.circlepath")
                        .font(.title)
                        .foregroundStyle(.secondary)
                    Text("Compare to see file differences")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                    Button("Compare Now") {
                        Task { await networkManager.compareWithPeer(peer) }
                    }
                    .buttonStyle(.bordered)
                    .disabled(!peer.status.canSync)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .padding()
            } else {
                diffList
            }

            Divider()

            // Action footer.
            actionFooter
        }
        .frame(minWidth: 360, minHeight: 400)
        .onAppear {
            refreshDiffs()
        }
        .onChange(of: peer.remoteManifest) {
            refreshDiffs()
        }
    }

    // MARK: - Peer Header

    private var peerHeader: some View {
        HStack(spacing: 12) {
            Image(systemName: peer.platformIcon)
                .font(.title)

            VStack(alignment: .leading, spacing: 2) {
                Text(peer.name)
                    .font(.headline)
                HStack(spacing: 8) {
                    StatusBadge(status: peer.status)
                    Text("\(peer.configCount) configs")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            Spacer()

            Text("Last seen: \(peer.lastSeenDescription)")
                .font(.caption2)
                .foregroundStyle(.tertiary)
        }
        .padding(12)
    }

    // MARK: - Filter Bar

    private var filterBar: some View {
        HStack(spacing: 8) {
            filterButton(label: "All", status: nil, count: diffs.count)

            let localOnly = diffs.filter { $0.status == .localOnly }.count
            let remoteOnly = diffs.filter { $0.status == .remoteOnly }.count
            let modified = diffs.filter { $0.status == .modified }.count

            if localOnly > 0 {
                filterButton(label: "Local", status: .localOnly, count: localOnly)
            }
            if remoteOnly > 0 {
                filterButton(label: "Remote", status: .remoteOnly, count: remoteOnly)
            }
            if modified > 0 {
                filterButton(label: "Modified", status: .modified, count: modified)
            }

            Spacer()
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
    }

    private func filterButton(label: String, status: FileDiffStatus?, count: Int) -> some View {
        Button {
            filterStatus = status
        } label: {
            HStack(spacing: 4) {
                Text(label)
                Text("\(count)")
                    .padding(.horizontal, 6)
                    .padding(.vertical, 1)
                    .background(.quaternary, in: Capsule())
            }
            .font(.caption)
        }
        .buttonStyle(.bordered)
        .controlSize(.small)
        .tint(filterStatus == status ? .accentColor : nil)
    }

    // MARK: - Diff List

    private var diffList: some View {
        let filteredDiffs = filterStatus == nil
            ? diffs.filter { $0.status != .identical }
            : diffs.filter { $0.status == filterStatus }

        return ScrollView {
            LazyVStack(alignment: .leading, spacing: 1) {
                ForEach(filteredDiffs) { diff in
                    DiffRowView(diff: diff)
                }
            }
        }
    }

    // MARK: - Action Footer

    private var actionFooter: some View {
        HStack(spacing: 8) {
            let hasChanges = diffs.contains { $0.status != .identical }

            Button {
                Task { await networkManager.compareWithPeer(peer) }
            } label: {
                Label("Compare", systemImage: "arrow.triangle.2.circlepath")
            }
            .buttonStyle(.bordered)
            .controlSize(.small)
            .disabled(!peer.status.canSync)

            if hasChanges && peer.remoteManifest != nil {
                Button {
                    Task { await networkManager.pushToPeer(peer) }
                } label: {
                    Label("Push All", systemImage: "arrow.up.circle")
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
                .disabled(peer.status.isActive)

                Button {
                    Task { await networkManager.pullFromPeer(peer) }
                } label: {
                    Label("Pull All", systemImage: "arrow.down.circle")
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
                .disabled(peer.status.isActive)
            }

            Spacer()
        }
        .padding(12)
    }

    // MARK: - Helpers

    private func refreshDiffs() {
        diffs = networkManager.diffsWithPeer(peer)
    }
}

// MARK: - Diff Row View

/// A single row showing a file diff status.
struct DiffRowView: View {
    let diff: FileDiff

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: diff.status.iconName)
                .foregroundStyle(diff.status.color)
                .font(.caption)
                .frame(width: 16)

            VStack(alignment: .leading, spacing: 1) {
                Text(diff.relativePath)
                    .font(.caption)
                    .fontDesign(.monospaced)
                    .lineLimit(1)
                    .truncationMode(.middle)

                Text(diff.status.displayName)
                    .font(.caption2)
                    .foregroundStyle(diff.status.color)
            }

            Spacer()

            // Show hashes for modified files.
            if diff.status == .modified {
                VStack(alignment: .trailing, spacing: 1) {
                    if let localHash = diff.localHash {
                        Text("L: \(String(localHash.prefix(8)))")
                            .font(.caption2)
                            .fontDesign(.monospaced)
                            .foregroundStyle(.secondary)
                    }
                    if let remoteHash = diff.remoteHash {
                        Text("R: \(String(remoteHash.prefix(8)))")
                            .font(.caption2)
                            .fontDesign(.monospaced)
                            .foregroundStyle(.secondary)
                    }
                }
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
    }
}
