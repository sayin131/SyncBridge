# SyncBridge Protocol v1

Transport: HTTPS (self-signed cert, pinned by fingerprint at pairing time) + one WebSocket
for live events. All endpoints except `/ping` and `/pair` require:

```
X-Device-Id: <device_id>
Authorization: Bearer <token>
```

## Threat model

**Defends against:**
- A stranger on the same (possibly shared/college) Wi-Fi reading or tampering with traffic
  (TLS) or impersonating the laptop (certificate pinning — the fingerprint is transferred
  out-of-band via QR, never over the network being defended).
- An unpaired device calling any sync endpoint (bearer token required).
- Brute-forcing the pairing code (secret expires after 120s or 3 attempts).
- Path traversal (`../../etc/passwd`) escaping the sync root.
- Silent data corruption in transit (SHA-256 verification before any file is finalized).

**Does not defend against:**
- A compromised laptop or phone (no defense against the endpoint itself being malicious).
- Physical access to a device with the Keystore/token already unlocked.
- Traffic analysis (file sizes/timing are still observable, even though content isn't).

## Pairing flow

1. Laptop generates a self-signed cert on first run and computes its SHA-256 fingerprint.
2. Laptop creates a one-time pairing secret (expires in 120s, max 3 attempts) and encodes
   `{ip, port, fp, secret, ttl}` as a QR code.
3. Phone scans the QR, connects directly over raw TLS, fetches the server's certificate,
   and compares its fingerprint to `fp`. **If they don't match, abort — do not proceed.**
4. Phone calls `POST /pair` with its own generated `device_id`, a human-readable name, and
   the `secret`.
5. Laptop validates the secret (not expired, under attempt limit), issues a random 256-bit
   token, stores only its hash, and returns the plaintext token once.
6. Phone stores `{device_id, token, pinned_fp}` securely (Android Keystore /
   EncryptedSharedPreferences in the real app).

## Sync cycle

```
Phone                                   Laptop
  |-- verify pinned fingerprint -------->|   (raw TLS check, no HTTP request)
  |-- scan local folder ---------------->|   (local, no network)
  |-- POST /sync/plan {phone_manifest} ->|
  |                                      |-- rescans its own folder
  |                                      |-- loads base_hash for this device_id
  |                                      |-- runs 3-way planner(base, laptop, phone)
  |<--------- {actions: [...]} ----------|
  |
  |  for each action:
  |    PUSH          -> GET /sync/download/{path}       (phone pulls)
  |    PULL          -> PUT /sync/upload/{path}?offset=N (phone pushes, chunked)
  |                     POST /sync/upload-complete/{path} {sha256}
  |    DELETE_LAPTOP -> delete locally, POST /sync/ack {path, sha256: null}
  |    DELETE_PHONE  -> POST /sync/delete {path}
  |    CONFLICT      -> download laptop's version under a "(laptop conflict TIMESTAMP)"
  |                     name; leave base unset so it resurfaces until resolved
```

`base_hash` is stored **per (device_id, path)** on the laptop, so pairing a second phone
never corrupts the first phone's sync state.

## Endpoints

### `GET /ping`
No auth. `{"status": "ok", "time": <unix ts>}`

### `POST /pair`
No auth (secret-gated instead).
Request: `{"device_id": str, "device_name": str, "secret": str}`
Response: `{"token": str, "device_id": str}` — 403 if the secret is invalid/expired/exhausted.

### `GET /manifest`
Rescans and returns the laptop's current file state:
`{"<relative_path>": {"size": int, "mtime": float, "sha256": str}, ...}`

### `POST /sync/plan`
Request: `{"phone_manifest": {...}}` (same shape as `/manifest`)
Response: `{"actions": [{"kind": "PUSH"|"PULL"|"DELETE_LAPTOP"|"DELETE_PHONE"|"CONFLICT", "path": str}, ...]}`

### `GET /sync/download/{path}`
Supports `Range: bytes=START-` for resume. Returns raw file bytes, `206` on a range request.

### `GET /sync/upload-status/{path}`
`{"received_bytes": int}` — how much of an in-progress upload the server already has,
so the client knows where to resume.

### `PUT /sync/upload/{path}?offset=N`
Raw body = one chunk's bytes, written at `offset` into `{path}.part`.
`{"received_bytes": int}`

### `POST /sync/upload-complete/{path}`
Request: `{"sha256": str}` — the client's hash of the full file.
Server recomputes the hash of the `.part` file; on mismatch returns `409` and the upload
must be retried from scratch. On match: atomic rename into place, `base_hash` updated,
`file_changed` broadcast to any connected WebSocket clients.

### `POST /sync/delete`
Request: `{"path": str}` — deletes locally (if present), clears `base_hash` for this device.

### `POST /sync/ack`
Request: `{"path": str, "sha256": str|null}` — the phone tells the laptop that it has
finished a PULL or a DELETE_LAPTOP action, so the laptop's `base_hash` for this device can
be updated to match. (The laptop can't know a PULL succeeded on the phone's end any other
way, since the phone initiated that download itself.)

### `GET /history`
Last 50 `sync_history` rows: `[{device_id, path, action, status, timestamp}, ...]`

### `WS /events`
Server pushes `{"type": "file_changed", "path": str}` whenever the laptop's file watcher
detects a settled (debounced) change. Clients should treat this as "run a sync cycle soon,"
not as the change itself — always re-fetch via `/sync/plan`.

## Design notes / why

- **Metadata-first, not full rescans over the wire.** Only hashes travel in `/sync/plan`;
  actual bytes move only for files the planner says need it.
- **Three-way merge, not timestamp comparison.** Clocks drift between devices and copying
  a file changes its mtime; comparing against a stored `base_hash` avoids both problems.
- **Chunked + resumable by construction.** `.part` files plus `offset`-addressed `PUT`
  calls mean an interrupted 2GB transfer resumes instead of restarting.
- **Hash verified before a file is ever considered synced.** `base_hash` is only set after
  a server-side hash check passes, so a corrupted transfer can never silently become the
  new "agreed" state.
