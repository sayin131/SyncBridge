"""
run_server.py — entry point. Generates the TLS cert if needed, prints/saves a pairing QR,
and starts the FastAPI app over HTTPS.

Usage:
    python run_server.py [--port 8443] [--root ./sync_root] [--no-qr]
"""
import argparse
import os

import db
import pairing_qr
import security


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8443)
    ap.add_argument("--root", type=str, default="./sync_root")
    ap.add_argument("--no-qr", action="store_true")
    args = ap.parse_args()

    os.environ["SYNCBRIDGE_ROOT"] = os.path.abspath(args.root)
    os.makedirs(args.root, exist_ok=True)

    cert_path, key_path = security.ensure_cert()
    fp = security.cert_fingerprint(cert_path)
    ip = security.get_local_ip()

    print("=" * 60)
    print("SyncBridge Laptop Server")
    print(f"  Sync root:   {os.path.abspath(args.root)}")
    print(f"  Local IP:    {ip}")
    print(f"  Port:        {args.port}")
    print(f"  Cert SHA256: {fp}")
    print("=" * 60)

    if not args.no_qr:
        conn = db.connect()
        db.init_db(conn)
        payload = pairing_qr.make_pairing_payload(conn, ip, args.port, cert_path)
        png_path = pairing_qr.save_qr_png(payload)
        print(f"Pairing QR saved to: {png_path}")
        print("Pairing payload (for phone_sim, or manual pairing):")
        import json
        print(json.dumps(payload, indent=2))
        print("Valid for 120 seconds. Re-run with a fresh invocation to regenerate.")
        print("=" * 60)

    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=args.port,
        ssl_certfile=cert_path,
        ssl_keyfile=key_path,
    )


if __name__ == "__main__":
    main()
