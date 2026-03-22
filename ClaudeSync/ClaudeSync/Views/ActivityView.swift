// ActivityView.swift
// ClaudeSync
//
// Live activity feed showing sync events, tracker status, and file changes.
// Displayed as a collapsible section in the menu bar popover.

import SwiftUI

// MARK: - Activity Section (for MenuBarView)

/// Collapsible activity section showing recent sync events.
struct ActivitySection: View {
    @EnvironmentObject var networkManager: NetworkManager
    @ObservedObject var activityLog: ActivityLog

    @State private var isExpanded = true

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Section header with expand/collapse and event count.
            Button {
                withAnimation(.easeInOut(duration: 0.2)) {
                    isExpanded.toggle()
                    if isExpanded { activityLog.markRead() }
                }
            } label: {
                HStack(spacing: 6) {
                    Image(systemName: "list.bullet.rectangle")
                        .font(.caption2)
                        .foregroundStyle(.secondary)

                    Text("ACTIVITY")
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                        .fontWeight(.medium)

                    if activityLog.hasUnread {
                        Circle()
                            .fill(Color.blue)
                            .frame(width: 6, height: 6)
                    }

                    Spacer()

                    if let latest = activityLog.latestEvent, !isExpanded {
                        // Show latest event summary when collapsed.
                        HStack(spacing: 3) {
                            Image(systemName: latest.iconName)
                                .font(.system(size: 8))
                                .foregroundStyle(latest.iconColor)
                            Text(latest.message)
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                                .lineLimit(1)
                        }
                    }

                    Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 6)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            if isExpanded {
                if activityLog.events.isEmpty {
                    HStack {
                        Spacer()
                        Text("No activity yet")
                            .font(.caption)
                            .foregroundStyle(.tertiary)
                        Spacer()
                    }
                    .padding(.vertical, 12)
                } else {
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 0) {
                            ForEach(activityLog.events.prefix(20)) { event in
                                ActivityEventRow(event: event)
                            }
                        }
                    }
                    .frame(maxHeight: 160)
                }
            }
        }
    }
}

// MARK: - Activity Event Row

/// A single row in the activity feed.
struct ActivityEventRow: View {
    let event: ActivityEvent

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: event.iconName)
                .font(.system(size: 10))
                .foregroundStyle(event.iconColor)
                .frame(width: 14, height: 14)
                .padding(.top, 1)

            VStack(alignment: .leading, spacing: 1) {
                Text(event.message)
                    .font(.caption)
                    .foregroundStyle(.primary)
                    .lineLimit(2)

                if let detail = event.detail {
                    Text(detail)
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                        .lineLimit(1)
                }
            }

            Spacer()

            Text(event.relativeTime)
                .font(.system(size: 9))
                .foregroundStyle(.quaternary)
                .monospacedDigit()
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 4)
    }
}

// MARK: - Status Dashboard (compact status bar)

/// Compact horizontal dashboard showing key system status at a glance.
struct StatusDashboard: View {
    @EnvironmentObject var networkManager: NetworkManager

    var body: some View {
        HStack(spacing: 12) {
            // LAN status
            StatusPill(
                icon: "wifi",
                label: "\(networkManager.peers.count) LAN",
                color: networkManager.isOnline ? .green : .red,
                isActive: networkManager.isOnline
            )

            // Tracker status
            if !networkManager.syncConfig.trackers.isEmpty {
                StatusPill(
                    icon: "globe",
                    label: networkManager.isTrackerConnected
                        ? "\(networkManager.wanPeers.count) WAN"
                        : "Tracker",
                    color: networkManager.isTrackerConnected ? .purple : .red,
                    isActive: networkManager.isTrackerConnected
                )
            }

            // File watcher status
            if networkManager.isAutoSyncEnabled {
                StatusPill(
                    icon: "eye",
                    label: "Watching",
                    color: networkManager.isWatching ? .green : .yellow,
                    isActive: networkManager.isWatching
                )
            }

            Spacer()

            // Config count
            Text("\(networkManager.localConfigCount) files")
                .font(.system(size: 10))
                .foregroundStyle(.tertiary)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 6)
        .background(Color.primary.opacity(0.02))
    }
}

// MARK: - Status Pill

/// Small rounded indicator showing a status with icon, label, and color.
struct StatusPill: View {
    let icon: String
    let label: String
    let color: Color
    let isActive: Bool

    var body: some View {
        HStack(spacing: 4) {
            Circle()
                .fill(isActive ? color : color.opacity(0.4))
                .frame(width: 6, height: 6)

            Image(systemName: icon)
                .font(.system(size: 9))
                .foregroundStyle(isActive ? color : .secondary)

            Text(label)
                .font(.system(size: 10, weight: .medium))
                .foregroundStyle(isActive ? .primary : .secondary)
        }
        .padding(.horizontal, 6)
        .padding(.vertical, 3)
        .background(color.opacity(isActive ? 0.08 : 0.03), in: Capsule())
    }
}
