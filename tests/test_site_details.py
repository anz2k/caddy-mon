"""Unit tests for Phase 4: Site deep-dive details, extended history, recent host logs, and ACME cert scanning."""

import os
import time
import tempfile
from unittest import mock
import pytest

from caddy_mon import db, log_source, tls_source


@pytest.fixture(autouse=True)
def temp_db():
    """Run each test with an isolated temporary SQLite database."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = os.path.join(tmpdir, "test_caddy_mon.db")
        with mock.patch.object(db, "DB_PATH", db_file):
            db.init_db()
            yield db_file


def test_get_host_extended_history_and_incidents():
    now = time.time()
    fake_host = "test.example.com"

    # Record some snapshots
    db.record_snapshot([{"primary_host": fake_host, "alive": True, "latency_ms": 45.0}], now=now - 300)
    db.record_snapshot([{"primary_host": fake_host, "alive": True, "latency_ms": 65.0}], now=now - 200)
    db.record_snapshot([{"primary_host": fake_host, "alive": True, "latency_ms": 25.0}], now=now - 100)

    # Record an incident
    db.record_incident(fake_host, event_type="DOWN", details="Connection timeout", ts=now - 500)

    # Test extended history
    hist = db.get_host_extended_history(fake_host, now=now)
    assert hist["host"] == fake_host
    assert hist["sample_count"] == 3
    assert hist["min_latency_ms"] == 25.0
    assert hist["max_latency_ms"] == 65.0
    assert hist["avg_latency_ms"] == 45.0
    assert len(hist["sparkline_24h"]) == 24
    assert len(hist["sparkline_7d"]) == 28

    # Test host incidents
    incidents = db.get_host_incidents(fake_host)
    assert len(incidents) >= 1
    assert incidents[0]["event_type"] == "DOWN"


def test_get_host_recent_logs():
    now = time.time()
    fake_logs = [
        {"ts": now - 30, "host": "mail.example.com", "uri": "/inbox", "method": "GET", "client_ip": "192.168.1.10", "status": 200},
        {"ts": now - 20, "host": "autoconfig.example.com", "uri": "/config", "method": "GET", "client_ip": "192.168.1.10", "status": 200},
        {"ts": now - 10, "host": "other.domain.ee", "uri": "/", "method": "GET", "client_ip": "8.8.8.8", "status": 200},
    ]

    with mock.patch.object(log_source, "_LOG_CACHE", fake_logs), \
         mock.patch("caddy_mon.log_source.ingest_logs"):
        res = log_source.get_host_recent_logs(["mail.example.com", "autoconfig.example.com"], limit=10)
        assert len(res) == 2
        assert res[0]["host"] == "autoconfig.example.com"
        assert res[1]["host"] == "mail.example.com"
