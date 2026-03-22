// ==========================================================================
// File system watcher using the notify crate (OS-native events).
// Watches ~/.claude/ for changes to syncable files and emits debounced
// batches of changed paths via a tokio mpsc channel.
//
// Debounce strategy:
//   - First event starts a 500ms timer
//   - Subsequent events reset the timer
//   - At 2 seconds from the first event, force-flush the batch
// ==========================================================================

use notify::{Event, EventKind, RecommendedWatcher, RecursiveMode, Watcher};
use std::collections::HashSet;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use tokio::sync::mpsc;

/// Debounce window: 500ms after last event before flushing the batch.
const DEBOUNCE_MS: u64 = 500;

/// Maximum time from first event to forced flush (caps latency).
const MAX_BATCH_MS: u64 = 2000;

/// Watches a directory for file changes using OS-native events.
/// Debounces rapid changes into batches and sends them via a tokio channel.
pub struct FileWatcher {
    /// The notify watcher handle (dropped to stop watching)
    watcher: Option<RecommendedWatcher>,
    /// Root directory being watched (e.g., ~/.claude/)
    watch_path: PathBuf,
    /// Channel sender for batched change notifications.
    /// Each batch is a set of relative paths that changed.
    change_tx: mpsc::Sender<HashSet<String>>,
    /// Atomic flag indicating whether the watcher is currently active.
    is_watching: Arc<AtomicBool>,
}

impl FileWatcher {
    /// Create a new FileWatcher for the given directory.
    /// Changed paths are sent as batches through `change_tx`.
    pub fn new(watch_path: PathBuf, change_tx: mpsc::Sender<HashSet<String>>) -> Self {
        Self {
            watcher: None,
            watch_path,
            change_tx,
            is_watching: Arc::new(AtomicBool::new(false)),
        }
    }

    /// Start watching the directory. Spawns a background tokio task
    /// that debounces raw notify events into batches.
    pub fn start(&mut self) -> Result<(), Box<dyn std::error::Error>> {
        if self.is_watching.load(Ordering::SeqCst) {
            return Ok(());
        }

        let watch_path = self.watch_path.clone();
        let change_tx = self.change_tx.clone();
        let is_watching = Arc::clone(&self.is_watching);

        // Create a std::sync channel to bridge notify (sync) -> tokio (async)
        let (raw_tx, raw_rx) = std::sync::mpsc::channel::<Event>();

        // Create the OS-native file watcher
        let mut watcher = notify::recommended_watcher(move |res: Result<Event, notify::Error>| {
            if let Ok(event) = res {
                let _ = raw_tx.send(event);
            }
        })?;

        // Watch the entire directory tree recursively
        watcher.watch(&self.watch_path, RecursiveMode::Recursive)?;
        self.watcher = Some(watcher);
        self.is_watching.store(true, Ordering::SeqCst);

        // Spawn the debounce loop on the tokio runtime
        tokio::spawn(async move {
            debounce_loop(raw_rx, change_tx, watch_path, is_watching).await;
        });

        log::info!(
            "FileWatcher started for: {}",
            self.watch_path.display()
        );

        Ok(())
    }

    /// Stop watching. Drops the notify watcher handle.
    pub fn stop(&mut self) {
        self.watcher = None;
        self.is_watching.store(false, Ordering::SeqCst);
        log::info!("FileWatcher stopped");
    }

    /// Whether the watcher is currently active.
    pub fn is_watching(&self) -> bool {
        self.is_watching.load(Ordering::SeqCst)
    }
}

/// Background task that receives raw notify events, filters them,
/// converts absolute paths to relative, and debounces into batches.
async fn debounce_loop(
    raw_rx: std::sync::mpsc::Receiver<Event>,
    change_tx: mpsc::Sender<HashSet<String>>,
    watch_path: PathBuf,
    is_watching: Arc<AtomicBool>,
) {
    let mut pending: HashSet<String> = HashSet::new();
    let mut batch_start: Option<tokio::time::Instant> = None;
    let mut last_event: Option<tokio::time::Instant> = None;

    loop {
        if !is_watching.load(Ordering::SeqCst) {
            break;
        }

        // Calculate how long to wait before checking for events
        let timeout = if pending.is_empty() {
            // No pending events: block for up to 100ms then check the stop flag
            tokio::time::Duration::from_millis(100)
        } else if let Some(start) = batch_start {
            // We have pending events: check if we should force-flush
            let since_start = start.elapsed().as_millis() as u64;
            if since_start >= MAX_BATCH_MS {
                // Force-flush: max batch window exceeded
                flush_batch(&mut pending, &change_tx, &mut batch_start, &mut last_event).await;
                continue;
            }

            let since_last = last_event.map(|t| t.elapsed().as_millis() as u64).unwrap_or(0);
            if since_last >= DEBOUNCE_MS {
                // Debounce window elapsed: flush
                flush_batch(&mut pending, &change_tx, &mut batch_start, &mut last_event).await;
                continue;
            }

            // Wait for the debounce window to elapse
            let remaining_debounce = DEBOUNCE_MS.saturating_sub(since_last);
            let remaining_max = MAX_BATCH_MS.saturating_sub(since_start);
            tokio::time::Duration::from_millis(remaining_debounce.min(remaining_max))
        } else {
            tokio::time::Duration::from_millis(100)
        };

        // Try to receive raw events with a timeout (non-blocking via tokio sleep)
        let deadline = tokio::time::Instant::now() + timeout;
        let mut got_events = false;

        // Drain all available events from the sync channel
        loop {
            match raw_rx.try_recv() {
                Ok(event) => {
                    got_events = true;
                    process_event(&event, &watch_path, &mut pending);
                }
                Err(std::sync::mpsc::TryRecvError::Empty) => break,
                Err(std::sync::mpsc::TryRecvError::Disconnected) => {
                    // Watcher was dropped; flush remaining and exit
                    if !pending.is_empty() {
                        flush_batch(&mut pending, &change_tx, &mut batch_start, &mut last_event).await;
                    }
                    return;
                }
            }
        }

        if got_events && !pending.is_empty() {
            let now = tokio::time::Instant::now();
            if batch_start.is_none() {
                batch_start = Some(now);
            }
            last_event = Some(now);
        }

        // Sleep until the deadline (avoids busy-spinning)
        tokio::time::sleep_until(deadline).await;
    }

    // Flush any remaining events on shutdown
    if !pending.is_empty() {
        flush_batch(&mut pending, &change_tx, &mut batch_start, &mut last_event).await;
    }
}

