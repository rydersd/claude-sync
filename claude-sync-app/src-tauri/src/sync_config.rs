// ==========================================================================
// Sync configuration management
// Persists user preferences for trackers, auto-sync, and security settings.
// Stored at ~/.claude/sync-config.json alongside the synced config files.
// ==========================================================================

use serde::{Deserialize, Serialize};
use std::fs;
use std::path::PathBuf;

/// Config file name (stored in ~/.claude/).
const CONFIG_FILENAME: &str = "sync-config.json";

/// Top-level sync configuration.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SyncConfig {
    /// List of tracker servers for WAN peer discovery
    #[serde(default)]
    pub trackers: Vec<TrackerConfig>,
    /// Auto-sync behavior settings
    #[serde(default)]
    pub auto_sync: AutoSyncConfig,
    /// Security and pairing settings
    #[serde(default)]
    pub security: SecurityConfig,
}

/// Configuration for a single tracker server.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TrackerConfig {
    /// WebSocket URL (e.g., "wss://tracker.example.com/ws")
    pub url: String,
    /// Human-readable name for display
    pub name: String,
    /// Whether this tracker is currently enabled
    pub enabled: bool,
}

/// Auto-sync behavior configuration.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AutoSyncConfig {
    /// Whether auto-sync is enabled (file watching + broadcast)
    #[serde(default = "default_true")]
    pub enabled: bool,
    /// Debounce delay in milliseconds before processing file changes
    #[serde(default = "default_debounce")]
    pub debounce_ms: u32,
}

/// Security and pairing configuration.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SecurityConfig {
    /// Whether WAN connections require device pairing
    #[serde(default = "default_true")]
    pub require_pairing: bool,
    /// Whether to allow unpaired devices on the local LAN
    /// (LAN peers are implicitly more trusted since they share the same network)
    #[serde(default = "default_true")]
    pub allow_unpaired_lan: bool,
}

// -- Default value functions for serde --------------------------------------

fn default_true() -> bool {
    true
}

fn default_debounce() -> u32 {
    500
}

// -- Default trait impls ----------------------------------------------------

impl Default for SyncConfig {
    fn default() -> Self {
        Self {
            trackers: Vec::new(),
            auto_sync: AutoSyncConfig::default(),
            security: SecurityConfig::default(),
        }
    }
}

impl Default for AutoSyncConfig {
    fn default() -> Self {
        Self {
            enabled: true,
            debounce_ms: 500,
        }
    }
}

impl Default for SecurityConfig {
    fn default() -> Self {
        Self {
            require_pairing: true,
            allow_unpaired_lan: true,
        }
    }
}

impl SyncConfig {
    /// Load configuration from ~/.claude/sync-config.json.
    /// If the file doesn't exist, returns defaults and creates the file.
    pub fn load() -> Self {
        let config_path = Self::config_path();

        if !config_path.exists() {
            let default_config = Self::default();
            // Best-effort: write the default config so the user can discover it
            let _ = default_config.save();
            return default_config;
        }

        match fs::read_to_string(&config_path) {
            Ok(contents) => {
                match serde_json::from_str::<SyncConfig>(&contents) {
                    Ok(config) => config,
                    Err(e) => {
                        log::warn!(
                            "Failed to parse sync config at {:?}: {}. Using defaults.",
                            config_path, e
                        );
                        Self::default()
                    }
                }
            }
            Err(e) => {
                log::warn!(
                    "Failed to read sync config at {:?}: {}. Using defaults.",
                    config_path, e
                );
                Self::default()
            }
        }
    }

    /// Save configuration to ~/.claude/sync-config.json atomically.
    pub fn save(&self) -> Result<(), Box<dyn std::error::Error>> {
        let config_path = Self::config_path();

        // Ensure parent directory exists
        if let Some(parent) = config_path.parent() {
            fs::create_dir_all(parent)?;
        }

        let tmp_path = config_path.with_extension("json.tmp");
        let json = serde_json::to_string_pretty(self)?;
        fs::write(&tmp_path, &json)?;
        fs::rename(&tmp_path, &config_path)?;

        log::info!("Saved sync config to {:?}", config_path);
        Ok(())
    }

    /// Add a tracker to the configuration.
    pub fn add_tracker(&mut self, url: String, name: String) {
        // Avoid duplicates by URL
        if self.trackers.iter().any(|t| t.url == url) {
            log::info!("Tracker {} already exists, skipping add", url);
            return;
        }
        self.trackers.push(TrackerConfig {
            url,
            name,
            enabled: true,
        });
    }

    /// Remove a tracker by URL.
    pub fn remove_tracker(&mut self, url: &str) {
        self.trackers.retain(|t| t.url != url);
    }

    /// Enable or disable a tracker by URL.
    pub fn toggle_tracker(&mut self, url: &str, enabled: bool) {
        if let Some(tracker) = self.trackers.iter_mut().find(|t| t.url == url) {
            tracker.enabled = enabled;
        }
    }

    /// Get the path to the config file (~/.claude/sync-config.json).
    fn config_path() -> PathBuf {
        crate::config_scanner::claude_home_dir().join(CONFIG_FILENAME)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_default_config_values() {
        let config = SyncConfig::default();
        assert!(config.trackers.is_empty());
        assert!(config.auto_sync.enabled);
        assert_eq!(config.auto_sync.debounce_ms, 500);
        assert!(config.security.require_pairing);
        assert!(config.security.allow_unpaired_lan);
    }

    #[test]
    fn test_add_tracker() {
        let mut config = SyncConfig::default();
        config.add_tracker("wss://tracker.example.com/ws".to_string(), "Test".to_string());
        assert_eq!(config.trackers.len(), 1);
        assert_eq!(config.trackers[0].url, "wss://tracker.example.com/ws");
        assert!(config.trackers[0].enabled);

        // Adding same URL should not create duplicate
        config.add_tracker("wss://tracker.example.com/ws".to_string(), "Test 2".to_string());
        assert_eq!(config.trackers.len(), 1);
    }

    #[test]
    fn test_remove_tracker() {
        let mut config = SyncConfig::default();
        config.add_tracker("wss://a.com/ws".to_string(), "A".to_string());
        config.add_tracker("wss://b.com/ws".to_string(), "B".to_string());
        assert_eq!(config.trackers.len(), 2);

        config.remove_tracker("wss://a.com/ws");
        assert_eq!(config.trackers.len(), 1);
        assert_eq!(config.trackers[0].url, "wss://b.com/ws");
    }

    #[test]
    fn test_toggle_tracker() {
        let mut config = SyncConfig::default();
        config.add_tracker("wss://a.com/ws".to_string(), "A".to_string());
        assert!(config.trackers[0].enabled);

        config.toggle_tracker("wss://a.com/ws", false);
        assert!(!config.trackers[0].enabled);

        config.toggle_tracker("wss://a.com/ws", true);
        assert!(config.trackers[0].enabled);
    }

    #[test]
    fn test_serde_roundtrip() {
        let mut config = SyncConfig::default();
        config.add_tracker("wss://example.com/ws".to_string(), "Example".to_string());
        config.auto_sync.debounce_ms = 1000;
        config.security.require_pairing = false;

        let json = serde_json::to_string(&config).unwrap();
        let deserialized: SyncConfig = serde_json::from_str(&json).unwrap();

        assert_eq!(deserialized.trackers.len(), 1);
        assert_eq!(deserialized.auto_sync.debounce_ms, 1000);
        assert!(!deserialized.security.require_pairing);
    }
}
