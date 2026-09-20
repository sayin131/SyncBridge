import sqlite3

def connect(path="syncbridge.db"):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS files (
            path      TEXT PRIMARY KEY,
            size      INTEGER,
            mtime     REAL,
            sha256    TEXT,
            base_hash TEXT,
            deleted   INTEGER DEFAULT 0
        )""")
    conn.commit()

def save_manifest(conn, manifest):
    """Store current scan. Files that vanished become tombstones."""
    with conn:  # one transaction
        for path, m in manifest.items():
            conn.execute("""
                INSERT INTO files (path, size, mtime, sha256, deleted)
                VALUES (?, ?, ?, ?, 0)
                ON CONFLICT(path) DO UPDATE SET
                    size=excluded.size, mtime=excluded.mtime,
                    sha256=excluded.sha256, deleted=0""",
                (path, m["size"], m["mtime"], m["sha256"]))
        rows = conn.execute("SELECT path FROM files WHERE deleted=0").fetchall()
        for r in rows:
            if r["path"] not in manifest:
                conn.execute("UPDATE files SET deleted=1 WHERE path=?", (r["path"],))

def load_manifest(conn):
    rows = conn.execute(
        "SELECT path, size, mtime, sha256 FROM files WHERE deleted=0").fetchall()
    return {r["path"]: {"size": r["size"], "mtime": r["mtime"], "sha256": r["sha256"]}
            for r in rows}

def set_base(conn, path, sha256):
    with conn:
        conn.execute("UPDATE files SET base_hash=? WHERE path=?", (sha256, path))

def load_base(conn):
    """{path: base_hash} for every file that has been synced at least once."""
    rows = conn.execute(
        "SELECT path, base_hash FROM files WHERE base_hash IS NOT NULL").fetchall()
    return {r["path"]: r["base_hash"] for r in rows}