/// Extract relative paths from a notify event and add syncable ones to the pending set.
fn process_event(event: &Event, watch_path: &PathBuf, pending: &mut HashSet<String>) {
    // Only react to create, modify, remove, and rename events
    match event.kind {
        EventKind::Create(_)
        | EventKind::Modify(_)
        | EventKind::Remove(_) => {}
        _ => return,
    }

    for abs_path in &event.paths {
        if let Ok(rel) = abs_path.strip_prefix(watch_path) {
            let rel_str = rel.to_string_lossy().replace('\\', "/");
            if !rel_str.is_empty() && is_syncable_path(&rel_str) {
                pending.insert(rel_str);
            }
        }
    }
}

/// Send the pending batch and reset the timer state.
async fn flush_batch(
    pending: &mut HashSet<String>,
    change_tx: &mpsc::Sender<HashSet<String>>,
    batch_start: &mut Option<tokio::time::Instant>,
    last_event: &mut Option<tokio::time::Instant>,
) {
    if pending.is_empty() {
        return;
    }

    let batch = std::mem::take(pending);
    log::info!("FileWatcher flushing batch of {} changed paths", batch.len());

    if let Err(e) = change_tx.send(batch).await {
        log::warn!("FileWatcher failed to send batch: {}", e);
    }

    *batch_start = None;
    *last_event = None;
}

/// Filter function: only fire for files that belong to syncable paths.
/// Matches: agents/, skills/, rules/, hooks/, scripts/, memory/, worksets/, plugins/,
///          CLAUDE.md, settings.json, keybindings.json
/// Excludes: .credentials, projects/, cache/, statsig/, teams/, tasks/, hidden files
pub fn is_syncable_path(path: &str) -> bool {
    // Exclude hidden files (except CLAUDE.md at root, which doesn't start with '.')
    let filename = path.rsplit('/').next().unwrap_or(path);
    if filename.starts_with('.') {
        return false;
    }

    // Syncable path prefixes (mirrors config_scanner::SYNC_PATHS)
    const SYNC_PREFIXES: &[&str] = &[
        "agents/",
        "skills/",
        "rules/",
        "hooks/",
        "scripts/",
        "memory/",
        "worksets/",
        "plugins/",
    ];

    // Exact file matches
    if path == "CLAUDE.md" || path == "settings.json" || path == "keybindings.json" {
        return true;
    }

    // Directory prefix matches
    for prefix in SYNC_PREFIXES {
        if path.starts_with(prefix) {
            return true;
        }
    }

    // Exclude everything else (projects/, cache/, statsig/, .credentials, etc.)
    false
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_is_syncable_path_includes_expected() {
        assert!(is_syncable_path("CLAUDE.md"));
        assert!(is_syncable_path("settings.json"));
        assert!(is_syncable_path("rules/git-commits.md"));
        assert!(is_syncable_path("skills/commit/SKILL.md"));
        assert!(is_syncable_path("hooks/session-start.sh"));
        assert!(is_syncable_path("scripts/tool.py"));
        assert!(is_syncable_path("memory/writing/voice-profile.md"));
        assert!(is_syncable_path("agents/test.md"));
    }

    #[test]
    fn test_is_syncable_path_excludes_expected() {
        assert!(!is_syncable_path(".env"));
        assert!(!is_syncable_path(".credentials"));
        assert!(!is_syncable_path("projects/foo/settings.json"));
        assert!(!is_syncable_path("cache/data.json"));
        assert!(!is_syncable_path("statsig/config.json"));
        assert!(!is_syncable_path("history.jsonl"));
        assert!(!is_syncable_path("random-file.txt"));
        assert!(!is_syncable_path(".DS_Store"));
    }
}
