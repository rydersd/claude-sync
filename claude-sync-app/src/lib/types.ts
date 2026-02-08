// ==========================================================================
// Type definitions shared between frontend and Rust backend (via IPC)
// These types mirror the Rust structs in src-tauri/src/protocol.rs
// ==========================================================================

/** Represents a discovered peer on the local network */
export interface Peer {
  /** Unique device identifier (UUID v4) */
  device_id: string;
  /** Human-readable device name (hostname) */
  name: string;
  /** IP address and port of the peer's TCP sync server */
  address: string;
  /** Operating system platform: "macos", "linux", "windows" */
  platform: string;
  /** Number of config files the peer has available */
  file_count: number;
  /** Combined SHA-256 fingerprint of all config files */
  fingerprint: string;
  /** Protocol version supported by the peer */
  protocol_version: number;
}

/** A single file entry in a config manifest */
export interface FileEntry {
  /** Path relative to ~/.claude/ (e.g., "rules/git-commits.md") */
  path: string;
  /** SHA-256 hash of the file content */
  sha256: string;
  /** File size in bytes */
  size: number;
  /** Whether the file has the executable bit set */
  executable: boolean;
}

/** Single file difference between local and remote */
export interface FileDiff {
  /** Relative path of the file */
  path: string;
  /** Type of change */
  change_type: 'added' | 'modified' | 'deleted';
  /** Hash on the local side (if exists) */
  local_hash: string | null;
  /** Hash on the remote side (if exists) */
  remote_hash: string | null;
  /** File size on local side */
  local_size: number | null;
  /** File size on remote side */
  remote_size: number | null;
}

/** Result of comparing local config tree with a remote peer */
export interface DiffResult {
  /** Files that exist on remote but not locally */
  added: FileDiff[];
  /** Files that differ between local and remote */
  modified: FileDiff[];
  /** Files that exist locally but not on remote */
  deleted: FileDiff[];
  /** Total number of differences */
  total_changes: number;
}

/** Result of a sync operation */
export interface SyncResult {
  /** Whether the sync completed successfully */
  success: boolean;
  /** Number of files transferred */
  files_transferred: number;
  /** Direction of the sync */
  direction: 'push' | 'pull';
  /** Error message if sync failed */
  error: string | null;
}

/** Information about the local device */
export interface DeviceInfo {
  /** This device's unique ID */
  device_id: string;
  /** This device's name */
  name: string;
  /** OS platform */
  platform: string;
  /** Number of local config files */
  file_count: number;
  /** Fingerprint of local configs */
  fingerprint: string;
}

/** Config tree with file entries and metadata */
export interface ConfigTree {
  /** All file entries in the config directory */
  files: FileEntry[];
  /** Combined fingerprint hash */
  fingerprint: string;
  /** Total number of files */
  file_count: number;
}
