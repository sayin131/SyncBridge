"""
main.py — the SyncBridge laptop server.

Run with: python run_server.py
(main.py itself is imported by uvicorn via run_server.py, which also handles TLS setup)

Endpoints (all except /ping and /pair require X-Device-Id + Authorization: Bearer <token>):
  GET  /ping
  POST /pair                                  {device_id, device_name, secret} -> {token}
  GET  /manifest                               -> current laptop file state
  POST /sync/plan                              {phone_manifest} -> {actions: [...]}
  GET  /sync/download/{path}                    (supports Range header) -> file bytes
  GET  /sync/upload-status/{path}                -> {received_bytes}
  PUT  /sync/upload/{path}?offset=N              raw bytes -> append chunk
  POST /sync/upload-complete/{path}              {sha256} -> verify + finalize + update base
  POST /sync/delete                              {path} -> delete local file + update base
  POST /sync/ack                                 {path, sha256|null} -> record base after a
                                                  phone-side PULL/DELETE_LAPTOP completes
  GET  /history                                  recent sync_history rows
  WS   /events                                   live file_changed notifications
"""
import asyncio
import os
import time

from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import db
import planner
from scanner import safe_join, scan, sha256_file

SYNC_ROOT = os.environ.get("SYNCBRIDGE_ROOT", os.path.abspath("./sync_root"))
os.makedirs(SYNC_ROOT, exist_ok=True)

app = FastAPI(title="SyncBridge Laptop Server")
conn = db.connect()
db.init_db(conn)

# simple in-process websocket fan-out
_ws_clients: set[WebSocket] = set()


def rescan_and_store():
    cache = db.load_manifest(conn)
    manifest = scan(SYNC_ROOT, cache=cache)
    db.save_manifest(conn, manifest)
    return manifest


# ---------- auth ----------

def require_auth(x_device_id: str = Header(...), authorization: str = Header(...)) -> str:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    token = authorization.removeprefix("Bearer ")
    if not db.verify_token(conn, x_device_id, token):
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
    cert_fp = ""  # the phone already pinned this from the QR payload directly
    token = db.create_device(conn, req.device_id, req.device_name, cert_fp)
    return {"token": token, "device_id": req.device_id}


# ---------- manifest & planning ----------

@app.get("/manifest")
def manifest(device_id: str = Header(..., alias="X-Device-Id"), authorization: str = Header(...)):
    require_auth(device_id, authorization)
    m = rescan_and_store()
    return m


class PlanRequest(BaseModel):
    phone_manifest: dict


@app.post("/sync/plan")
def sync_plan(req: PlanRequest, device_id: str = Header(..., alias="X-Device-Id"),
              authorization: str = Header(...)):
    require_auth(device_id, authorization)
    laptop_manifest = rescan_and_store()
    laptop_hashes = {p: m["sha256"] for p, m in laptop_manifest.items()}
    phone_hashes = {p: m["sha256"] for p, m in req.phone_manifest.items()}
    base = db.load_base(conn, device_id)

    actions = planner.plan(base, laptop_hashes, phone_hashes)
    return {"actions": [{"kind": a.kind, "path": a.path} for a in actions]}


# ---------- download (laptop -> phone, i.e. phone is PULLing or receiving a PUSH) ----------

@app.get("/sync/download/{path:path}")
def download(path: str, request: Request,
             device_id: str = Header(..., alias="X-Device-Id"), authorization: str = Header(...)):
    require_auth(device_id, authorization)
    full = safe_join(SYNC_ROOT, path)
    if not os.path.isfile(full):
        raise HTTPException(404, "file not found")

    file_size = os.path.getsize(full)
    range_header = request.headers.get("range")
    start, end = 0, file_size - 1
    status_code = 200
    if range_header:
        # "bytes=START-END" (END optional)
        try:
            spec = range_header.replace("bytes=", "")
            s, _, e = spec.partition("-")
            start = int(s) if s else 0
            end = int(e) if e else file_size - 1
            status_code = 206
        except ValueError:
            raise HTTPException(416, "bad range header")

    def iterfile():
        with open(full, "rb") as f:
            f.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = f.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
    }
    return StreamingResponse(iterfile(), status_code=status_code, headers=headers,
                              media_type="application/octet-stream")


# ---------- upload (phone -> laptop, i.e. phone is PUSHing or completing a PULL-ack) ----------

def part_path(full_path: str) -> str:
    return full_path + ".part"


