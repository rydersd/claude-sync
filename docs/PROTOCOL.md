# Claude Sync Network Protocol Specification

**Version**: 1
**Status**: Draft
**Last Updated**: 2026-02-07
**Scope**: Peer-to-peer LAN sync for Claude Code configurations

---

## Table of Contents

1. [Overview](#1-overview)
2. [Service Discovery](#2-service-discovery)
3. [Connection Protocol](#3-connection-protocol)
4. [Message Types](#4-message-types)
5. [Sync Flow](#5-sync-flow)
6. [Settings.json Handling](#6-settingsjson-handling)
7. [Security Considerations](#7-security-considerations)
8. [Compatibility](#8-compatibility)
9. [Appendix: Quick Reference](#9-appendix-quick-reference)

---

## 1. Overview

Claude Sync is a peer-to-peer LAN protocol that enables Claude Code configuration synchronization between devices. Both macOS (SwiftUI) and Windows (Tauri/Rust) native apps implement this protocol to discover each other on the local network and synchronize configuration files with user-initiated push/pull operations.

**Design principles:**

- **Peer-to-peer**: No central server. Any device can initiate sync with any other discovered device.
- **User-initiated**: Discovery is automatic; sync requires explicit user action (push or pull).
- **MVP simplicity**: Trust the LAN, no TLS, no authentication. Security is scoped to future versions.
- **Cross-platform**: Protocol is platform-agnostic. macOS and Windows apps are interoperable.

---

## 2. Service Discovery

### 2.1 mDNS/DNS-SD Advertisement

Each running Claude Sync app advertises itself via Bonjour (mDNS/DNS-SD).

| Field | Value |
|---|---|
| Service type | `_claude-sync._tcp` |
| Domain | `local.` |
| Port | Dynamic (OS-assigned, advertised via mDNS) |

The fully qualified service type for DNS-SD browsing is:

```
_claude-sync._tcp.local.
```

### 2.2 TXT Records

The service advertisement MUST include the following TXT record key-value pairs:

| Key | Format | Description | Example |
|---|---|---|---|
| `v` | Integer string | Protocol version | `v=1` |
| `id` | UUID v4 string | Stable device identifier, persisted locally across app launches | `id=a1b2c3d4-e5f6-7890-abcd-ef1234567890` |
| `name` | UTF-8 string | Human-readable machine name | `name=Ryders-MacBook-Pro` |
| `configs` | Integer string | Number of syncable config files currently tracked | `configs=12` |
| `fingerprint` | Hex string (16 chars) | First 16 characters of the SHA-256 hex digest computed over a sorted list of individual file SHA-256 hashes | `fingerprint=3a7f2b1c9d4e8f06` |
| `platform` | Enum string | Operating system: `macos`, `windows`, or `linux` | `platform=macos` |
| `app_version` | Semver string | Application version | `app_version=1.0.0` |

### 2.3 Fingerprint Computation

The fingerprint enables a quick "are we in sync?" check without establishing a TCP connection.

**Algorithm:**

1. For each syncable config file, compute its SHA-256 hex digest.
2. Create a list of strings in the format `<relative-path>:<sha256-hex>`.
3. Sort the list lexicographically (byte-order ascending).
4. Join the sorted list with newline characters (`\n`).
5. Compute the SHA-256 hex digest of the joined string.
6. Take the first 16 characters of that hex digest.

**Pseudocode:**

```
file_hashes = []
for file in syncable_files:
    hash = sha256_hex(file.contents)
    file_hashes.append(f"{file.relative_path}:{hash}")

file_hashes.sort()
joined = "\n".join(file_hashes)
fingerprint = sha256_hex(joined)[0:16]
```

### 2.4 Device ID Persistence

The device ID (`id` TXT record) MUST be:

- Generated once as a UUID v4 on first app launch.
- Persisted to local storage (not synced).
- Stable across app restarts, OS reboots, and app updates.
- Unique per device (not per user or per installation).

**Storage locations:**

| Platform | Path |
|---|---|
| macOS | `~/Library/Application Support/claude-sync/device-id` |
| Windows | `%APPDATA%\claude-sync\device-id` |

### 2.5 Browsing

Apps MUST continuously browse for `_claude-sync._tcp.local.` services while running. When a service is discovered or lost, the UI MUST update the peer list accordingly.

Apps MUST filter out their own advertisement (match on `id` TXT record) and not display themselves as a peer.

---

## 3. Connection Protocol

### 3.1 Transport

All sync communication uses TCP. The connecting peer (client) opens a TCP connection to the advertised host and port of the target peer (server).

### 3.2 Message Framing

Messages are length-prefixed JSON:

```
+--------------------+-----------------------------+
| Length (4 bytes)    | JSON Payload (N bytes)      |
| Big-endian uint32   | UTF-8 encoded               |
+--------------------+-----------------------------+
```

- **Length prefix**: 4 bytes, unsigned 32-bit integer, big-endian byte order. Specifies the byte length of the JSON payload that follows. Does NOT include the 4-byte length prefix itself.
- **JSON payload**: UTF-8 encoded JSON object. No trailing newline or null terminator.

**Example** (hex representation of a 27-byte JSON payload):

```
00 00 00 1B  7B 22 74 79 70 65 22 3A 22 68 65 6C ...
|  length  | |         JSON body                  |
```

### 3.3 Connection Lifecycle

1. Client opens TCP connection to server.
2. Client sends `CLIENT_HELLO`.
3. Server responds with `SERVER_HELLO`.
4. If fingerprints match, either side sends `SYNC_NOT_NEEDED` and both close the connection gracefully.
5. Otherwise, the connection remains open for manifest exchange and sync operations.
6. After sync completes (or on user cancellation), the initiator sends `SYNC_COMPLETE` and both sides close the connection.
7. Either side may send `ERROR` at any point, after which the connection SHOULD be closed.

### 3.4 Connection Limits

- A device MUST accept at most 1 concurrent sync connection (serialize sync operations).
- A device MAY maintain multiple browse/discovery connections.
- Idle connections (no messages for 30 seconds) SHOULD be closed by either side.

### 3.5 Maximum Message Size

The maximum allowed message size is **16,777,216 bytes (16 MB)**. This accommodates large skill files encoded in base64. Any message exceeding this limit MUST be rejected with an `ERROR` message (code: `message_too_large`).

---

## 4. Message Types

Every message is a JSON object with a required `type` field. Unknown `type` values MUST be ignored (forward compatibility).

### 4.1 Handshake Messages

#### `hello` (Client Hello / Server Hello)

Sent by both sides immediately after TCP connection is established. The client sends first; the server responds.

**Schema:**

```json
{
  "type": "hello",
  "device_id": "<uuid-v4>",
  "name": "<human-readable-device-name>",
  "protocol_version": 1,
  "fingerprint": "<16-char-hex>",
  "platform": "<macos|windows|linux>",
  "file_count": 12
}
```

**Field descriptions:**

| Field | Type | Required | Description |
|---|---|---|---|
| `type` | string | Yes | Always `"hello"` |
| `device_id` | string | Yes | UUID v4 device identifier (same as mDNS TXT `id`) |
| `name` | string | Yes | Human-readable device name |
| `protocol_version` | integer | Yes | Protocol version (currently `1`) |
| `fingerprint` | string | Yes | 16-char hex fingerprint (see Section 2.3) |
| `platform` | string | Yes | One of: `"macos"`, `"windows"`, `"linux"` |
| `file_count` | integer | Yes | Number of syncable config files |

**Example (client sends):**

```json
{
  "type": "hello",
  "device_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "name": "Ryders-MacBook-Pro",
  "protocol_version": 1,
  "fingerprint": "3a7f2b1c9d4e8f06",
  "platform": "macos",
  "file_count": 12
}
```

**Example (server responds):**

```json
{
  "type": "hello",
  "device_id": "f9e8d7c6-b5a4-3210-fedc-ba0987654321",
  "name": "Office-Desktop",
  "protocol_version": 1,
  "fingerprint": "7c1e4a9b3d2f0856",
  "platform": "windows",
  "file_count": 8
}
```

**Protocol version mismatch handling:**

If the received `protocol_version` does not match the local version, the receiver MUST send an `ERROR` message with code `version_mismatch` and close the connection.

#### `sync_not_needed`

Sent by either side after handshake when fingerprints match, indicating configs are already in sync.

**Schema:**

```json
{
  "type": "sync_not_needed",
  "fingerprint": "<16-char-hex>"
}
```

**Example:**

```json
{
  "type": "sync_not_needed",
  "fingerprint": "3a7f2b1c9d4e8f06"
}
```

After sending or receiving this message, both sides close the TCP connection.

### 4.2 Manifest Messages

#### `manifest_request`

Sent by either peer to request the other peer's file manifest.

**Schema:**

```json
{
  "type": "manifest_request"
}
```

#### `manifest`

Response to a `manifest_request`. Contains the complete list of syncable files with metadata.

**Schema:**

```json
{
  "type": "manifest",
  "files": [
    {
      "path": "<relative-posix-path>",
      "sha256": "<64-char-hex>",
      "size": 1234,
      "mtime_epoch": 1707350400
    }
  ]
}
```

**Field descriptions:**

| Field | Type | Required | Description |
|---|---|---|---|
| `type` | string | Yes | Always `"manifest"` |
| `files` | array | Yes | Array of file metadata objects |
| `files[].path` | string | Yes | Relative path using POSIX separators (`/`), rooted at the Claude config directory |
| `files[].sha256` | string | Yes | Full SHA-256 hex digest of file contents (64 characters) |
| `files[].size` | integer | Yes | File size in bytes |
| `files[].mtime_epoch` | integer | Yes | Last modification time as Unix epoch seconds (UTC) |

**Path normalization:**

All paths MUST use POSIX forward-slash separators (`/`) regardless of the sending platform. Windows implementations MUST convert backslashes to forward slashes before sending and convert back when writing to disk.

**Example:**

```json
{
  "type": "manifest",
  "files": [
    {
      "path": "CLAUDE.md",
      "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      "size": 4521,
      "mtime_epoch": 1707350400
    },
    {
      "path": "rules/git-commits.md",
      "sha256": "a7ffc6f8bf1ed76651c14756a061d662f580ff4de43b49fa82d80a4b80f8434a",
      "size": 892,
      "mtime_epoch": 1707264000
    },
    {
      "path": "settings.json",
      "sha256": "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
      "size": 2048,
      "mtime_epoch": 1707350400
    },
    {
      "path": "skills/commit/SKILL.md",
      "sha256": "d7a8fbb307d7809469ca9abcb0082e4f8d5651e46d3cdb762d02d0bf37c9e592",
      "size": 1567,
      "mtime_epoch": 1707177600
    }
  ]
}
```

### 4.3 Sync Operation Messages

#### `sync_request`

Sent by the initiating peer to begin a sync operation.

**Schema:**

```json
{
  "type": "sync_request",
  "direction": "<push|pull>",
  "files": ["<path1>", "<path2>"]
}
```

**Field descriptions:**

| Field | Type | Required | Description |
|---|---|---|---|
| `type` | string | Yes | Always `"sync_request"` |
| `direction` | string | Yes | `"push"` (sender has files to give) or `"pull"` (sender wants files from receiver) |
| `files` | array | Yes | List of relative POSIX file paths to sync |

**Direction semantics:**

- `"push"`: The sender of this message will transfer files TO the receiver. The sender follows up with `file` messages.
- `"pull"`: The sender of this message wants files FROM the receiver. The receiver follows up with `file` messages.

**Example (push):**

```json
{
  "type": "sync_request",
  "direction": "push",
  "files": [
    "CLAUDE.md",
    "rules/git-commits.md",
    "settings.json"
  ]
}
```

**Example (pull):**

```json
{
  "type": "sync_request",
  "direction": "pull",
  "files": [
    "skills/commit/SKILL.md",
    "rules/search-tools.md"
  ]
}
```

#### `sync_ack`

Response to a `sync_request`. Indicates whether the receiver accepts the sync operation.

**Schema:**

```json
{
  "type": "sync_ack",
  "accepted": true,
  "reason": "<optional-rejection-reason>"
}
```

**Field descriptions:**

| Field | Type | Required | Description |
|---|---|---|---|
| `type` | string | Yes | Always `"sync_ack"` |
| `accepted` | boolean | Yes | `true` if sync is accepted, `false` if rejected |
| `reason` | string | No | Human-readable rejection reason (only present when `accepted` is `false`) |

**Example (accepted):**

```json
{
  "type": "sync_ack",
  "accepted": true
}
```

**Example (rejected):**

```json
{
  "type": "sync_ack",
  "accepted": false,
  "reason": "Another sync operation is already in progress"
}
```

#### `file`

Transfers a single file. Sent by the appropriate peer based on the `direction` in the preceding `sync_request`.

**Schema:**

```json
{
  "type": "file",
  "path": "<relative-posix-path>",
  "content_base64": "<base64-encoded-content>",
  "sha256": "<64-char-hex>",
  "size": 1234,
  "executable": false
}
```

**Field descriptions:**

| Field | Type | Required | Description |
|---|---|---|---|
| `type` | string | Yes | Always `"file"` |
| `path` | string | Yes | Relative path using POSIX separators |
| `content_base64` | string | Yes | File contents encoded as standard base64 (RFC 4648, no line breaks) |
| `sha256` | string | Yes | SHA-256 hex digest of the raw (pre-encoding) file contents |
| `size` | integer | Yes | Size of the raw (pre-encoding) file contents in bytes |
| `executable` | boolean | Yes | Whether the file should have the executable permission bit set |

**Example:**

```json
{
  "type": "file",
  "path": "rules/git-commits.md",
  "content_base64": "IyBHaXQgQ29tbWl0IFJ1bGVzCgpXaGVuIHRoZSB1c2VyIGFza3MgdG8gY29tbWl0Li4u",
  "sha256": "a7ffc6f8bf1ed76651c14756a061d662f580ff4de43b49fa82d80a4b80f8434a",
  "size": 892,
  "executable": false
}
```

**Integrity verification:**

The receiver MUST:
1. Decode the base64 content.
2. Verify that the decoded byte length matches `size`.
3. Compute the SHA-256 of the decoded bytes and verify it matches `sha256`.
4. If either check fails, respond with a `file_ack` where `success` is `false` and error is `"checksum_mismatch"` or `"size_mismatch"`.

#### `file_ack`

Acknowledgment for each received file.

**Schema:**

```json
{
  "type": "file_ack",
  "path": "<relative-posix-path>",
  "success": true,
  "error": "<optional-error-string>"
}
```

**Field descriptions:**

| Field | Type | Required | Description |
|---|---|---|---|
| `type` | string | Yes | Always `"file_ack"` |
| `path` | string | Yes | Path of the file being acknowledged |
| `success` | boolean | Yes | `true` if file was received and written successfully |
| `error` | string | No | Error description (only present when `success` is `false`) |

**Example (success):**

```json
{
  "type": "file_ack",
  "path": "rules/git-commits.md",
  "success": true
}
```

**Example (failure):**

```json
{
  "type": "file_ack",
  "path": "rules/git-commits.md",
  "success": false,
  "error": "checksum_mismatch"
}
```

#### `sync_complete`

Sent by the sync initiator after all files have been transferred and acknowledged.

**Schema:**

```json
{
  "type": "sync_complete",
  "files_transferred": 3,
  "direction": "<push|pull>"
}
```

**Field descriptions:**

| Field | Type | Required | Description |
|---|---|---|---|
| `type` | string | Yes | Always `"sync_complete"` |
| `files_transferred` | integer | Yes | Number of files successfully transferred |
| `direction` | string | Yes | The direction that was used: `"push"` or `"pull"` |

**Example:**

```json
{
  "type": "sync_complete",
  "files_transferred": 3,
  "direction": "push"
}
```

After this message, both sides close the TCP connection.

### 4.4 Status Messages

#### `status_request`

Request the current status of a peer. Can be sent at any point after handshake.

**Schema:**

```json
{
  "type": "status_request"
}
```

#### `status`

Response to a `status_request`.

**Schema:**

```json
{
  "type": "status",
  "device_id": "<uuid-v4>",
  "name": "<device-name>",
  "uptime_seconds": 3600,
  "last_sync_timestamp": 1707350400,
  "file_count": 12,
  "fingerprint": "<16-char-hex>"
}
```

**Field descriptions:**

| Field | Type | Required | Description |
|---|---|---|---|
| `type` | string | Yes | Always `"status"` |
| `device_id` | string | Yes | Device UUID |
| `name` | string | Yes | Human-readable device name |
| `uptime_seconds` | integer | Yes | Seconds since the app started |
| `last_sync_timestamp` | integer | Yes | Unix epoch seconds of last completed sync, or `0` if never synced |
| `file_count` | integer | Yes | Current number of syncable config files |
| `fingerprint` | string | Yes | Current 16-char hex fingerprint |

**Example:**

```json
{
  "type": "status",
  "device_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "name": "Ryders-MacBook-Pro",
  "uptime_seconds": 3600,
  "last_sync_timestamp": 1707350400,
  "file_count": 12,
  "fingerprint": "3a7f2b1c9d4e8f06"
}
```

### 4.5 Error Messages

#### `error`

Sent by either peer to indicate a protocol-level error. After sending or receiving an `error`, the connection SHOULD be closed.

**Schema:**

```json
{
  "type": "error",
  "code": "<error-code>",
  "message": "<human-readable-description>"
}
```

**Defined error codes:**

| Code | Description | When |
|---|---|---|
| `version_mismatch` | Protocol versions are incompatible | During handshake when `protocol_version` differs |
| `transfer_failed` | File transfer could not be completed | During file transfer on I/O or network error |
| `file_not_found` | Requested file does not exist on sender | When a pull requests a file the peer does not have |
| `checksum_mismatch` | Received file SHA-256 does not match declared hash | After decoding a `file` message |
| `permission_denied` | Operation not allowed | When sync is rejected due to policy |
| `message_too_large` | Message exceeds 16 MB limit | When a received length prefix exceeds 16,777,216 |
| `invalid_message` | Message JSON is malformed or missing required fields | When parsing fails |
| `sync_in_progress` | Another sync is already running | When a second `sync_request` arrives during active sync |

**Example:**

```json
{
  "type": "error",
  "code": "version_mismatch",
  "message": "This device speaks protocol version 1, but received version 2. Please update the app."
}
```

---

## 5. Sync Flow

### 5.1 Discovery

```
  Device A (macOS)                        Device B (Windows)
  ================                        ==================

  1. Start app
  2. Generate/load device ID
  3. Scan syncable configs
  4. Compute fingerprint
  5. Advertise via mDNS:                  5. Advertise via mDNS:
     _claude-sync._tcp.local.                _claude-sync._tcp.local.
     port=49152                              port=51234
     TXT: v=1, id=..., ...                  TXT: v=1, id=..., ...

  6. Browse _claude-sync._tcp.local.      6. Browse _claude-sync._tcp.local.
  7. Discover Device B                    7. Discover Device A
  8. Show in peer list UI                 8. Show in peer list UI
```

Both devices continuously browse for the service type. When a new service appears, it is added to the UI peer list. When a service disappears (app closed, network change), it is removed.

### 5.2 Quick Sync Check (Fingerprint Comparison)

Before establishing a TCP connection, the app compares the fingerprint from the discovered peer's TXT record against its own fingerprint.

- **Fingerprints match**: Show peer status as "In Sync" in the UI. No connection needed.
- **Fingerprints differ**: Show peer status as "Out of Sync" in the UI. User can initiate a full compare.

This avoids unnecessary TCP connections for devices that are already synchronized.

### 5.3 Full Compare (Manifest Exchange)

When the user clicks on an out-of-sync peer to see details, the app connects and performs a full comparison.

```
  Device A (initiator)                    Device B (responder)
  ====================                    ====================

  1. TCP connect to Device B
  2. Send hello --->                      3. Receive hello
                                          4. Send hello <---
  5. Receive hello

     [If fingerprints match at this point: send sync_not_needed, disconnect]

  6. Send manifest_request --->           7. Receive manifest_request
                                          8. Send manifest <---
  9. Receive manifest
  10. Compute diff locally:
      - Files only on A (push candidates)
      - Files only on B (pull candidates)
      - Files on both but different hash
        (conflict: show both mtimes, let user choose)
  11. Display diff in UI
```

**Diff computation rules:**

| Condition | Category | UI Label |
|---|---|---|
| File exists on local only | Push candidate | "Only on this device" |
| File exists on remote only | Pull candidate | "Only on remote device" |
| File exists on both, hashes differ, local mtime > remote mtime | Push candidate (newer local) | "Modified locally (newer)" |
| File exists on both, hashes differ, remote mtime > local mtime | Pull candidate (newer remote) | "Modified remotely (newer)" |
| File exists on both, hashes differ, mtimes equal | Conflict | "Conflict (same timestamp)" |
| File exists on both, hashes match | In sync | (not shown or grayed out) |

### 5.4 Push Flow

User selects files to push and clicks "Push".

```
  Device A (pusher)                       Device B (receiver)
  =================                       ====================

  1. Send sync_request
     direction: "push"
     files: ["CLAUDE.md",
             "rules/git-commits.md"] -->

                                          2. Receive sync_request
                                          3. Validate request
                                          4. Send sync_ack
                                             accepted: true <---

  5. Receive sync_ack
  6. For each file:
     a. Read file contents
     b. Base64 encode
     c. Compute SHA-256
     d. Send file message -->
                                          e. Receive file message
                                          f. Decode base64
                                          g. Verify size + SHA-256
                                          h. Write to disk (atomic)
                                          i. Send file_ack <---
     j. Receive file_ack
     k. If success: continue
        If failure: log error, continue with next file

  7. Send sync_complete
     files_transferred: 2
     direction: "push" -->
                                          8. Receive sync_complete
                                          9. Update local fingerprint
                                          10. Update mDNS TXT records

  11. Update local state
  12. Close TCP connection                12. Close TCP connection
```

### 5.5 Pull Flow

User selects files to pull and clicks "Pull".

```
  Device A (puller)                       Device B (sender)
  =================                       =================

  1. Send sync_request
     direction: "pull"
     files: ["skills/commit/SKILL.md",
             "rules/search-tools.md"] -->

                                          2. Receive sync_request
                                          3. Validate: do requested files exist?
                                          4. Send sync_ack
                                             accepted: true <---

  5. Receive sync_ack
                                          6. For each requested file:
                                             a. Read file contents
                                             b. Base64 encode
                                             c. Compute SHA-256
                                             d. Send file message <---
  7. For each received file:
     a. Receive file message
     b. Decode base64
     c. Verify size + SHA-256
     d. Write to disk (atomic)
     e. Send file_ack -->
                                             f. Receive file_ack

  8. After all files received:
     Send sync_complete
     files_transferred: 2
     direction: "pull" -->
                                          9. Receive sync_complete

  10. Update local fingerprint
  11. Update mDNS TXT records
  12. Close TCP connection                12. Close TCP connection
```

### 5.6 Atomic File Writes

When writing received files to disk, implementations MUST use atomic write operations to prevent corruption:

1. Write contents to a temporary file in the same directory (e.g., `<filename>.tmp.<random>`).
2. Verify the written content (re-read and hash if paranoid).
3. Rename the temporary file to the final path (atomic on both macOS and Windows).
4. Set executable bit if `executable` is `true` (macOS/Linux only; ignored on Windows).

### 5.7 mDNS Re-advertisement

After any sync operation completes, the device MUST:

1. Recompute the fingerprint.
2. Update the `configs` count.
3. Re-advertise the mDNS TXT records with the new values.

This ensures other peers on the network see the updated sync state without needing to connect.

---

## 6. Settings.json Handling

### 6.1 Portable vs. Machine-Specific Keys

Claude Code's `settings.json` contains both portable configuration and machine-specific paths. Only portable keys are synced.

**Portable keys (synced):**

```json
{
  "hooks": { ... },
  "statusLine": { ... },
  "attribution": { ... },
  "permissions": { ... },
  "theme": { ... }
}
```

**Machine-specific keys (never synced):**

```json
{
  "mcpServers": { ... },
  "projects": { ... },
  "env": { ... }
}
```

### 6.2 Sender-Side Stripping

Before transferring `settings.json`, the sender MUST:

1. Read the full `settings.json`.
2. Parse it as JSON.
3. Remove all machine-specific keys (keep only portable keys).
4. Serialize the filtered object back to JSON.
5. Transfer the filtered JSON as the file content.

This ensures no machine-specific paths or secrets leak to other devices.

### 6.3 Receiver-Side Deep Merge

When receiving `settings.json`, the receiver MUST NOT overwrite its local file. Instead:

1. Read the local `settings.json`.
2. Parse both local and received JSON.
3. For each portable key in the received JSON:
   - Deep-merge into the local JSON (received values override local values at the leaf level).
4. Preserve all local machine-specific keys untouched.
5. Write the merged result back to disk (atomic write).

**Deep merge example:**

Local:
```json
{
  "hooks": {
    "PreToolUse": [{"type": "command", "command": "local-hook.sh"}],
    "PostToolUse": [{"type": "command", "command": "local-post.sh"}]
  },
  "mcpServers": { "local-server": { "port": 3000 } }
}
```

Received (after sender stripping):
```json
{
  "hooks": {
    "PreToolUse": [{"type": "command", "command": "synced-hook.sh"}],
    "SessionStart": [{"type": "command", "command": "synced-start.sh"}]
  }
}
```

Result after merge:
```json
{
  "hooks": {
    "PreToolUse": [{"type": "command", "command": "synced-hook.sh"}],
    "PostToolUse": [{"type": "command", "command": "local-post.sh"}],
    "SessionStart": [{"type": "command", "command": "synced-start.sh"}]
  },
  "mcpServers": { "local-server": { "port": 3000 } }
}
```

**Note**: Array values within portable keys are replaced entirely (not appended). In the example above, `PreToolUse` is replaced with the received value, not merged element-by-element.

### 6.4 Key Classification

Implementations MUST maintain an explicit allowlist of portable keys. Any key not in the allowlist is considered machine-specific and is never synced. This is safer than a blocklist approach because new keys default to not being synced.

**Portable key allowlist (protocol version 1):**

- `hooks`
- `statusLine`
- `attribution`
- `permissions`
- `theme`

This list may be extended in future protocol versions.

---

## 7. Security Considerations

### 7.1 MVP Threat Model

The MVP operates under the assumption that the local network is trusted.

**What is protected:**
- File integrity via SHA-256 checksums (detects corruption, not tampering).
- Machine-specific settings are stripped before transfer (no accidental secret leakage via settings.json).

**What is NOT protected (MVP):**
- No authentication: any device on the LAN can discover and connect to peers.
- No encryption: all data is transferred in plaintext over TCP.
- No authorization: any connecting device can request any file in the syncable set.
- No tamper detection: a MITM could modify files in transit.

### 7.2 MVP Recommendations

- Only run Claude Sync on trusted networks (home, office with WPA2/WPA3).
- Do not use on public Wi-Fi, shared networks, or untrusted environments.
- The app SHOULD display a warning on first launch about LAN trust assumptions.
- The app SHOULD allow the user to disable the sync service without quitting.

### 7.3 Future Security (Post-MVP)

The following security enhancements are planned for future protocol versions:

**Device Pairing (v2):**
1. Each device generates a self-signed TLS certificate on first launch.
2. User initiates pairing from Device A to Device B.
3. Device B displays a 6-digit confirmation code.
4. User enters the code on Device A.
5. Both devices exchange and persist each other's certificate fingerprint.
6. All subsequent connections use mutual TLS with pinned certificates.
7. Unpaired devices are rejected at the TLS handshake level.

**Encrypted Transport (v2):**
- All connections upgraded to TLS 1.3.
- Self-signed certificates, pinned during pairing.
- No reliance on external certificate authorities.

**Access Control (v2+):**
- Per-file sync permissions.
- Read-only vs. read-write peer relationships.
- Sync approval prompts for incoming push requests.

---

## 8. Compatibility

### 8.1 Protocol Version

This document describes **protocol version 1**.

### 8.2 Cross-Platform Requirements

Both macOS (SwiftUI) and Windows (Tauri/Rust) implementations MUST:

- Implement all message types defined in Section 4.
- Use big-endian byte order for the 4-byte length prefix.
- Use UTF-8 encoding for all JSON payloads.
- Use standard base64 (RFC 4648) for file content encoding, without line breaks.
- Use POSIX path separators (`/`) in all path fields.
- Implement the fingerprint algorithm exactly as specified in Section 2.3.
- Support the full sync flow as described in Section 5.

### 8.3 Endianness

The 4-byte message length prefix MUST be big-endian (network byte order).

Example: A message of 256 bytes has the length prefix `0x00 0x00 0x01 0x00`.

### 8.4 Encoding

- **JSON**: UTF-8, no BOM.
- **File contents**: Standard base64 (RFC 4648, alphabet `A-Za-z0-9+/`, padding `=`). No line breaks or whitespace within the base64 string.
- **Hashes**: Lowercase hexadecimal.
- **Paths**: POSIX forward-slash separators. No leading slash (paths are relative).

### 8.5 Maximum Message Size

16,777,216 bytes (16 MB). Both sender and receiver MUST enforce this limit.

Rationale: The largest syncable files are skill definitions and CLAUDE.md files. Even with base64 overhead (~33%), a 12 MB raw file fits within the 16 MB limit.

### 8.6 Minimum Implementation

A conforming implementation MUST support at minimum:

| Capability | Required |
|---|---|
| mDNS service advertisement | Yes |
| mDNS service browsing | Yes |
| TCP server (accept connections) | Yes |
| TCP client (initiate connections) | Yes |
| All message types in Section 4 | Yes |
| Fingerprint computation (Section 2.3) | Yes |
| Settings.json stripping (Section 6.2) | Yes |
| Settings.json deep merge (Section 6.3) | Yes |
| Atomic file writes (Section 5.6) | Yes |

---

## 9. Appendix: Quick Reference

### 9.1 Message Type Summary

| Message | Direction | Purpose |
|---|---|---|
| `hello` | Both | Handshake with device info and fingerprint |
| `sync_not_needed` | Either | Fingerprints match, no sync required |
| `manifest_request` | Either | Request peer's file list |
| `manifest` | Response | Full file list with hashes and metadata |
| `sync_request` | Initiator | Begin push or pull operation |
| `sync_ack` | Responder | Accept or reject sync request |
| `file` | Sender | Transfer a single file |
| `file_ack` | Receiver | Acknowledge file receipt |
| `sync_complete` | Initiator | All files transferred, close connection |
| `status_request` | Either | Request peer status |
| `status` | Response | Current device status |
| `error` | Either | Protocol error, connection should close |

### 9.2 Error Code Summary

| Code | Meaning |
|---|---|
| `version_mismatch` | Incompatible protocol versions |
| `transfer_failed` | File transfer I/O or network error |
| `file_not_found` | Requested file does not exist |
| `checksum_mismatch` | SHA-256 verification failed |
| `permission_denied` | Operation not allowed |
| `message_too_large` | Message exceeds 16 MB |
| `invalid_message` | Malformed or incomplete JSON |
| `sync_in_progress` | Another sync is already active |

### 9.3 TXT Record Summary

```
v=1
id=a1b2c3d4-e5f6-7890-abcd-ef1234567890
name=Ryders-MacBook-Pro
configs=12
fingerprint=3a7f2b1c9d4e8f06
platform=macos
app_version=1.0.0
```

### 9.4 Wire Format Example

Complete example of a `hello` message on the wire:

```
JSON payload (113 bytes):
{"type":"hello","device_id":"a1b2c3d4-e5f6-7890-abcd-ef1234567890","name":"MyMac","protocol_version":1,"fingerprint":"3a7f2b1c9d4e8f06","platform":"macos","file_count":5}

Wire bytes (hex):
00 00 00 71    <- Length prefix: 113 in big-endian uint32
7B 22 74 79... <- UTF-8 JSON payload
```

### 9.5 Syncable File Locations

| Platform | Claude Config Root |
|---|---|
| macOS | `~/.claude/` |
| Windows | `%USERPROFILE%\.claude\` |
| Linux | `~/.claude/` |

**Syncable paths (relative to config root):**

- `CLAUDE.md`
- `settings.json` (portable keys only, per Section 6)
- `rules/*.md`
- `skills/**/*`
- `hooks/**/*` (script files referenced by settings.json hooks)

**Not synced:**

- `.credentials`
- `projects/` (machine-specific project paths)
- `cache/` (ephemeral data)
- `statsig/` (telemetry)
- Any file starting with `.` (hidden files)
