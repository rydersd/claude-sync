// ConfigScanner.swift
// ClaudeSync
//
// Walks ~/.claude/ directory to discover syncable configuration files.
// Implements the same sync/exclude logic as claude-sync.py:
//   - Only files under SYNC_PATHS are included
//   - Files under EXCLUDE_PATHS are skipped
//   - Files matching WALK_EXCLUDE_PATTERNS are skipped
//   - settings.json is included (for partial sync via SettingsMerger)
//
// All scanning is async to avoid blocking the main thread.

import Foundation
import os

/// Scans the local ~/.claude/ directory for syncable configuration files,
/// computing SHA-256 hashes for each file. Uses the same include/exclude
/// logic as the Python claude-sync.py tool.
actor ConfigScanner {

    /// Logger for scan operations.
    private let logger = Logger(subsystem: "com.claudesync", category: "ConfigScanner")

    /// The base directory to scan (typically ~/.claude/).
    let baseDirectory: URL

    /// Paths that are eligible for sync (relative to baseDirectory).
    /// Entries ending with "/" are directory prefixes.
    static let syncPaths: [String] = [
        "CLAUDE.md",
        "agents/",
        "skills/",
        "rules/",
        "hooks/",
        "scripts/",
    ]

    /// Paths that are explicitly excluded from sync.
    static let excludePaths: [String] = [
        ".env",
        "mcp_config.json",
        "session-env/",
        "todos/",
        "projects/",
        "history.jsonl",
        "stats-cache.json",
        "telemetry/",
        "cache/",
        "state/",
        "plans/",
        "downloads/",
        "plugins/",
        "shell-snapshots/",
        "paste-cache/",
        "file-history/",
        "debug/",
        "statsig/",
    ]

    /// Filename patterns to exclude during directory traversal.
    static let walkExcludePatterns: [String] = [
        "node_modules",
        "__pycache__",
        ".pyc",
        ".DS_Store",
        "*.swp",
        "*.swo",
        "*~",
    ]

    // MARK: - Initialization

    /// Creates a scanner targeting the given base directory.
    /// - Parameter baseDirectory: The directory to scan. Defaults to ~/.claude/.
    init(baseDirectory: URL? = nil) {
        self.baseDirectory = baseDirectory ?? FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".claude")
    }

    // MARK: - Scanning

    /// Performs a full scan of the base directory and returns a dictionary
    /// of relative_path -> SHA-256 hash for all syncable files.
    /// - Returns: Dictionary mapping relative paths to their SHA-256 hashes.
    func scan() async -> [String: String] {
        var results: [String: String] = [:]

        guard FileManager.default.fileExists(atPath: baseDirectory.path) else {
            logger.warning("Base directory does not exist: \(self.baseDirectory.path)")
            return results
        }

        let fileManager = FileManager.default

        // Use FileManager's directory enumerator for recursive traversal.
        guard let enumerator = fileManager.enumerator(
            at: baseDirectory,
            includingPropertiesForKeys: [.isRegularFileKey, .isDirectoryKey, .fileSizeKey, .contentModificationDateKey],
            options: [.skipsHiddenFiles],
            errorHandler: { [logger] url, error in
                logger.error("Error enumerating \(url.path): \(error.localizedDescription)")
                return true // Continue enumeration despite errors.
            }
        ) else {
            logger.error("Failed to create directory enumerator for \(self.baseDirectory.path)")
            return results
        }

        for case let fileURL as URL in enumerator {
            let relativePath = fileURL.path.replacingOccurrences(of: baseDirectory.path + "/", with: "")

            // Skip excluded directories (prune the enumerator).
            if isExcludedDirectory(relativePath: relativePath) {
                enumerator.skipDescendants()
                continue
            }

            // Skip files matching walk exclude patterns.
            if matchesWalkExcludePattern(filename: fileURL.lastPathComponent) {
                continue
            }

            // Check if this path should be excluded.
            if isExcluded(relativePath: relativePath) {
                continue
            }

            // Check if this path is syncable.
            guard isSyncable(relativePath: relativePath) else {
                continue
            }

            // Only process regular files.
            do {
                let resourceValues = try fileURL.resourceValues(forKeys: [.isRegularFileKey])
                guard resourceValues.isRegularFile == true else {
                    continue
                }
            } catch {
                continue
            }

            // Compute the SHA-256 hash.
            do {
                let hash = try FileHasher.hashFile(at: fileURL)
                results[relativePath] = hash
            } catch {
                logger.error("Failed to hash \(relativePath): \(error.localizedDescription)")
            }
        }

        logger.info("Scan complete: \(results.count) syncable files found")
        return results
    }

    /// Scans and returns detailed ConfigFile objects (with size and modification date).
    func scanDetailed() async -> [ConfigFile] {
        var files: [ConfigFile] = []

        guard FileManager.default.fileExists(atPath: baseDirectory.path) else {
            return files
        }

        let fileManager = FileManager.default
        guard let enumerator = fileManager.enumerator(
            at: baseDirectory,
            includingPropertiesForKeys: [.isRegularFileKey, .fileSizeKey, .contentModificationDateKey],
            options: [.skipsHiddenFiles],
            errorHandler: nil
        ) else {
            return files
        }

        for case let fileURL as URL in enumerator {
            let relativePath = fileURL.path.replacingOccurrences(of: baseDirectory.path + "/", with: "")

            if isExcludedDirectory(relativePath: relativePath) {
                enumerator.skipDescendants()
                continue
            }

            if matchesWalkExcludePattern(filename: fileURL.lastPathComponent) {
                continue
            }

            if isExcluded(relativePath: relativePath) {
                continue
            }

            guard isSyncable(relativePath: relativePath) else {
                continue
            }

            do {
                let resourceValues = try fileURL.resourceValues(
                    forKeys: [.isRegularFileKey, .fileSizeKey, .contentModificationDateKey]
                )
                guard resourceValues.isRegularFile == true else { continue }

                let hash = try FileHasher.hashFile(at: fileURL)
                let size = UInt64(resourceValues.fileSize ?? 0)
                let modified = resourceValues.contentModificationDate ?? Date()

                files.append(ConfigFile(
                    relativePath: relativePath,
                    hash: hash,
                    size: size,
                    modifiedDate: modified
                ))
            } catch {
                logger.error("Failed to process \(relativePath): \(error.localizedDescription)")
            }
        }

        return files
    }

    // MARK: - Path Filtering

    /// Checks if a relative path falls under any syncable path prefix.
    private func isSyncable(relativePath: String) -> Bool {
        for syncPath in Self.syncPaths {
            if syncPath.hasSuffix("/") {
                // Directory prefix match.
                let prefix = String(syncPath.dropLast())
                if relativePath.hasPrefix(prefix + "/") || relativePath == prefix {
                    return true
                }
            } else {
                // Exact file match.
                if relativePath == syncPath {
                    return true
                }
            }
        }

        // settings.json is a special case (partial sync via SettingsMerger).
        if relativePath == "settings.json" {
            return true
        }

        return false
    }

    /// Checks if a relative path should be excluded from sync.
    private func isExcluded(relativePath: String) -> Bool {
        for excludePath in Self.excludePaths {
            if excludePath.hasSuffix("/") {
                let dirName = String(excludePath.dropLast())
                if relativePath == dirName || relativePath.hasPrefix(dirName + "/") {
                    return true
                }
            } else {
                if relativePath == excludePath {
                    return true
                }
            }
        }
        return false
    }

    /// Checks if a relative path represents an excluded directory
    /// (used to prune the enumerator for efficiency).
    private func isExcludedDirectory(relativePath: String) -> Bool {
        for excludePath in Self.excludePaths {
            if excludePath.hasSuffix("/") {
                let dirName = String(excludePath.dropLast())
                if relativePath == dirName {
                    return true
                }
            }
        }

        // Also check walk exclude patterns that are directory names.
        let lastComponent = (relativePath as NSString).lastPathComponent
        for pattern in Self.walkExcludePatterns {
            if !pattern.contains("*") && !pattern.contains(".") {
                // Simple name match (e.g. "node_modules", "__pycache__").
                if lastComponent == pattern {
                    return true
                }
            }
        }

        return false
    }

    /// Checks if a filename matches any walk exclude pattern.
    private func matchesWalkExcludePattern(filename: String) -> Bool {
        for pattern in Self.walkExcludePatterns {
            if fnmatch(pattern, filename) {
                return true
            }
        }
        return false
    }

    /// Simple fnmatch-style pattern matching supporting * and ? wildcards.
    private func fnmatch(_ pattern: String, _ string: String) -> Bool {
        // Use NSPredicate with LIKE for simple glob matching.
        // Convert shell glob to NSPredicate LIKE pattern:
        //   * -> * (same in LIKE)
        //   ? -> ? (same in LIKE)
        let predicate = NSPredicate(format: "SELF LIKE %@", pattern)
        return predicate.evaluate(with: string)
    }

    /// Reads the contents of a file at the given relative path.
    /// Used for file transfer operations.
    /// - Parameter relativePath: Path relative to the base directory.
    /// - Returns: The file contents as Data, or nil if the file cannot be read.
    func readFile(relativePath: String) -> Data? {
        let url = baseDirectory.appendingPathComponent(relativePath)
        return try? Data(contentsOf: url)
    }

    /// Writes data to a file at the given relative path, creating parent directories as needed.
    /// - Parameters:
    ///   - data: The file contents to write.
    ///   - relativePath: Path relative to the base directory.
    /// - Throws: If the file cannot be written.
    func writeFile(data: Data, relativePath: String) throws {
        let url = baseDirectory.appendingPathComponent(relativePath)
        let parentDir = url.deletingLastPathComponent()

        // Create parent directories if they do not exist.
        try FileManager.default.createDirectory(at: parentDir, withIntermediateDirectories: true)

        try data.write(to: url)

        // Set executable permission on scripts.
        if relativePath.hasSuffix(".sh") || relativePath.hasSuffix(".py") {
            var attributes = try FileManager.default.attributesOfItem(atPath: url.path)
            if let permissions = attributes[.posixPermissions] as? NSNumber {
                let newPermissions = permissions.uint16Value | 0o111
                attributes[.posixPermissions] = NSNumber(value: newPermissions)
                try FileManager.default.setAttributes(attributes, ofItemAtPath: url.path)
            }
        }
    }
}
