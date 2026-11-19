"""
client.py — a command-line stand-in for the Android app. It does everything a real
phone client would: pin the server's cert, pair, scan its own folder, ask the laptop
for a sync plan, and execute PUSH/PULL/DELETE/CONFLICT actions with chunked, resumable,
hash-verified transfers.

This lets the whole SyncBridge system be demoed and tested end-to-end on one laptop,
without needing an Android build. The real Android app should mirror this control flow
(pair -> scan -> plan -> execute) using OkHttp + CertificatePinner instead of `requests`.

Usage:
    python client.py pair --ip 192.168.1.20 --port 8443 --fp <fingerprint> --secret <secret>
    python client.py sync
    python client.py status
"""
import argparse
import hashlib
import json
import os
import sys
import time
import uuid

import requests
import urllib3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "laptop"))
from scanner import scan, sha256_file, safe_join  # noqa: E402

import security_sim  # noqa: E402

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

STATE_FILE = os.path.join(os.path.dirname(__file__), "phone_state.json")
CHUNK_SIZE = 4 * 1024 * 1024  # 4 MB


def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE) as f:
        return json.load(f)


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def base_url(state: dict) -> str:
    return f"https://{state['ip']}:{state['port']}"


def auth_headers(state: dict) -> dict:
    return {"X-Device-Id": state["device_id"], "Authorization": f"Bearer {state['token']}"}


# ---------- pair ----------

def cmd_pair(args):
    print(f"Verifying server certificate fingerprint before trusting it...")
    if not security_sim.verify_pin(args.ip, args.port, args.fp):
        print("FINGERPRINT MISMATCH. Refusing to pair — this could be a man-in-the-middle.")
        sys.exit(1)
    print("Fingerprint verified. Proceeding with pairing.")

    device_id = args.device_id or f"phone-{uuid.uuid4().hex[:8]}"
    resp = requests.post(
        f"https://{args.ip}:{args.port}/pair",
        json={"device_id": device_id, "device_name": args.device_name, "secret": args.secret},
        verify=False,
        timeout=10,
    )
    if resp.status_code != 200:
        print(f"Pairing failed: {resp.status_code} {resp.text}")
        sys.exit(1)

    data = resp.json()
    state = {
        "ip": args.ip,
        "port": args.port,
        "fp": args.fp,
        "device_id": device_id,
        "token": data["token"],
        "root": os.path.abspath(args.root),
    }
    os.makedirs(state["root"], exist_ok=True)
    save_state(state)
    print(f"Paired as {device_id}. Sync root: {state['root']}")
    print(f"State saved to {STATE_FILE}")


# ---------- sync ----------

def ensure_pinned(state: dict):
    if not security_sim.verify_pin(state["ip"], state["port"], state["fp"]):
        print("FINGERPRINT MISMATCH on this connection. Aborting sync for safety.")
        sys.exit(1)


def upload_file(state: dict, rel_path: str):
    full = safe_join(state["root"], rel_path)
    size = os.path.getsize(full)
    url = f"{base_url(state)}/sync/upload/{rel_path}"

    # resume support: ask server how much it already has
    status = requests.get(
        f"{base_url(state)}/sync/upload-status/{rel_path}", headers=auth_headers(state),
        verify=False, timeout=10,
    ).json()
    offset = status.get("received_bytes", 0)
    if offset > size:
        offset = 0  # stale partial from a different version, start over

    with open(full, "rb") as f:
        f.seek(offset)
        while offset < size:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            r = requests.put(
                f"{url}?offset={offset}", data=chunk, headers=auth_headers(state),
                verify=False, timeout=30,
            )
            r.raise_for_status()
            offset += len(chunk)
            pct = int(100 * offset / size) if size else 100
            print(f"  uploading {rel_path}: {pct}%", end="\r")
    print()

    digest = sha256_file(full)
    r = requests.post(
        f"{base_url(state)}/sync/upload-complete/{rel_path}",
        json={"sha256": digest}, headers=auth_headers(state), verify=False, timeout=30,
    )
    r.raise_for_status()
    print(f"  PUSH complete: {rel_path} ({digest[:12]}...)")
    return digest


