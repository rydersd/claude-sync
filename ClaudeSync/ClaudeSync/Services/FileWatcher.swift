// FileWatcher.swift
// ClaudeSync
//
// Watches ~/.claude/ for file changes using FSEvents (macOS file system events API).
// Provides debounced change notifications: accumulates changed paths in a Set<String>,
// resets a 500ms timer on each new event, and force-flushes at 2 seconds to prevent
// unbounded batching during rapid multi-file saves.
//
// Only fires for files matching ConfigScanner's syncable patterns (rules/*.md,
// skills/**/* , hooks/**/* , CLAUDE.md, settings.json, memory/**/* , etc.)

import Foundation
import os

/// Watches ~/.claude/ for file changes using FSEvents and delivers debounced
/// batches of changed relative paths. Actor-isolated for thread safety.
actor FileWatcher {

    // MARK: - Types

    /// Callback type for delivering batches of changed file paths.
    /// Paths are relative to the watched directory (e.g. "rules/git-commits.md").
    typealias FilesChangedHandler = @Sendable (Set<String>) async -> Void

    // MARK: - Configuration

    /// Debounce window: timer resets to this interval on each new event.
    private static let debounceInterval: TimeInterval = 0.5

    /// Maximum batch accumulation time before force-flushing.
    private static let maxBatchInterval: TimeInterval = 2.0

    // MARK: - Properties

    /// The directory to watch (typically ~/.claude/).
    private let watchDirectory: URL

    /// Callback invoked with batches of changed relative paths.
    private let onFilesChanged: FilesChangedHandler

    /// Logger for file watcher events.
    private let logger = Logger(subsystem: "com.claudesync", category: "FileWatcher")

    /// The FSEventStream reference, nil when not watching.
    private var eventStream: FSEventStreamRef?

    /// Retained reference to the callback bridge, released when the watcher stops.
    private var retainedBridge: Unmanaged<FileWatcherCallbackBridge>?

    /// Dedicated dispatch queue for FSEventStream callbacks.
    private let streamQueue = DispatchQueue(label: "com.claudesync.filewatcher.stream")

    /// Accumulated changed paths waiting to be flushed.
    private var pendingPaths: Set<String> = []

    /// The debounce timer task. Resets on each new event.
    private var debounceTask: Task<Void, Never>?

    /// The force-flush timer task. Starts with the first event in a batch,
    /// fires after maxBatchInterval regardless of ongoing events.
    private var forceFlushTask: Task<Void, Never>?

    /// Whether the watcher is currently active.
    private(set) var isWatching: Bool = false

    // MARK: - Initialization

    /// Creates a file watcher for the given directory.
    /// - Parameters:
    ///   - directory: The directory to watch. Defaults to ~/.claude/.
    ///   - onFilesChanged: Callback invoked with batches of changed relative paths.
    init(
        directory: URL? = nil,
        onFilesChanged: @escaping FilesChangedHandler
    ) {
        self.watchDirectory = directory ?? FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".claude")
        self.onFilesChanged = onFilesChanged
    }

    // MARK: - Lifecycle

    /// Starts watching the directory for file changes.
    /// Idempotent: calling start() when already watching is a no-op.
    func start() {
        guard !isWatching else {
            logger.info("FileWatcher already active, ignoring start()")
            return
        }

        // Ensure the watched directory exists before creating the stream.
        var isDirectory: ObjCBool = false
        guard FileManager.default.fileExists(atPath: watchDirectory.path, isDirectory: &isDirectory),
              isDirectory.boolValue else {
            logger.error("Watch directory does not exist: \(self.watchDirectory.path)")
            return
        }

        let watchPath = watchDirectory.path as CFString
        let pathsToWatch = [watchPath] as CFArray

        // FSEvents context pointing back to this actor through an unmanaged pointer.
        // We use a class wrapper to bridge the actor reference into the C callback.
        let callbackBridge = FileWatcherCallbackBridge(watcher: self)
        let bridge = Unmanaged.passRetained(callbackBridge)
        self.retainedBridge = bridge

        var context = FSEventStreamContext(
            version: 0,
            info: bridge.toOpaque(),
            retain: nil,
            release: nil,
            copyDescription: nil
        )

        // Create the FSEventStream with file-level granularity and no-defer mode.
        guard let stream = FSEventStreamCreate(
            kCFAllocatorDefault,
            FileWatcher.fsEventCallback,
            &context,
            pathsToWatch,
            FSEventStreamEventId(kFSEventStreamEventIdSinceNow),
            0.1, // latency: 100ms at the FSEvents level (our debounce handles the rest)
            UInt32(kFSEventStreamCreateFlagFileEvents | kFSEventStreamCreateFlagNoDefer)
        ) else {
            logger.error("Failed to create FSEventStream for \(self.watchDirectory.path)")
            self.retainedBridge?.release()
            self.retainedBridge = nil
            return
        }

        eventStream = stream
        FSEventStreamSetDispatchQueue(stream, streamQueue)
        FSEventStreamStart(stream)

        isWatching = true
        logger.info("FileWatcher started for \(self.watchDirectory.path)")
    }

    /// Stops watching and cancels any pending debounce timers.
    /// Flushes any accumulated changes before stopping.
    func stop() {
        guard isWatching else { return }

        // Cancel pending timers.
        debounceTask?.cancel()
        debounceTask = nil
        forceFlushTask?.cancel()
        forceFlushTask = nil

        // Flush any remaining pending paths.
        if !pendingPaths.isEmpty {
            let pathsToFlush = pendingPaths
            pendingPaths.removeAll()
            Task { [onFilesChanged] in
                await onFilesChanged(pathsToFlush)
            }
        }

        // Stop and invalidate the FSEventStream.
        if let stream = eventStream {
            FSEventStreamStop(stream)
            FSEventStreamInvalidate(stream)
            FSEventStreamRelease(stream)
            eventStream = nil
        }

        // Release the retained callback bridge to avoid memory leaks.
        retainedBridge?.release()
        retainedBridge = nil

        isWatching = false
        logger.info("FileWatcher stopped")
    }

    // MARK: - Event Processing

    /// Called from the FSEvents callback bridge when file system events occur.
    /// Filters paths through ConfigScanner's syncable patterns and accumulates
    /// relative paths for debounced delivery.
    func handleFSEvents(paths: [String]) {
        let basePath = watchDirectory.path
        let basePathWithSlash = basePath.hasSuffix("/") ? basePath : basePath + "/"

        var addedAny = false

        for absolutePath in paths {
            // Convert to relative path within the watched directory.
            guard absolutePath.hasPrefix(basePathWithSlash) else { continue }
            let relativePath = String(absolutePath.dropFirst(basePathWithSlash.count))

            // Skip empty paths and directories (FSEvents includes directory events too).
            guard !relativePath.isEmpty else { continue }

            // Filter through syncable patterns (same logic as ConfigScanner).
            guard isSyncablePath(relativePath) else { continue }

            pendingPaths.insert(relativePath)
            addedAny = true
        }

        guard addedAny else { return }

        // Start the force-flush timer on the first event in a new batch.
        if forceFlushTask == nil {
            forceFlushTask = Task { [weak self] in
                try? await Task.sleep(nanoseconds: UInt64(Self.maxBatchInterval * 1_000_000_000))
                guard !Task.isCancelled else { return }
                await self?.flushPendingPaths()
            }
        }

        // Reset the debounce timer on every new event.
        debounceTask?.cancel()
        debounceTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(Self.debounceInterval * 1_000_000_000))
            guard !Task.isCancelled else { return }
            await self?.flushPendingPaths()
        }
    }

    /// Delivers accumulated paths to the callback and resets both timers.
    private func flushPendingPaths() {
        guard !pendingPaths.isEmpty else { return }

        let pathsToDeliver = pendingPaths
        pendingPaths.removeAll()

        // Cancel both timers since we're flushing now.
        debounceTask?.cancel()
        debounceTask = nil
        forceFlushTask?.cancel()
        forceFlushTask = nil

        logger.info("Flushing \(pathsToDeliver.count) changed paths")

        Task { [onFilesChanged] in
            await onFilesChanged(pathsToDeliver)
        }
    }

    // MARK: - Path Filtering

    /// Checks if a relative path matches ConfigScanner's syncable patterns.
    /// Mirrors the logic in ConfigScanner.isSyncable() and exclusion checks.
    private func isSyncablePath(_ relativePath: String) -> Bool {
        // Check exclusions first.
        for excludePath in ConfigScanner.excludePaths {
            if excludePath.hasSuffix("/") {
                let dirName = String(excludePath.dropLast())
                if relativePath == dirName || relativePath.hasPrefix(dirName + "/") {
                    return false
                }
            } else {
                if relativePath == excludePath {
                    return false
                }
            }
        }

        // Check walk exclude patterns on the filename.
        let filename = (relativePath as NSString).lastPathComponent
        for pattern in ConfigScanner.walkExcludePatterns {
            let predicate = NSPredicate(format: "SELF LIKE %@", pattern)
            if predicate.evaluate(with: filename) {
                return false
            }
        }

        // Check syncable paths.
        for syncPath in ConfigScanner.syncPaths {
            if syncPath.hasSuffix("/") {
                let prefix = String(syncPath.dropLast())
                if relativePath.hasPrefix(prefix + "/") || relativePath == prefix {
                    return true
                }
            } else {
                if relativePath == syncPath {
                    return true
                }
            }
        }

        // settings.json is a special case.
        if relativePath == "settings.json" {
            return true
        }

        return false
    }

    // MARK: - FSEvents Callback

    /// The C function pointer callback for FSEventStreamCreate.
    /// Bridges into the actor by extracting the FileWatcherCallbackBridge from the context info.
    private static let fsEventCallback: FSEventStreamCallback = {
        (streamRef, clientCallbackInfo, numEvents, eventPaths, eventFlags, eventIds) in

        guard let info = clientCallbackInfo else { return }
        let bridge = Unmanaged<FileWatcherCallbackBridge>.fromOpaque(info).takeUnretainedValue()

        // eventPaths is a raw C array of C strings (char**) when using kFSEventStreamCreateFlagFileEvents.
        // Must be cast properly — NOT via NSArray which causes EXC_BAD_ACCESS.
        let pathsPtr = unsafeBitCast(eventPaths, to: UnsafePointer<UnsafePointer<CChar>>.self)

        // Filter out directory-level events by checking flags. We only care about file-level events.
        var filePaths: [String] = []
        for i in 0..<numEvents {
            let flags = eventFlags[i]
            // Skip events that are purely directory modifications (not file-level).
            let isItemFile = (flags & UInt32(kFSEventStreamEventFlagItemIsFile)) != 0
            if isItemFile {
                filePaths.append(String(cString: pathsPtr[i]))
            }
        }

        guard !filePaths.isEmpty else { return }

        // Dispatch into the actor.
        let watcher = bridge.watcher
        Task {
            await watcher.handleFSEvents(paths: filePaths)
        }
    }
}

// MARK: - Callback Bridge

/// A class that holds a reference to the FileWatcher actor, used to bridge
/// from the C FSEventStream callback into Swift actor isolation.
/// Must be a class because it is passed through Unmanaged as a pointer.
private final class FileWatcherCallbackBridge: @unchecked Sendable {
    let watcher: FileWatcher

    init(watcher: FileWatcher) {
        self.watcher = watcher
    }
}
