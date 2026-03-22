// ==========================================================================
// Settings merger - handles portable vs machine-specific settings.json keys
// When syncing settings.json, only portable keys are transferred.
// Machine-specific keys (env, mcpServers, projects) stay local.
// ==========================================================================

use serde_json::{Map, Value};

/// Keys in settings.json that are portable (safe to sync between machines).
/// Must match the macOS app's SettingsMerger.portableKeys.
const PORTABLE_KEYS: &[&str] = &["hooks", "statusLine", "attribution", "permissions", "theme", "teammateMode"];

/// Keys in settings.json that are machine-specific (never synced).
const MACHINE_SPECIFIC_KEYS: &[&str] = &["env", "mcpServers", "projects"];

/// Specific env var keys promoted to sync between machines.
/// The env block as a whole remains machine-specific — only these named keys transfer.
/// [EXPERIMENTAL -> STANDARD]
const RECOMMENDED_ENV_KEYS: &[&str] = &["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"];

/// Extract only the portable keys from a settings.json value.
/// Returns a new JSON object containing only the keys safe to sync.
pub fn extract_portable(settings: &Value) -> Value {
    match settings {
        Value::Object(map) => {
            let mut portable = Map::new();
            for key in PORTABLE_KEYS {
                if let Some(value) = map.get(*key) {
                    portable.insert(key.to_string(), value.clone());
                }
            }
            // Extract recommended env keys (specific keys promoted from env block)
            if let Some(Value::Object(env_map)) = map.get("env") {
                let mut rec_env = Map::new();
                for key in RECOMMENDED_ENV_KEYS {
                    if let Some(value) = env_map.get(*key) {
                        rec_env.insert(key.to_string(), value.clone());
                    }
                }
                if !rec_env.is_empty() {
                    portable.insert("env".to_string(), Value::Object(rec_env));
                }
            }
            Value::Object(portable)
        }
        _ => Value::Object(Map::new()),
    }
}

/// Deep merge overlay into base. Overlay values win for leaf nodes.
/// Both values must be JSON objects; non-object values are replaced entirely.
pub fn deep_merge(base: &Value, overlay: &Value) -> Value {
    match (base, overlay) {
        (Value::Object(base_map), Value::Object(overlay_map)) => {
            let mut result = base_map.clone();
            for (key, overlay_value) in overlay_map {
                let merged = if let Some(base_value) = result.get(key) {
                    deep_merge(base_value, overlay_value)
                } else {
                    overlay_value.clone()
                };
                result.insert(key.clone(), merged);
            }
            Value::Object(result)
        }
        // For non-object types, overlay wins
        (_, overlay) => overlay.clone(),
    }
}

/// Prepare settings for push: extract only portable keys.
pub fn merge_for_push(home_settings: &Value) -> Value {
    extract_portable(home_settings)
}

/// Prepare settings for pull: merge remote portable keys into local settings.
/// Machine-specific keys in local_settings are preserved.
pub fn merge_for_pull(local_settings: &Value, repo_settings: &Value) -> Value {
    let mut portable = extract_portable(repo_settings);
    // Pop env from portable before deep merge — env needs surgical key-level merge,
    // not wholesale replacement (which would clobber local-only env vars).
    let remote_rec_env = portable.as_object_mut()
        .and_then(|m| m.remove("env"));
    let mut result = deep_merge(local_settings, &portable);
    // Merge only recommended env keys into local env without clobbering
    if let Some(Value::Object(rec_env)) = remote_rec_env {
        if let Some(result_obj) = result.as_object_mut() {
            let local_env = result_obj
                .entry("env")
                .or_insert_with(|| Value::Object(Map::new()));
            if let Value::Object(local_env_map) = local_env {
                for key in RECOMMENDED_ENV_KEYS {
                    if let Some(value) = rec_env.get(*key) {
                        local_env_map.insert(key.to_string(), value.clone());
                    }
                }
            }
        }
    }
    result
}

/// Check if a settings object contains any machine-specific keys
/// that should not be synced.
pub fn has_machine_specific_keys(settings: &Value) -> bool {
    match settings {
        Value::Object(map) => {
            for key in MACHINE_SPECIFIC_KEYS {
                if map.contains_key(*key) {
                    return true;
                }
            }
            false
        }
        _ => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn test_extract_portable_keeps_only_portable_keys() {
        let settings = json!({
            "hooks": {"PreToolUse": []},
            "env": {"PATH": "/usr/bin"},
            "permissions": {"allow": ["*"]},
            "statusLine": "custom",
            "theme": "dark",
            "mcpServers": {"server1": {}}
        });

        let portable = extract_portable(&settings);
        let map = portable.as_object().unwrap();

        assert!(map.contains_key("hooks"));
        assert!(map.contains_key("statusLine"));
        assert!(map.contains_key("permissions"));
        assert!(map.contains_key("theme"));
        assert!(!map.contains_key("env"));
        assert!(!map.contains_key("mcpServers"));
    }

    #[test]
    fn test_deep_merge_overlay_wins() {
        let base = json!({
            "hooks": {"PreToolUse": ["old"]},
            "local_only": true
        });
        let overlay = json!({
            "hooks": {"PreToolUse": ["new"], "PostToolUse": ["added"]},
            "new_key": "value"
        });

        let merged = deep_merge(&base, &overlay);
        let map = merged.as_object().unwrap();

        // overlay's hooks should be merged
        assert_eq!(
            map["hooks"]["PostToolUse"],
            json!(["added"])
        );
        // overlay wins for conflicting leaf values
        assert_eq!(
            map["hooks"]["PreToolUse"],
            json!(["new"])
        );
        // base-only keys preserved
        assert_eq!(map["local_only"], json!(true));
        // overlay-only keys added
        assert_eq!(map["new_key"], json!("value"));
    }

    #[test]
    fn test_merge_for_pull_preserves_local_machine_keys() {
        let local = json!({
            "env": {"MY_VAR": "secret"},
            "permissions": {"allow": ["*"]},
            "hooks": {"old": true}
        });
        let remote = json!({
            "hooks": {"new": true},
            "env": {"SHOULD_BE_IGNORED": true},
            "statusLine": "from remote"
        });

        let result = merge_for_pull(&local, &remote);
        let map = result.as_object().unwrap();

        // Local machine-specific keys preserved
        assert!(map.contains_key("env"));
        assert_eq!(map["env"]["MY_VAR"], json!("secret"));
        // Remote's env should NOT override local (it's stripped by extract_portable)
        assert!(!map["env"].as_object().unwrap().contains_key("SHOULD_BE_IGNORED"));

        // Portable keys merged from remote
        assert!(map.contains_key("statusLine"));
        assert_eq!(map["statusLine"], json!("from remote"));

        // Hooks merged
        assert_eq!(map["hooks"]["new"], json!(true));
        assert_eq!(map["hooks"]["old"], json!(true));
    }

    #[test]
    fn test_has_machine_specific_keys() {
        assert!(has_machine_specific_keys(&json!({"env": {}})));
        assert!(has_machine_specific_keys(&json!({"mcpServers": {}})));
        assert!(has_machine_specific_keys(&json!({"projects": {}})));
        assert!(!has_machine_specific_keys(&json!({"hooks": {}})));
        assert!(!has_machine_specific_keys(&json!({"permissions": {}})));
        assert!(!has_machine_specific_keys(&json!({})));
    }
}
