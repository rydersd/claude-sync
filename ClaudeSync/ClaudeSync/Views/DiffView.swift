// DiffView.swift
// ClaudeSync
//
// Shows a visual file diff between local and remote versions of a config file.
// Displays the relative path, both hashes, and a line-by-line diff
// when both versions are available locally.

import SwiftUI

struct DiffView: View {
    /// The file diff to display.
    let diff: FileDiff

    /// Local file content (if available).
    let localContent: String?

    /// Remote file content (if available).
    let remoteContent: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // File header.
            fileHeader

            Divider()

            // Diff content.
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    switch diff.status {
                    case .localOnly:
                        localOnlyView
                    case .remoteOnly:
                        remoteOnlyView
                    case .modified:
                        modifiedView
                    case .identical:
                        identicalView
                    }
                }
                .padding(12)
            }
        }
        .frame(minWidth: 500, minHeight: 300)
    }

    // MARK: - File Header

    private var fileHeader: some View {
        HStack {
            Image(systemName: diff.status.iconName)
                .foregroundStyle(diff.status.color)

            Text(diff.relativePath)
                .font(.headline)
                .fontDesign(.monospaced)

            Spacer()

            Text(diff.status.displayName)
                .font(.caption)
                .padding(.horizontal, 8)
                .padding(.vertical, 3)
                .background(diff.status.color.opacity(0.1), in: Capsule())
        }
        .padding(12)
    }

    // MARK: - Local Only View

    private var localOnlyView: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("File exists only on this machine", systemImage: "info.circle")
                .font(.subheadline)
                .foregroundStyle(.secondary)

            if let content = localContent {
                DiffContentBlock(
                    title: "Local Content",
                    content: content,
                    lineColor: .green.opacity(0.15),
                    prefix: "+"
                )
            }
        }
    }

    // MARK: - Remote Only View

    private var remoteOnlyView: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("File exists only on the remote machine", systemImage: "info.circle")
                .font(.subheadline)
                .foregroundStyle(.secondary)

            if let content = remoteContent {
                DiffContentBlock(
                    title: "Remote Content",
                    content: content,
                    lineColor: .blue.opacity(0.15),
                    prefix: ">"
                )
            }
        }
    }

    // MARK: - Modified View

    private var modifiedView: some View {
        VStack(alignment: .leading, spacing: 8) {
            // Hash comparison.
            HStack(spacing: 16) {
                if let localHash = diff.localHash {
                    VStack(alignment: .leading) {
                        Text("Local Hash")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                        Text(localHash.prefix(16) + "...")
                            .font(.caption)
                            .fontDesign(.monospaced)
                    }
                }

                if let remoteHash = diff.remoteHash {
                    VStack(alignment: .leading) {
                        Text("Remote Hash")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                        Text(remoteHash.prefix(16) + "...")
                            .font(.caption)
                            .fontDesign(.monospaced)
                    }
                }
            }

            Divider()

            // Unified diff output.
            if let localText = localContent, let remoteText = remoteContent {
                unifiedDiff(local: localText, remote: remoteText)
            } else {
                Text("File content not available for diff display")
                    .font(.caption)
                    .foregroundStyle(.tertiary)
            }
        }
    }

    // MARK: - Identical View

    private var identicalView: some View {
        VStack(spacing: 8) {
            Image(systemName: "checkmark.circle.fill")
                .font(.title)
                .foregroundStyle(.green)
            Text("Files are identical")
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 24)
    }

    // MARK: - Unified Diff

    @ViewBuilder
    private func unifiedDiff(local: String, remote: String) -> some View {
        let localLines = local.components(separatedBy: "\n")
        let remoteLines = remote.components(separatedBy: "\n")
        let diffResult = computeSimpleDiff(old: localLines, new: remoteLines)

        VStack(alignment: .leading, spacing: 0) {
            ForEach(Array(diffResult.enumerated()), id: \.offset) { _, line in
                HStack(spacing: 0) {
                    Text(line.prefix)
                        .font(.caption)
                        .fontDesign(.monospaced)
                        .foregroundStyle(line.color)
                        .frame(width: 16, alignment: .center)

                    Text(line.text)
                        .font(.caption)
                        .fontDesign(.monospaced)
                        .lineLimit(1)
                        .truncationMode(.tail)
                }
                .padding(.vertical, 1)
                .padding(.horizontal, 4)
                .background(line.backgroundColor)
            }
        }
    }

    /// Simple line-by-line diff computation.
    /// Uses a basic LCS-inspired approach to identify added, removed, and context lines.
    private func computeSimpleDiff(old: [String], new: [String]) -> [DiffLine] {
        var result: [DiffLine] = []

        // Use a simple approach: find common prefix, common suffix, then mark
        // the middle section as changed. For a more sophisticated diff,
        // we would implement Myers' algorithm, but this provides good enough
        // results for config files which are typically small.

        let maxLines = max(old.count, new.count)
        var oldIndex = 0
        var newIndex = 0

        // Find common prefix.
        while oldIndex < old.count && newIndex < new.count && old[oldIndex] == new[newIndex] {
            result.append(DiffLine(prefix: " ", text: old[oldIndex], type: .context))
            oldIndex += 1
            newIndex += 1
        }

        // Collect remaining old lines as removals.
        var removals: [DiffLine] = []
        while oldIndex < old.count {
            removals.append(DiffLine(prefix: "-", text: old[oldIndex], type: .removal))
            oldIndex += 1
        }

        // Collect remaining new lines as additions.
        var additions: [DiffLine] = []
        while newIndex < new.count {
            additions.append(DiffLine(prefix: "+", text: new[newIndex], type: .addition))
            newIndex += 1
        }

        // Check for common suffix between removals and additions.
        var commonSuffix: [DiffLine] = []
        while !removals.isEmpty && !additions.isEmpty {
            let lastRemoval = removals.last!
            let lastAddition = additions.last!
            if lastRemoval.text == lastAddition.text {
                commonSuffix.insert(
                    DiffLine(prefix: " ", text: lastRemoval.text, type: .context),
                    at: 0
                )
                removals.removeLast()
                additions.removeLast()
            } else {
                break
            }
        }

        // Limit output for very large diffs.
        let effectiveRemovals = Array(removals.prefix(500))
        let effectiveAdditions = Array(additions.prefix(500))

        result.append(contentsOf: effectiveRemovals)
        result.append(contentsOf: effectiveAdditions)
        result.append(contentsOf: commonSuffix)

        if removals.count > 500 || additions.count > 500 {
            result.append(DiffLine(
                prefix: " ",
                text: "... (\(maxLines - result.count) more lines truncated)",
                type: .context
            ))
        }

        return result
    }
}

