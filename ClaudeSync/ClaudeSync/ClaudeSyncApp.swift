// ClaudeSyncApp.swift
// ClaudeSync - Peer-to-peer config sync for Claude Code
//
// App entry point. Shows a main dashboard window for monitoring sync status,
// peers, and activity. Also provides a menu bar icon for quick access.
// Owns the NetworkManager and starts advertising/browsing on launch.

import SwiftUI

@main
struct ClaudeSyncApp: App {
    // NetworkManager is the root observable object that owns all networking state.
    // It starts Bonjour advertising and browsing on initialization.
    @StateObject private var networkManager = NetworkManager()

    var body: some Scene {
        // Main dashboard window — the primary UI.
        WindowGroup("Claude Sync") {
            DashboardView()
                .environmentObject(networkManager)
        }
        .defaultSize(width: 720, height: 500)

        // Menu bar icon for quick status when the window is closed.
        MenuBarExtra {
            MenuBarView()
                .environmentObject(networkManager)
                .frame(minWidth: 360, maxWidth: 360, minHeight: 300, maxHeight: 600)
        } label: {
            Label("Claude Sync", systemImage: "arrow.triangle.2.circlepath")
        }
        .menuBarExtraStyle(.window)

        Settings {
            SettingsView()
                .environmentObject(networkManager)
        }
    }
}