def download_file(state: dict, rel_path: str):
    full = safe_join(state["root"], rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    part = full + ".part"

    offset = os.path.getsize(part) if os.path.exists(part) else 0
    headers = {**auth_headers(state), "Range": f"bytes={offset}-"}
    with requests.get(
        f"{base_url(state)}/sync/download/{rel_path}", headers=headers,
        verify=False, stream=True, timeout=30,
    ) as r:
        r.raise_for_status()
        mode = "r+b" if offset else "wb"
        with open(part, mode) as f:
            f.seek(offset)
            for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
                f.write(chunk)

    digest = sha256_file(part)
    os.replace(part, full)
    print(f"  PULL complete: {rel_path} ({digest[:12]}...)")
    return digest


def handle_conflict(state: dict, rel_path: str):
    """Download the laptop's version alongside the phone's own, instead of overwriting."""
    full = safe_join(state["root"], rel_path)
    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    base, ext = os.path.splitext(full)
    conflict_name = f"{base} (laptop conflict {stamp}){ext}"

    if os.path.exists(full):
        tmp_target = conflict_name
    else:
        tmp_target = full  # phone didn't have it at all; nothing to preserve

    # download laptop's version into the conflict-named file
    rel_conflict = os.path.relpath(tmp_target, state["root"]).replace(os.sep, "/")
    headers = auth_headers(state)
    with requests.get(
        f"{base_url(state)}/sync/download/{rel_path}", headers=headers,
        verify=False, stream=True, timeout=30,
    ) as r:
        r.raise_for_status()
        os.makedirs(os.path.dirname(tmp_target), exist_ok=True)
        with open(tmp_target, "wb") as f:
            for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
                f.write(chunk)

    print(f"  CONFLICT on {rel_path}: kept both versions")
    print(f"    phone version:  {rel_path}")
    print(f"    laptop version: {rel_conflict}")
    # Don't update base_hash — leave this path unresolved so it surfaces again
    # on the next sync until the user manually reconciles it (deletes one copy).


def cmd_sync(args):
    state = load_state()
    if not state:
        print("Not paired yet. Run `python client.py pair ...` first.")
        sys.exit(1)

    ensure_pinned(state)
    print(f"Scanning {state['root']} ...")
    phone_manifest = scan(state["root"])
    print(f"  {len(phone_manifest)} files locally")

    print("Requesting sync plan from laptop...")
    resp = requests.post(
        f"{base_url(state)}/sync/plan",
        json={"phone_manifest": phone_manifest},
        headers=auth_headers(state), verify=False, timeout=30,
    )
    resp.raise_for_status()
    actions = resp.json()["actions"]
    print(f"Plan: {len(actions)} action(s)")

    if not actions:
        print("Already in sync.")
        return

    for action in actions:
        kind, path = action["kind"], action["path"]
        print(f"- {kind}: {path}")

        if kind == "PUSH":
            # laptop has a new/changed file the phone doesn't -> phone PULLs it
            digest = download_file(state, path)
            requests.post(f"{base_url(state)}/sync/ack", json={"path": path, "sha256": digest},
                           headers=auth_headers(state), verify=False, timeout=10)

        elif kind == "PULL":
            # phone has a new/changed file the laptop doesn't -> phone PUSHes it
            digest = upload_file(state, path)
            # server already set its own base on upload-complete; nothing more to do

        elif kind == "DELETE_LAPTOP":
            # laptop deleted it -> phone should delete its local copy too
            full = safe_join(state["root"], path)
            if os.path.exists(full):
                os.remove(full)
            requests.post(f"{base_url(state)}/sync/ack", json={"path": path, "sha256": None},
                           headers=auth_headers(state), verify=False, timeout=10)
            print(f"  deleted locally: {path}")

        elif kind == "DELETE_PHONE":
            # phone deleted it -> tell laptop to delete its copy too
            requests.post(f"{base_url(state)}/sync/delete", json={"path": path},
                           headers=auth_headers(state), verify=False, timeout=10)
            print(f"  told laptop to delete: {path}")

        elif kind == "CONFLICT":
            handle_conflict(state, path)

        else:
            print(f"  (unknown action kind: {kind}, skipping)")

    print("Sync cycle complete.")


# ---------- status ----------

def cmd_status(args):
    state = load_state()
    if not state:
        print("Not paired.")
        return
    ensure_pinned(state)
    resp = requests.get(f"{base_url(state)}/history", headers=auth_headers(state),
                         verify=False, timeout=10)
    resp.raise_for_status()
    for row in resp.json()[:15]:
        print(f"{time.strftime('%H:%M:%S', time.localtime(row['timestamp']))}  "
              f"{row['action']:16s} {row['status']:12s} {row['path']}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_pair = sub.add_parser("pair")
    p_pair.add_argument("--ip", required=True)
    p_pair.add_argument("--port", type=int, required=True)
    p_pair.add_argument("--fp", required=True, help="server cert sha256 fingerprint")
    p_pair.add_argument("--secret", required=True, help="one-time pairing secret from QR")
    p_pair.add_argument("--device-id", default=None)
    p_pair.add_argument("--device-name", default="phone_sim")
    p_pair.add_argument("--root", default="./phone_sync_root")
    p_pair.set_defaults(func=cmd_pair)

    p_sync = sub.add_parser("sync")
    p_sync.set_defaults(func=cmd_sync)

    p_status = sub.add_parser("status")
    p_status.set_defaults(func=cmd_status)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
