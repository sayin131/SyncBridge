# SyncBridge

A secure, local-network, peer-to-peer file synchronization system between a laptop and a
phone — no cloud, no WhatsApp/Telegram, works over the same Wi-Fi.

This build has two parts:

- **`laptop/`** — the real server: FastAPI + TLS + certificate pinning + pairing +
  SQLite-backed three-way sync planner + chunked resumable transfer + file watcher +
  WebSocket live events. This is production-shaped code, not a mockup.
- **`phone_sim/`** — a command-line Python client that plays the phone's role (scans a
  folder, pairs, pulls a sync plan, executes it) so the **entire system runs end-to-end
  on one laptop**, without needing an Android build. See "Porting to real Android" below
  for what changes when you move this logic into Kotlin.

## What's been tested in this environment

The sandbox that built this has no outbound network access, so `fastapi`, `uvicorn`, and
`pytest` could not be installed here, which means **the live HTTPS server itself has not
been run end-to-end in this environment.** Everything that *could* be tested without those
packages was tested and passes:

```
laptop/tests/test_planner.py   — 17/17 passed  (every row of the 3-way merge table,
                                                  including add/edit/delete/conflict/rename-
                                                  adjacent cases and ordering)
laptop/tests/test_scanner.py   —  7/7 passed   (hashing, mtime caching, nested paths,
                                                  .part files ignored, path-traversal guard)
db.py manual verification      — tombstones, per-device base_hash isolation, token auth,
                                   pairing-secret throttling, and a malicious filename all
                                   verified directly
security.py manual verification — cert generation, fingerprinting, idempotency verified
all .py files                  — compile cleanly (python3 -m py_compile), no syntax errors
```

**On your own machine**, with internet access for `pip install`, follow "Run it" below to
bring up the real server and run a real sync. The parts tested here are the hardest and
most bug-prone (the merge logic and the hashing/caching), so what's left to verify on your
machine is mostly "does the HTTP plumbing work," which is lower-risk.

## Run it

### 1. Laptop server
```bash
cd laptop
python -m venv venv
source venv/bin/activate        # venv\Scripts\activate on Windows
pip install -r requirements.txt
pytest tests/ -v                # confirm 24/24 pass on your machine too
python run_server.py --root ./sync_root
```
This prints a pairing QR (`pairing_qr.png`) and the payload as JSON (ip, port, fingerprint,
secret) — the plaintext JSON is there so `phone_sim` can pair without actually scanning a
QR image.

### 2. Phone simulator (run in a second terminal, same laptop is fine for testing)
```bash
cd phone_sim
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

python client.py pair --ip <ip> --port 8443 --fp <fingerprint> --secret <secret> \
    --root ./phone_sync_root
```
Copy `ip`, `fp`, and `secret` from the server's printed payload. The secret expires in
120 seconds, so pair promptly or re-run `run_server.py` for a fresh one.

### 3. Try a sync
```bash
# put a file in laptop/sync_root/, then:
cd phone_sim && python client.py sync
# phone_sync_root/ should now have it

# put a different file in phone_sim/phone_sync_root/, then:
python client.py sync
# laptop/sync_root/ should now have it

python client.py status   # recent history
```

### 4. Try the interesting cases
- Edit the same filename differently on both sides, then sync — expect a
  `(laptop conflict ...)` copy to appear in `phone_sync_root/`, original untouched.
- Delete a file on one side, sync — it should disappear on the other.
- Start an upload of a big file and kill the server mid-transfer — restart it and sync
  again; it should resume from the `.part` file's current size, not from zero.
- Pair a second `device_id` against the same laptop root — its `base_hash` state is
  independent (check the `device_base` table in `syncbridge.db`).

## Repo layout
```
SyncBridge/
├── laptop/
│   ├── main.py          FastAPI app: all endpoints
│   ├── run_server.py    entry point: cert + QR + uvicorn with TLS
│   ├── db.py            SQLite: files, device_base, devices, pairing, history
│   ├── planner.py       the 3-way merge algorithm
│   ├── scanner.py       folder walk + hashing (with mtime cache) + path safety
│   ├── security.py      self-signed cert generation + fingerprinting
│   ├── pairing_qr.py    QR payload + image generation
│   ├── watcher.py       debounced filesystem watcher -> WebSocket broadcast
│   └── tests/
├── phone_sim/
│   ├── client.py        pair / sync / status commands
│   └── security_sim.py  fetches + checks the server cert fingerprint (pinning)
├── protocol/
│   └── protocol.md       full endpoint + message-format spec, threat model
└── README.md (this file)
```

## Porting `phone_sim` to real Android (Kotlin)

The control flow in `phone_sim/client.py` — pin cert, pair, scan, request plan, execute
actions — is exactly what the Android app should do. What changes:

| phone_sim (Python)              | Real Android equivalent |
|---|---|
| `requests` + `verify=False` + manual fingerprint pre-check | OkHttp `CertificatePinner`, which enforces the pin at the TLS handshake itself (stronger — no separate step to forget) |
| `phone_state.json` on disk      | Android Keystore / `EncryptedSharedPreferences` for the token and pinned fingerprint |
| `scanner.py` (`os.walk`)        | Storage Access Framework (`DocumentFile` tree from a folder picker), since Scoped Storage blocks arbitrary filesystem walks |
| Manual CLI invocation of `sync` | `WorkManager` periodic job, plus a foreground service while a sync is actively running (Android kills plain background threads) |
| Printed progress                | Notification with progress bar |
| ZXing/ML Kit for the actual QR scan (phone_sim takes the payload as CLI args instead) | Same libraries — this part was simplified in the simulator, not replaced |

The server side (`laptop/`) doesn't change at all when you build the real Android client —
it already speaks a plain, documented HTTP/JSON protocol (see `protocol/protocol.md`).

## Known limitations of this build (next things to do)

- No rename detection yet — a move shows up as a delete + a new file, not a cheap rename
  (the planner table has a placeholder for this; see protocol.md's design notes).
- `phone_sim` is single-threaded and sequential per action; the real app should queue and
  parallelize independent transfers.
- No bandwidth limiting, ignore-patterns, or multi-laptop support yet.
- Conflict files are never auto-cleaned; the user must manually resolve them, by design.
