"""
main.py — the SyncBridge laptop server (FastAPI app).

Started by run_server.py (CLI) or app_gui.py (desktop app). Endpoints (all except /ping
and /pair require X-Device-Id + Authorization: Bearer <token>); see protocol/protocol.md.
"""
import asyncio
import os
import re
import threading
import time

from fastapi import (Depends, FastAPI, Header, HTTPException, Request, WebSocket,
                     WebSocketDisconnect)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import db
import planner
from scanner import safe_join, scan, sha256_file

SYNC_ROOT = os.environ.get("SYNCBRIDGE_ROOT", os.path.abspath("./sync_root"))
os.makedirs(SYNC_ROOT, exist_ok=True)

app = FastAPI(title="SyncBridge Laptop Server")
conn = db.connect(os.environ.get("SYNCBRIDGE_DB", "syncbridge.db"))
db.init_db(conn)

_scan_lock = threading.RLock()
_ws_clients: set[WebSocket] = set()
_loop: asyncio.AbstractEventLoop | None = None
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


def rescan_and_store() -> dict:
    with _scan_lock:
        cache = db.load_manifest(conn)
        manifest = scan(SYNC_ROOT, cache=cache)
        db.save_manifest(conn, manifest)
        return manifest


def resolve(path: str) -> str:
    """Map a client-supplied relative path to an absolute path inside SYNC_ROOT, or 400."""
    if path.endswith(".part"):
        raise HTTPException(400, "invalid path")
    try:
        return safe_join(SYNC_ROOT, path)
    except ValueError:
        raise HTTPException(400, "invalid path")


def part_path(full: str, sha: str) -> str:
    """Partial uploads are keyed by the file's final hash, so bytes from two different
    versions of a file can never be mixed into one resumed upload."""
    if not _SHA_RE.match(sha):
        raise HTTPException(400, "sha256 must be 64 lowercase hex chars")
    return f"{full}.{sha[:16]}.part"


def notify(message: dict):
    """Fire-and-forget websocket broadcast, callable from any thread."""
    if _loop is not None and _loop.is_running():
        asyncio.run_coroutine_threadsafe(broadcast(message), _loop)


# ---------- auth ----------

def require_auth(x_device_id: str = Header(...), authorization: str = Header(...)) -> str:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    if not db.verify_token(conn, x_device_id, authorization.removeprefix("Bearer ")):
        raise HTTPException(401, "invalid device_id or token")
    db.touch_device(conn, x_device_id)
    return x_device_id


# ---------- basic ----------

@app.get("/ping")
def ping():
    return {"status": "ok", "time": time.time()}


# ---------- pairing ----------

class PairRequest(BaseModel):
    device_id: str
    device_name: str
    secret: str


@app.post("/pair")
def pair(req: PairRequest):
    if not db.check_and_consume_pairing_secret(conn, req.secret):
        raise HTTPException(403, "invalid, expired, or exhausted pairing secret")
    db.invalidate_pairing_secret(conn, req.secret)
    token = db.create_device(conn, req.device_id, req.device_name, "")
    return {"token": token, "device_id": req.device_id}


# ---------- manifest & planning ----------

@app.get("/manifest")
def manifest(device_id: str = Depends(require_auth)):
    return rescan_and_store()


class PlanRequest(BaseModel):
    phone_manifest: dict


@app.post("/sync/plan")
def sync_plan(req: PlanRequest, device_id: str = Depends(require_auth)):
    laptop_manifest = rescan_and_store()
    laptop_hashes = {p: m["sha256"] for p, m in laptop_manifest.items()}
    phone_hashes = {p: m["sha256"] for p, m in req.phone_manifest.items()}
    base = db.load_base(conn, device_id)

    out = []
    for a in planner.plan(base, laptop_hashes, phone_hashes):
        entry = {"kind": a.kind, "path": a.path}
        # Attach the laptop's current hash/size whenever the laptop has the file, so the
        # phone can verify a download. Absent for a path the laptop doesn't have (deleted).
        if a.path in laptop_manifest:
            entry["sha256"] = laptop_manifest[a.path]["sha256"]
            entry["size"] = laptop_manifest[a.path]["size"]
        out.append(entry)
    return {"actions": out}


# ---------- download (laptop -> phone) ----------

@app.get("/sync/download/{path:path}")
def download(path: str, request: Request, device_id: str = Depends(require_auth)):
    full = resolve(path)
    if not os.path.isfile(full):
        raise HTTPException(404, "file not found")

    file_size = os.path.getsize(full)
    start, end, status_code = 0, file_size - 1, 200
    range_header = request.headers.get("range")
    if range_header:
        try:
            s, _, e = range_header.replace("bytes=", "").partition("-")
            start = int(s) if s else 0
            end = int(e) if e else file_size - 1
            status_code = 206
        except ValueError:
            raise HTTPException(416, "bad range header")
        if start > end + 1 or start > file_size or end >= file_size:
            raise HTTPException(416, "range not satisfiable")

    length = max(end - start + 1, 0)

    def iterfile():
        with open(full, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
    }
    return StreamingResponse(iterfile(), status_code=status_code, headers=headers,
                             media_type="application/octet-stream")


