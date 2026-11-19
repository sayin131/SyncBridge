"""
security_sim.py — emulates certificate pinning the way the real Android app would with
OkHttp's CertificatePinner: before trusting the server, fetch its certificate directly
over a raw TLS socket and compare its SHA-256 fingerprint against the one we pinned
during pairing (from the QR code). If it doesn't match, refuse to proceed — this is
what defeats a man-in-the-middle on shared Wi-Fi.

Note: for the actual HTTP requests after pinning passes, this simulator uses
`verify=False` on the `requests` session since the cert is self-signed and we've
already done our own trust check. A real client should still do this check at the
TLS handshake itself (which is what CertificatePinner does) rather than as a
separate step — this simulator approximates that for the sake of being runnable
from plain Python without extra native dependencies.
"""
import hashlib
import ssl
import socket


def fetch_server_fingerprint(ip: str, port: int, timeout=5) -> str:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # we're about to verify it ourselves, manually

    with socket.create_connection((ip, port), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=ip) as tls_sock:
            der_cert = tls_sock.getpeercert(binary_form=True)
    return hashlib.sha256(der_cert).hexdigest()


def verify_pin(ip: str, port: int, expected_fp: str) -> bool:
    actual = fetch_server_fingerprint(ip, port)
    return actual.lower() == expected_fp.lower()
