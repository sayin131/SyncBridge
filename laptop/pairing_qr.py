"""
pairing_qr.py — generates the QR code the phone scans to pair.

The QR payload is JSON containing everything the phone needs to make first contact
and verify it's talking to the right laptop, without ever typing an IP address:
  ip, port, cert fingerprint (for pinning), one-time pairing secret.
"""
import json
import secrets

import qrcode

import db
import security


def make_pairing_payload(conn, ip: str, port: int, cert_path="cert.pem", ttl_seconds=120) -> dict:
    fp = security.cert_fingerprint(cert_path)
    secret = secrets.token_hex(16)
    db.register_pairing_secret(conn, secret)
    return {
        "ip": ip,
        "port": port,
        "fp": fp,
        "secret": secret,
        "ttl": ttl_seconds,
    }


def save_qr_png(payload: dict, out_path="pairing_qr.png"):
    img = qrcode.make(json.dumps(payload))
    img.save(out_path)
    return out_path


if __name__ == "__main__":
    conn = db.connect()
    db.init_db(conn)
    cert_path, _ = security.ensure_cert()
    ip = security.get_local_ip()
    payload = make_pairing_payload(conn, ip, 8443, cert_path)
    path = save_qr_png(payload)
    print(f"Pairing QR saved to {path}")
    print(json.dumps(payload, indent=2))
    print("\nScan this within 120 seconds, or re-run this script for a fresh code.")
