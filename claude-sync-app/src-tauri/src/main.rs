// ==========================================================================
// Claude Sync - Desktop entry point
// Prevents the console window from opening on Windows release builds,
// then delegates to the shared library entry point.
// ==========================================================================

// Prevents an additional console window on Windows in release builds.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    // Initialize logging for development/debugging
    env_logger::init();

    // Delegate to the shared library run() function
    claude_sync_app_lib::run();
}
