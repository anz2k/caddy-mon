"""Unit tests for caddy_mon.tls_source (cert parsing + site matching)."""

import os
import time
import datetime
import tempfile
from unittest import mock

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption

from caddy_mon import tls_source
from caddy_mon.caddy_source import _site_tls


def _make_cert(hostname: str, days_valid: int, tmpdir: str) -> str:
    """Generate a self-signed cert PEM for `hostname`, save to tmpdir, return path."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.datetime.utcnow()
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)]))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=days_valid))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(hostname)]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    path = os.path.join(tmpdir, f"{hostname}.crt")
    with open(path, "wb") as f:
        f.write(cert.public_bytes(Encoding.PEM))
    return path


def test_cert_status_reads_valid_cert():
    with tempfile.TemporaryDirectory() as d:
        _make_cert("idm.example.com", days_valid=358, tmpdir=d)
        with mock.patch.object(tls_source, "CERT_DIR", d):
            entries = tls_source.cert_status()
    assert len(entries) == 1
    e = entries[0]
    assert e["hosts"] == ["idm.example.com"]
    assert e["days_left"] > 300
    assert e["warn"] is False


def test_cert_status_warns_when_close_to_expiry():
    with tempfile.TemporaryDirectory() as d:
        _make_cert("soon.example.com", days_valid=10, tmpdir=d)
        with mock.patch.object(tls_source, "CERT_DIR", d):
            entries = tls_source.cert_status()
    assert len(entries) == 1
    assert entries[0]["days_left"] <= 30
    assert entries[0]["warn"] is True


def test_cert_status_empty_when_no_dir():
    with mock.patch.object(tls_source, "CERT_DIR", "/nonexistent/path/xyz"):
        entries = tls_source.cert_status()
    assert entries == []


def test_cert_status_skips_bad_pem():
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "broken.crt"), "w") as f:
            f.write("not a real cert")
        _make_cert("ok.example.com", days_valid=100, tmpdir=d)
        with mock.patch.object(tls_source, "CERT_DIR", d):
            entries = tls_source.cert_status()
    # broken.crt is skipped, ok.example.com is parsed
    assert len(entries) == 1
    assert entries[0]["hosts"] == ["ok.example.com"]


def test_site_tls_matches_by_san():
    fake = [{
        "hosts": ["idm.example.com", "idm.example.lan"],
        "days_left": 200,
        "warn": False,
    }]
    with mock.patch.object(tls_source, "cert_status", return_value=fake):
        result = _site_tls(["idm.example.com"])
    assert result == {"days_left": 200, "warn": False}


def test_site_tls_returns_none_when_no_match():
    fake = [{"hosts": ["other.ee"], "days_left": 100, "warn": False}]
    with mock.patch.object(tls_source, "cert_status", return_value=fake):
        result = _site_tls(["idm.example.com"])
    assert result is None


def test_site_tls_picks_soonest_expiring():
    fake = [
        {"hosts": ["a.ee"], "days_left": 300, "warn": False},
        {"hosts": ["a.ee", "b.ee"], "days_left": 10, "warn": True},
    ]
    with mock.patch.object(tls_source, "cert_status", return_value=fake):
        result = _site_tls(["b.ee"])
    # should pick the cert that covers b.ee (10 days), not a.ee (300 days)
    assert result == {"days_left": 10, "warn": True}
