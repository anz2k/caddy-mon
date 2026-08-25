"""Unit tests for maintenance mode and alert suppression."""

import os
import tempfile
import time
from unittest import mock
import pytest

from caddy_mon import db, alerts


@pytest.fixture(autouse=True)
def temp_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = os.path.join(tmpdir, "test_maint.db")
        with mock.patch.object(db, "DB_PATH", db_file):
            db.init_db()
            yield db_file


def test_set_and_get_maintenance():
    # Initial status -> None
    assert db.get_maintenance_status("app.example.ee") is None

    # Enable maintenance
    now = time.time()
    db.set_maintenance("app.example.ee", enabled=True, reason="Upgrading database", now=now)
    m = db.get_maintenance_status("app.example.ee")
    assert m is not None
    assert m["enabled"] is True
    assert m["reason"] == "Upgrading database"

    # All maintenance mapping
    all_m = db.get_all_maintenance()
    assert "app.example.ee" in all_m

    # Disable maintenance
    db.set_maintenance("app.example.ee", enabled=False)
    assert db.get_maintenance_status("app.example.ee") is None
    assert "app.example.ee" not in db.get_all_maintenance()


@pytest.mark.asyncio
async def test_alerts_suppressed_during_maintenance():
    now = time.time()
    host = "maint.example.ee"
    db.set_maintenance(host, enabled=True, reason="Scheduled OS upgrade", now=now)

    sites_alive = [{"primary_host": host, "alive": True, "latency_ms": 10.0}]
    sites_dead = [{"primary_host": host, "alive": False, "latency_ms": 0.0, "upstreams": []}]

    with mock.patch.object(alerts, "TELEGRAM_BOT_TOKEN", "fake-token"), \
         mock.patch.object(alerts, "TELEGRAM_CHAT_ID", "12345"), \
         mock.patch.object(alerts, "dispatch_alert", new_callable=mock.AsyncMock) as mock_dispatch:

        # Step 1: Initial check sets baseline state
        await alerts.process_site_alerts(sites_alive, now=now)
        # Step 2: Site goes dead while in maintenance -> Alert should be SUPPRESSED (0 dispatches)
        await alerts.process_site_alerts(sites_dead, now=now + 10)
        assert mock_dispatch.call_count == 0
