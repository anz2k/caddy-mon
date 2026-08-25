"""Unit tests for caddy_mon.db SQLite persistence module."""

import os
import tempfile
import time
from unittest import mock
import pytest

from caddy_mon import db


@pytest.fixture(autouse=True)
def temp_db():
    """Run each test with an isolated temporary SQLite database."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = os.path.join(tmpdir, "test_caddy_mon.db")
        with mock.patch.object(db, "DB_PATH", db_file):
            db.init_db()
            yield db_file


def test_init_db_creates_tables():
    with db.get_connection() as conn:
        tables = [
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        assert "site_snapshots" in tables
        assert "incident_events" in tables
        assert "alert_state" in tables


def test_record_snapshot_and_uptime():
    now = time.time()
    fake_sites = [
        {"primary_host": "app.example.ee", "alive": True, "latency_ms": 15.2, "upstreams": [{"status": 200}]},
        {"primary_host": "api.example.ee", "alive": False, "latency_ms": 0.0, "upstreams": [{"status": 502}]},
    ]
    # Record 3 alive snapshots for app.example.ee, 1 dead
    db.record_snapshot(fake_sites, now=now - 300)
    db.record_snapshot(fake_sites, now=now - 200)
    db.record_snapshot(fake_sites, now=now - 100)

    # 3 out of 3 alive -> 100.0%
    app_uptime = db.get_site_uptime_24h("app.example.ee", now=now)
    assert app_uptime == 100.0

    # 0 out of 3 alive -> 0.0%
    api_uptime = db.get_site_uptime_24h("api.example.ee", now=now)
    assert api_uptime == 0.0

    # Unknown host -> None
    assert db.get_site_uptime_24h("unknown.ee", now=now) is None


def test_get_site_sparkline():
    now = time.time()
    # Record snapshots with varying latencies
    site_alive = [{"primary_host": "app.example.ee", "alive": True, "latency_ms": 20.0}]
    site_slower = [{"primary_host": "app.example.ee", "alive": True, "latency_ms": 40.0}]

    db.record_snapshot(site_alive, now=now - 3600 * 2)
    db.record_snapshot(site_slower, now=now - 3600 * 1)

    sparkline = db.get_site_sparkline("app.example.ee", hours=24, points=12, now=now)
    assert len(sparkline) == 12
    assert isinstance(sparkline, list)
    assert any(p > 0.0 for p in sparkline)


def test_record_and_get_incidents():
    now = time.time()
    inc_id = db.record_incident("app.example.ee", "DOWN", "Connection refused: 192.168.1.10:80", ts=now)
    assert inc_id > 0

    incidents = db.get_recent_incidents(limit=10)
    assert len(incidents) == 1
    assert incidents[0]["host"] == "app.example.ee"
    assert incidents[0]["event_type"] == "DOWN"
    assert "Connection refused" in incidents[0]["details"]


def test_prune_old_history():
    now = time.time()
    old_ts = now - (10 * 86400)  # 10 days ago
    recent_ts = now - 3600        # 1 hour ago

    db.record_snapshot([{"primary_host": "app.example.ee", "alive": True, "latency_ms": 10.0}], now=old_ts)
    db.record_snapshot([{"primary_host": "app.example.ee", "alive": True, "latency_ms": 10.0}], now=recent_ts)

    with db.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS c FROM site_snapshots").fetchone()["c"] == 2

    # Prune older than 7 days
    db.prune_old_history(days=7, now=now)

    with db.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS c FROM site_snapshots").fetchone()["c"] == 1
