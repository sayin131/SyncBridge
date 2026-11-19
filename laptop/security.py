"""
security.py — self-signed TLS certificate generation and fingerprinting.

We don't use a CA-issued cert because SyncBridge never talks to the public internet —
it only ever talks to a phone that has pinned this exact certificate's fingerprint
during pairing. That pinning is what prevents a man-in-the-middle on shared Wi-Fi,
not the certificate's "trustworthiness" in the browser sense.
"""
import datetime
import hashlib
import ipaddress
import os
import socket

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def get_local_ip() -> str:
    """Best-effort LAN IP (doesn't actually send data)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def ensure_cert(cert_path="cert.pem", key_path="key.pem", days_valid=3650):
    """Create a self-signed cert if one doesn't already exist. Returns (cert_path, key_path)."""
    if os.path.exists(cert_path) and os.path.exists(key_path):
        return cert_path, key_path

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "SyncBridge Laptop"),
    ])

    local_ip = get_local_ip()
    san_entries = [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    try:
        san_entries.append(x509.IPAddress(ipaddress.ip_address(local_ip)))
    except ValueError:
        pass

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.utcnow())
        .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=days_valid))
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .sign(key, hashes.SHA256())
    )

    with open(key_path, "wb") as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    return cert_path, key_path


def cert_fingerprint(cert_path="cert.pem") -> str:
    """SHA-256 fingerprint of the DER-encoded cert, as the phone will pin it."""
    with open(cert_path, "rb") as f:
        cert = x509.load_pem_x509_certificate(f.read())
    der = cert.public_bytes(serialization.Encoding.DER)
    return hashlib.sha256(der).hexdigest()


if __name__ == "__main__":
    cert, key = ensure_cert()
    print(f"cert: {cert}")
    print(f"key:  {key}")
    print(f"fingerprint (sha256): {cert_fingerprint(cert)}")
    print(f"local ip: {get_local_ip()}")
