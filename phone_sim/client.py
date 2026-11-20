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


def upload_file(state: dict, rel_path: str) -> str:
    full = safe_join(state["root"], rel_path)
    size = os.path.getsize(full)
    digest = sha256_file(full)
    url = f"{base_url(state)}/sync/upload/{rel_path}"
    h = auth_headers(state)

    # resume: the server's partial file is keyed by this exact hash, so it is always
    # a prefix of *this* version of the file
    offset = requests.get(f"{base_url(state)}/sync/upload-status/{rel_path}",
                          params={"sha256": digest}, headers=h, verify=False,
                          timeout=10).json().get("received_bytes", 0)
    if offset > size:
        offset = 0

    with open(full, "rb") as f:
        f.seek(offset)
        while offset < size:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            r = requests.put(url, params={"offset": offset, "sha256": digest}, data=chunk,
                             headers=h, verify=False, timeout=60)
            r.raise_for_status()
            offset += len(chunk)
            print(f"  uploading {rel_path}: {int(100 * offset / size) if size else 100}%", end="\r")
    print()

    r = requests.post(f"{base_url(state)}/sync/upload-complete/{rel_path}",
                      json={"sha256": digest}, headers=h, verify=False, timeout=120)
    r.raise_for_status()
    print(f"  uploaded: {rel_path} ({digest[:12]}...)")
    return digest


def download_file(state: dict, rel_path: str, expected_sha: str | None) -> str:
    full = safe_join(state["root"], rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    # partial is keyed by expected hash so a stale partial of an older version is never reused
    tag = (expected_sha or "nohash")[:16]
    part = f"{full}.{tag}.part"

    offset = os.path.getsize(part) if os.path.exists(part) else 0
    headers = {**auth_headers(state), "Range": f"bytes={offset}-"}
    with requests.get(f"{base_url(state)}/sync/download/{rel_path}", headers=headers,
                      verify=False, stream=True, timeout=60) as r:
        r.raise_for_status()
        append = offset > 0 and r.status_code == 206
        with open(part, "ab" if append else "wb") as f:
            for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
                f.write(chunk)

    digest = sha256_file(part)
    if expected_sha and digest != expected_sha:
        os.remove(part)
        raise RuntimeError(f"hash mismatch for {rel_path}; discarded, will retry next sync")
    os.replace(part, full)
    print(f"  downloaded: {rel_path} ({digest[:12]}...)")
    return digest


def ack(state: dict, path: str, sha: str | None):
    requests.post(f"{base_url(state)}/sync/ack", json={"path": path, "sha256": sha},
                  headers=auth_headers(state), verify=False, timeout=10).raise_for_status()


def handle_conflict(state: dict, action: dict) -> bool:
    """
    Converging conflict policy (laptop version keeps the original name):
      laptop deleted, phone edited -> phone's edit wins: re-upload it (nothing lost)
      phone deleted, laptop edited -> restore laptop's version here (nothing lost)
      both changed                 -> rename phone's file to "name (phone conflict TS).ext",
                                      download laptop's under the original name. The renamed
                                      file is then a brand-new file and syncs to the laptop on
                                      the next pass, so both sides end up with both versions.
    Returns True if a follow-up sync pass is needed.
    """
    path, laptop_sha = action["path"], action.get("sha256")
    full = safe_join(state["root"], path)
    have_local = os.path.exists(full)

    if laptop_sha is None and have_local:
        upload_file(state, path)
        print("  conflict resolved: laptop deleted it but you edited it, so your version was kept")
        return False
    if not have_local:
        sha = download_file(state, path, laptop_sha)
        ack(state, path, sha)
        print("  conflict resolved: restored laptop's edited version")
        return False

    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    base, ext = os.path.splitext(full)
    renamed = f"{base} (phone conflict {stamp}){ext}"
    os.replace(full, renamed)
    sha = download_file(state, path, laptop_sha)
    ack(state, path, sha)
    print(f"  CONFLICT: kept both. Laptop's is {path}; yours is {os.path.basename(renamed)}")
    return True


def sync_pass(state: dict) -> tuple[int, bool]:
    print(f"Scanning {state['root']} ...")
    phone_manifest = scan(state["root"])
    print(f"  {len(phone_manifest)} files locally")
    resp = requests.post(f"{base_url(state)}/sync/plan", json={"phone_manifest": phone_manifest},
                         headers=auth_headers(state), verify=False, timeout=60)
    resp.raise_for_status()
    actions = resp.json()["actions"]
    print(f"Plan: {len(actions)} action(s)")

    again = False
    for action in actions:
        kind, path = action["kind"], action["path"]
        print(f"- {kind}: {path}")
        try:
            if kind == "PUSH":            # laptop has newer/new -> phone downloads
                sha = download_file(state, path, action.get("sha256"))
                ack(state, path, sha)
            elif kind == "PULL":          # phone has newer/new -> phone uploads
                upload_file(state, path)
            elif kind == "DELETE_LAPTOP":  # laptop deleted it -> delete here
                full = safe_join(state["root"], path)
                if os.path.exists(full):
                    os.remove(full)
                ack(state, path, None)
                print(f"  deleted locally: {path}")
            elif kind == "DELETE_PHONE":   # phone deleted it -> tell laptop
                requests.post(f"{base_url(state)}/sync/delete", json={"path": path},
                              headers=auth_headers(state), verify=False, timeout=10).raise_for_status()
                print(f"  told laptop to delete: {path}")
            elif kind == "CONFLICT":
                again = handle_conflict(state, action) or again
            else:
                print(f"  (unknown action {kind}, skipped)")
        except Exception as e:  # one bad file must not abort the whole sync
            print(f"  FAILED: {e}")
    return len(actions), again


def cmd_sync(args):
    state = load_state()
    if not state:
        print("Not paired yet. Run `python client.py pair ...` first.")
        sys.exit(1)
    ensure_pinned(state)

    n, again = sync_pass(state)
    if again:  # conflict copies created; push them to the laptop
        print("\nSecond pass to propagate conflict copies...")
        sync_pass(state)
    if n == 0:
        print("Already in sync.")
    else:
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
