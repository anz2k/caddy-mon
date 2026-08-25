"""TLS certificate expiry monitoring.

Reads certificate files mounted at /caddy-certs (from the Caddy cert dir),
parses each PEM, and reports not_after + days_left per SAN/hostname.
"""

import os
import glob
import time
from cryptography import x509
from cryptography.hazmat.primitives import serialization

from .config import TZ

CERT_DIR = "/caddy-certs"
# Warn when fewer than this many days remain.
WARN_DAYS = 30


def _load_certs():
    """Yield (cert_obj, san_list, not_after) for each cert file in CERT_DIR."""
    if not os.path.isdir(CERT_DIR):
        return
    for path in sorted(glob.glob(os.path.join(CERT_DIR, "*.crt"))):
        try:
            with open(path, "rb") as f:
                data = f.read()
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
        yield cert, sans, cert.not_valid_after_utc


def cert_status():
    """Return list of cert entries with expiry info.

    Each entry: {"file", "hosts", "not_after", "days_left", "warn"}.
    Sorted by days_left ascending (most urgent first).
    """
    now = time.time()
    entries = []
    for cert, sans, not_after in _load_certs():
        days_left = int((not_after.timestamp() - now) / 86400)
        entries.append({
            "file": "",  # filled by caller if needed
            "hosts": sans,
            "not_after": not_after.isoformat(),
            "days_left": days_left,
            "warn": days_left <= WARN_DAYS,
        })
    entries.sort(key=lambda e: e["days_left"])
    return entries