// MARK: - Supporting Types

/// A single line in a diff output.
struct DiffLine {
    let prefix: String
    let text: String
    let type: DiffLineType

    var color: Color {
        switch type {
        case .addition: return .green
        case .removal: return .red
        case .context: return .primary
        }
    }

    var backgroundColor: Color {
        switch type {
        case .addition: return .green.opacity(0.08)
        case .removal: return .red.opacity(0.08)
        case .context: return .clear
        }
    }
}

enum DiffLineType {
    case addition
    case removal
    case context
}

/// A block of content with a colored background, used for single-side diffs.
struct DiffContentBlock: View {
    let title: String
    let content: String
    let lineColor: Color
    let prefix: String

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.caption)
                .fontWeight(.medium)
                .foregroundStyle(.secondary)

            ScrollView(.horizontal, showsIndicators: false) {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(Array(content.components(separatedBy: "\n").prefix(200).enumerated()), id: \.offset) { index, line in
                        HStack(spacing: 4) {
                            Text("\(index + 1)")
                                .font(.caption2)
                                .fontDesign(.monospaced)
                                .foregroundStyle(.tertiary)
                                .frame(width: 30, alignment: .trailing)

                            Text(prefix)
                                .font(.caption)
                                .fontDesign(.monospaced)
                                .foregroundStyle(.secondary)

                            Text(line)
                                .font(.caption)
                                .fontDesign(.monospaced)
                        }
                        .padding(.vertical, 1)
                        .padding(.horizontal, 4)
                        .background(lineColor)
                    }
                }
            }
            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 4))
        }
    }
}
