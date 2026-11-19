"""
db.py — SQLite persistence: file manifests, per-device base hashes, devices, sync history.

Schema notes:
- files: current known state of each path on the laptop (deleted=1 is a tombstone)
- device_base: per-(device_id, path) last-synced hash. Multi-device aware from day one,
  so a second phone doesn't corrupt a first phone's sync state.
- devices: paired devices and their auth token hash
- sync_history: an audit log of every transfer/delete/conflict action
"""
import hashlib
import secrets
import sqlite3
import time


def connect(path: str = "syncbridge.db") -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS files (
            path      TEXT PRIMARY KEY,
            size      INTEGER,
            mtime     REAL,
            sha256    TEXT,
            deleted   INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS device_base (
            device_id TEXT NOT NULL,
            path      TEXT NOT NULL,
            base_hash TEXT,
            PRIMARY KEY (device_id, path)
        );

        CREATE TABLE IF NOT EXISTS devices (
            device_id    TEXT PRIMARY KEY,
            device_name  TEXT,
            token_hash   TEXT,
            cert_fp      TEXT,
            paired_at    REAL,
            last_seen    REAL
        );

        CREATE TABLE IF NOT EXISTS pairing_attempts (
            secret_hash  TEXT PRIMARY KEY,
            created_at   REAL,
            attempts     INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS sync_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id   TEXT,
            path        TEXT,
            action      TEXT,
            status      TEXT,
            timestamp   REAL
        );
    """)
    conn.commit()


# ---------- file manifest ----------

def save_manifest(conn: sqlite3.Connection, manifest: dict):
    """Upsert current scan results; paths missing from `manifest` become tombstones."""
    with conn:
        for path, m in manifest.items():
            conn.execute("""
                INSERT INTO files (path, size, mtime, sha256, deleted)
                VALUES (?, ?, ?, ?, 0)
                ON CONFLICT(path) DO UPDATE SET
                    size=excluded.size, mtime=excluded.mtime,
                    sha256=excluded.sha256, deleted=0
            """, (path, m["size"], m["mtime"], m["sha256"]))

        existing = conn.execute("SELECT path FROM files WHERE deleted=0").fetchall()
        for row in existing:
            if row["path"] not in manifest:
                conn.execute("UPDATE files SET deleted=1 WHERE path=?", (row["path"],))


def load_manifest(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        "SELECT path, size, mtime, sha256 FROM files WHERE deleted=0"
    ).fetchall()
    return {r["path"]: {"size": r["size"], "mtime": r["mtime"], "sha256": r["sha256"]} for r in rows}


def current_hashes(conn: sqlite3.Connection) -> dict:
    """{path: sha256} for non-deleted files — what the planner calls `laptop`."""
    return {p: m["sha256"] for p, m in load_manifest(conn).items()}


# ---------- per-device base hash (last synced state) ----------

def load_base(conn: sqlite3.Connection, device_id: str) -> dict:
    rows = conn.execute(
        "SELECT path, base_hash FROM device_base WHERE device_id=?", (device_id,)
    ).fetchall()
    return {r["path"]: r["base_hash"] for r in rows}


def set_base(conn: sqlite3.Connection, device_id: str, path: str, sha256: str | None):
    with conn:
        if sha256 is None:
            conn.execute(
                "DELETE FROM device_base WHERE device_id=? AND path=?", (device_id, path)
            )
        else:
            conn.execute("""
                INSERT INTO device_base (device_id, path, base_hash) VALUES (?, ?, ?)
                ON CONFLICT(device_id, path) DO UPDATE SET base_hash=excluded.base_hash
            """, (device_id, path, sha256))


# ---------- devices / pairing ----------

def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_device(conn: sqlite3.Connection, device_id: str, device_name: str, cert_fp: str) -> str:
    """Pairs a device, returns the plaintext token (only shown once)."""
    token = secrets.token_hex(32)
    with conn:
        conn.execute("""
            INSERT INTO devices (device_id, device_name, token_hash, cert_fp, paired_at, last_seen)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(device_id) DO UPDATE SET
                device_name=excluded.device_name, token_hash=excluded.token_hash,
                cert_fp=excluded.cert_fp, paired_at=excluded.paired_at
        """, (device_id, device_name, hash_token(token), cert_fp, time.time(), time.time()))
    return token


def verify_token(conn: sqlite3.Connection, device_id: str, token: str) -> bool:
    row = conn.execute(
        "SELECT token_hash FROM devices WHERE device_id=?", (device_id,)
    ).fetchone()
    if not row:
        return False
    # constant-time comparison to avoid timing side-channels
    return secrets.compare_digest(row["token_hash"], hash_token(token))


def touch_device(conn: sqlite3.Connection, device_id: str):
    with conn:
        conn.execute("UPDATE devices SET last_seen=? WHERE device_id=?", (time.time(), device_id))


# ---------- pairing secret throttling ----------

def register_pairing_secret(conn: sqlite3.Connection, secret: str):
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO pairing_attempts (secret_hash, created_at, attempts) VALUES (?, ?, 0)",
            (hash_token(secret), time.time()),
        )


def check_and_consume_pairing_secret(conn: sqlite3.Connection, secret: str, ttl_seconds=120, max_attempts=3) -> bool:
    """Returns True if the secret is valid and this attempt is allowed; always burns an attempt."""
    h = hash_token(secret)
    row = conn.execute(
        "SELECT created_at, attempts FROM pairing_attempts WHERE secret_hash=?", (h,)
    ).fetchone()
    if not row:
        return False
    if time.time() - row["created_at"] > ttl_seconds:
        return False
    if row["attempts"] >= max_attempts:
        return False
    with conn:
        conn.execute(
            "UPDATE pairing_attempts SET attempts = attempts + 1 WHERE secret_hash=?", (h,)
        )
    return True


def invalidate_pairing_secret(conn: sqlite3.Connection, secret: str):
    with conn:
        conn.execute("DELETE FROM pairing_attempts WHERE secret_hash=?", (hash_token(secret),))


# ---------- history ----------

def log(conn: sqlite3.Connection, device_id: str, path: str, action: str, status: str):
    with conn:
        conn.execute(
            "INSERT INTO sync_history (device_id, path, action, status, timestamp) VALUES (?, ?, ?, ?, ?)",
            (device_id, path, action, status, time.time()),
        )


def recent_history(conn: sqlite3.Connection, limit=50):
    return conn.execute(
        "SELECT * FROM sync_history ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
