// ==========================================================================
// Diff viewer component - renders file differences in the sync panel
// ==========================================================================

import type { DiffResult, FileDiff } from '../lib/types';

/**
 * Render the list of file differences into the #diff-file-list container.
 * Groups files by change type (added, modified, deleted) and displays
 * them with color-coded indicators.
 */
export function renderDiffFiles(diff: DiffResult): void {
  const container = document.getElementById('diff-file-list');
  if (!container) return;

  // Clear existing content
  container.innerHTML = '';

  const fragment = document.createDocumentFragment();

  // Render each change type in order: added, modified, deleted
  for (const fileDiff of diff.added) {
    fragment.appendChild(createDiffFileRow(fileDiff, 'added'));
  }
  for (const fileDiff of diff.modified) {
    fragment.appendChild(createDiffFileRow(fileDiff, 'modified'));
  }
  for (const fileDiff of diff.deleted) {
    fragment.appendChild(createDiffFileRow(fileDiff, 'deleted'));
  }

  container.appendChild(fragment);
}

/**
 * Create a single diff file row element.
 * Shows a colored indicator dot, the file path, and the change type label.
 */
function createDiffFileRow(fileDiff: FileDiff, changeType: string): HTMLElement {
  const row = document.createElement('div');
  row.className = 'diff-file';
  row.title = buildTooltip(fileDiff, changeType);

  // Change type indicator dot
  const indicator = document.createElement('span');
  indicator.className = `indicator ${changeType}`;
  row.appendChild(indicator);

  // File path
  const pathSpan = document.createElement('span');
  pathSpan.className = 'path';
  pathSpan.textContent = fileDiff.path;
  row.appendChild(pathSpan);

  // Size info (if available)
  const sizeInfo = formatSizeChange(fileDiff, changeType);
  if (sizeInfo) {
    const sizeSpan = document.createElement('span');
    sizeSpan.className = 'change-type';
    sizeSpan.textContent = sizeInfo;
    row.appendChild(sizeSpan);
  }

  // Change type label
  const typeSpan = document.createElement('span');
  typeSpan.className = 'change-type';
  typeSpan.textContent = changeType;
  row.appendChild(typeSpan);

  return row;
}

/**
 * Build a tooltip string for a diff file row with hash and size details.
 */
function buildTooltip(fileDiff: FileDiff, changeType: string): string {
  const lines: string[] = [
    `Path: ${fileDiff.path}`,
    `Change: ${changeType}`,
  ];

  if (fileDiff.local_hash) {
    lines.push(`Local hash: ${fileDiff.local_hash.substring(0, 12)}...`);
  }
  if (fileDiff.remote_hash) {
    lines.push(`Remote hash: ${fileDiff.remote_hash.substring(0, 12)}...`);
  }
  if (fileDiff.local_size !== null) {
    lines.push(`Local size: ${formatBytes(fileDiff.local_size)}`);
  }
  if (fileDiff.remote_size !== null) {
    lines.push(`Remote size: ${formatBytes(fileDiff.remote_size)}`);
  }

  return lines.join('\n');
}

/**
 * Format the size change for display (e.g., "1.2 KB" or "+500 B").
 */
function formatSizeChange(fileDiff: FileDiff, changeType: string): string | null {
  if (changeType === 'added' && fileDiff.remote_size !== null) {
    return formatBytes(fileDiff.remote_size);
  }
  if (changeType === 'deleted' && fileDiff.local_size !== null) {
    return formatBytes(fileDiff.local_size);
  }
  if (changeType === 'modified' && fileDiff.local_size !== null && fileDiff.remote_size !== null) {
    const diff = fileDiff.remote_size - fileDiff.local_size;
    if (diff > 0) {
      return `+${formatBytes(diff)}`;
    } else if (diff < 0) {
      return `-${formatBytes(Math.abs(diff))}`;
    }
    return 'same size';
  }
  return null;
}

/**
 * Format a byte count into a human-readable string.
 */
function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  const value = bytes / Math.pow(1024, i);
  return `${value.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}
