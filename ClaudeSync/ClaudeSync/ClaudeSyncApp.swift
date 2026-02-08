// ClaudeSyncApp.swift
// ClaudeSync - Peer-to-peer LAN config sync for Claude Code
//
// App entry point. Menu bar only application using MenuBarExtra with window style.
// No dock icon (LSUIElement = YES). Owns the NetworkManager and starts
// advertising/browsing immediately on launch.

import SwiftUI

@main
struct ClaudeSyncApp: App {
    // NetworkManager is the root observable object that owns all networking state.
    // It starts Bonjour advertising and browsing on initialization.
    @StateObject private var networkManager = NetworkManager()

    var body: some Scene {
        MenuBarExtra {
            MenuBarView()
                .environmentObject(networkManager)
                .frame(width: 360, minHeight: 300, maxHeight: 600)
        } label: {
            // The menu bar icon indicates sync capability.
            // Uses a system symbol that communicates bidirectional sync.
            Label("Claude Sync", systemImage: "arrow.triangle.2.circlepath")
        }
        .menuBarExtraStyle(.window)

        Settings {
            SettingsView()
                .environmentObject(networkManager)
        }
    }
}