@app.get("/sync/upload-status/{path:path}")
def upload_status(path: str, device_id: str = Header(..., alias="X-Device-Id"),
                   authorization: str = Header(...)):
    require_auth(device_id, authorization)
    full = safe_join(SYNC_ROOT, path)
    p = part_path(full)
    received = os.path.getsize(p) if os.path.exists(p) else 0
    return {"received_bytes": received}


@app.put("/sync/upload/{path:path}")
async def upload_chunk(path: str, request: Request, offset: int = 0,
                        device_id: str = Header(..., alias="X-Device-Id"),
                        authorization: str = Header(...)):
    require_auth(device_id, authorization)
    full = safe_join(SYNC_ROOT, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    p = part_path(full)

    body = await request.body()
    mode = "r+b" if os.path.exists(p) else "wb"
    with open(p, mode) as f:
        f.seek(offset)
        f.write(body)

    return {"received_bytes": os.path.getsize(p)}


class CompleteUploadRequest(BaseModel):
    sha256: str


@app.post("/sync/upload-complete/{path:path}")
async def upload_complete(path: str, req: CompleteUploadRequest,
                           device_id: str = Header(..., alias="X-Device-Id"),
                           authorization: str = Header(...)):
    require_auth(device_id, authorization)
    full = safe_join(SYNC_ROOT, path)
    p = part_path(full)
    if not os.path.exists(p):
        raise HTTPException(400, "no in-progress upload for this path")

    actual = sha256_file(p)
    if actual != req.sha256:
        db.log(conn, device_id, path, "PULL_RECEIVE", "hash_mismatch")
        raise HTTPException(409, f"hash mismatch: expected {req.sha256}, got {actual}")

    os.replace(p, full)  # atomic on POSIX and Windows (same filesystem)
    rescan_and_store()
    db.set_base(conn, device_id, path, actual)
    db.log(conn, device_id, path, "PUSH_RECEIVE", "ok")
    asyncio.create_task(broadcast({"type": "file_changed", "path": path}))
    return {"status": "ok", "sha256": actual}


# ---------- delete ----------

class DeleteRequest(BaseModel):
    path: str


@app.post("/sync/delete")
def sync_delete(req: DeleteRequest, device_id: str = Header(..., alias="X-Device-Id"),
                 authorization: str = Header(...)):
    require_auth(device_id, authorization)
    full = safe_join(SYNC_ROOT, req.path)
    if os.path.exists(full):
        os.remove(full)
    rescan_and_store()
    db.set_base(conn, device_id, req.path, None)
    db.log(conn, device_id, req.path, "DELETE", "ok")
    return {"status": "ok"}


# ---------- ack: phone tells laptop a PULL/DELETE_LAPTOP finished, to update base ----------

class AckRequest(BaseModel):
    path: str
    sha256: str | None = None


@app.post("/sync/ack")
def sync_ack(req: AckRequest, device_id: str = Header(..., alias="X-Device-Id"),
             authorization: str = Header(...)):
    require_auth(device_id, authorization)
    db.set_base(conn, device_id, req.path, req.sha256)
    db.log(conn, device_id, req.path, "ACK", "ok")
    return {"status": "ok"}


# ---------- history ----------

@app.get("/history")
def history(device_id: str = Header(..., alias="X-Device-Id"), authorization: str = Header(...)):
    require_auth(device_id, authorization)
    rows = db.recent_history(conn)
    return [dict(r) for r in rows]


# ---------- websocket events ----------

@app.websocket("/events")
async def events(ws: WebSocket):
    await ws.accept()
    _ws_clients.add(ws)
    try:
        while True:
            await ws.receive_text()  # we don't expect client messages; just keep-alive
    except WebSocketDisconnect:
        _ws_clients.discard(ws)


async def broadcast(message: dict):
    dead = []
    for ws in _ws_clients:
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _ws_clients.discard(ws)


# ---------- filesystem watcher ----------

_observer = None


@app.on_event("startup")
async def start_watcher():
    global _observer
    import watcher

    loop = asyncio.get_event_loop()

    async def on_settled(path: str):
        rescan_and_store()
        rel = os.path.relpath(path, SYNC_ROOT).replace(os.sep, "/")
        await broadcast({"type": "file_changed", "path": rel})

    _observer = watcher.start_watching(SYNC_ROOT, loop, on_settled)


@app.on_event("shutdown")
async def stop_watcher():
    if _observer:
        _observer.stop()
        _observer.join(timeout=2)