# ---------- upload (phone -> laptop) ----------

@app.get("/sync/upload-status/{path:path}")
def upload_status(path: str, sha256: str, device_id: str = Depends(require_auth)):
    p = part_path(resolve(path), sha256)
    return {"received_bytes": os.path.getsize(p) if os.path.exists(p) else 0}


@app.put("/sync/upload/{path:path}")
async def upload_chunk(path: str, request: Request, sha256: str, offset: int = 0,
                       device_id: str = Depends(require_auth)):
    full = resolve(path)
    p = part_path(full, sha256)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    body = await request.body()

    if offset == 0 or not os.path.exists(p):
        mode, offset = "wb", 0          # fresh start: truncate any stale partial
    else:
        if offset > os.path.getsize(p):
            raise HTTPException(409, "offset is beyond what the server has; re-query status")
        mode = "r+b"
    with open(p, mode) as f:
        f.seek(offset)
        f.write(body)
    return {"received_bytes": os.path.getsize(p)}


class CompleteUploadRequest(BaseModel):
    sha256: str


@app.post("/sync/upload-complete/{path:path}")
def upload_complete(path: str, req: CompleteUploadRequest, device_id: str = Depends(require_auth)):
    full = resolve(path)
    p = part_path(full, req.sha256)
    if not os.path.exists(p):
        raise HTTPException(400, "no in-progress upload for this path/hash")

    actual = sha256_file(p)
    if actual != req.sha256:
        os.remove(p)  # corrupted; force a clean restart
        db.log(conn, device_id, path, "UPLOAD", "hash_mismatch")
        raise HTTPException(409, "hash mismatch; upload discarded, retry from the start")

    os.replace(p, full)  # atomic rename into place
    rescan_and_store()
    db.set_base(conn, device_id, path, actual)
    db.log(conn, device_id, path, "UPLOAD", "ok")
    notify({"type": "file_changed", "path": path})
    return {"status": "ok", "sha256": actual}


# ---------- delete / ack ----------

class DeleteRequest(BaseModel):
    path: str


@app.post("/sync/delete")
def sync_delete(req: DeleteRequest, device_id: str = Depends(require_auth)):
    full = resolve(req.path)
    if os.path.exists(full):
        os.remove(full)
    rescan_and_store()
    db.set_base(conn, device_id, req.path, None)
    db.log(conn, device_id, req.path, "DELETE", "ok")
    return {"status": "ok"}


class AckRequest(BaseModel):
    path: str
    sha256: str | None = None


@app.post("/sync/ack")
def sync_ack(req: AckRequest, device_id: str = Depends(require_auth)):
    """Phone reports it finished applying a laptop->phone change (download or delete),
    so the base for this device/path can advance to the agreed state."""
    resolve(req.path)  # validates path
    db.set_base(conn, device_id, req.path, req.sha256)
    db.log(conn, device_id, req.path, "DOWNLOAD" if req.sha256 else "DELETE_LOCAL", "ok")
    return {"status": "ok"}


@app.get("/history")
def history(device_id: str = Depends(require_auth)):
    return [dict(r) for r in db.recent_history(conn)]


# ---------- websocket events (authenticated) ----------

@app.websocket("/events")
async def events(ws: WebSocket):
    dev = ws.headers.get("x-device-id", "")
    auth = ws.headers.get("authorization", "")
    if not auth.startswith("Bearer ") or not db.verify_token(conn, dev, auth[7:]):
        await ws.close(code=4401)
        return
    await ws.accept()
    _ws_clients.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _ws_clients.discard(ws)


async def broadcast(message: dict):
    for ws in list(_ws_clients):
        try:
            await ws.send_json(message)
        except Exception:
            _ws_clients.discard(ws)


# ---------- filesystem watcher ----------

_observer = None


@app.on_event("startup")
async def start_watcher():
    global _observer, _loop
    import watcher

    _loop = asyncio.get_running_loop()

    async def on_settled(path: str):
        await asyncio.to_thread(rescan_and_store)
        rel = os.path.relpath(path, SYNC_ROOT).replace(os.sep, "/")
        await broadcast({"type": "file_changed", "path": rel})

    _observer = watcher.start_watching(SYNC_ROOT, _loop, on_settled)


@app.on_event("shutdown")
async def stop_watcher():
    global _observer
    if _observer:
        _observer.stop()
        _observer.join(timeout=2)
        _observer = None
