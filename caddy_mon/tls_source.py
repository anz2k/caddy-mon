"""TLS certificate expiry monitoring.

Reads certificate files mounted at /caddy-certs and /caddy-data (Caddy ACME storage),
parses each PEM, and reports not_after + days_left per SAN/hostname.
"""

import os
import glob
import time
from typing import List, Dict, Any

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
except ImportError:
    x509 = None  # type: ignore

CERT_DIR = "/caddy-certs"
CERT_DIRS = [CERT_DIR, "/caddy-data", "/data/caddy/certificates"]
WARN_DAYS = 30


def _load_certs():
    """Yield (cert_obj, san_list, not_after, source_path) for discovered certificates."""
    if x509 is None:
        return

    # Check CERT_DIR first (in case it was patched by tests) plus any other dirs
    search_dirs = [CERT_DIR] + [d for d in CERT_DIRS if d != CERT_DIR]
    seen_paths = set()
    for root_dir in search_dirs:
        if not os.path.isdir(root_dir):
            continue
        for root, _, files in os.walk(root_dir):
            for file in files:
                if file.endswith((".crt", ".pem")) and not file.endswith((".key", "privkey.pem", "key.pem")):
                    path = os.path.join(root, file)
                    if path in seen_paths:
                        continue
                    seen_paths.add(path)
                    try:
                        with open(path, "rb") as f:
                            data = f.read()
                        # Only parse if it looks like a certificate PEM
                        if b"BEGIN CERTIFICATE" not in data:
                            continue
                        cert = x509.load_pem_x509_certificate(data)
                    except Exception:
                        continue

                    sans = []
                    try:
                        ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
                        sans = ext.value.get_values_for_type(x509.DNSName)
                    except Exception:
                        try:
                            sans = [cert.subject.rfc4514_string()]
                        except Exception:
                            sans = []

                    not_after = cert.not_valid_after_utc
                    yield cert, sans, not_after, path


def cert_status() -> List[Dict[str, Any]]:
    """Return list of cert entries with expiry info sorted by days_left ascending."""
    now = time.time()
    entries = []
    seen_sans = set()

    for _, sans, not_after, path in _load_certs():
        san_key = tuple(sorted(sans))
        if san_key in seen_sans and sans:
            continue
        if sans:
            seen_sans.add(san_key)

        days_left = int((not_after.timestamp() - now) / 86400)
        issuer_str = "ACME" if "acme" in path.lower() or "certificates" in path.lower() else "Local / Custom"

        entries.append({
            "file": os.path.basename(path),
            "path": path,
            "hosts": sans,
            "not_after": not_after.isoformat(),
            "days_left": days_left,
            "warn": days_left <= WARN_DAYS,
            "issuer": issuer_str,
        })

    entries.sort(key=lambda e: e["days_left"])
    return entries
