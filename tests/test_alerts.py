"""Unit tests for caddy_mon.alerts notification and transition logic."""

import os
import tempfile
import time
from unittest import mock
import pytest

from caddy_mon import db, alerts


@pytest.fixture(autouse=True)
def temp_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = os.path.join(tmpdir, "test_alerts.db")
        with mock.patch.object(db, "DB_PATH", db_file):
            db.init_db()
            yield db_file


@pytest.mark.asyncio
async def test_alert_dispatched_on_site_down():
    now = time.time()
    # First state: Site is ALIVE
    sites_alive = [{"primary_host": "mail.example.com", "alive": True, "latency_ms": 10.0, "upstreams": []}]

    with mock.patch.object(alerts, "TELEGRAM_BOT_TOKEN", "fake-token"), \
         mock.patch.object(alerts, "TELEGRAM_CHAT_ID", "12345"), \
         mock.patch.object(alerts, "dispatch_alert", new_callable=mock.AsyncMock) as mock_dispatch:

        # Step 1: Initial check sets baseline state
        await alerts.process_site_alerts(sites_alive, now=now)
        assert mock_dispatch.call_count == 0

        # Step 2: Site transitions to DEAD
        sites_dead = [{
            "primary_host": "mail.example.com",
            "alive": False,
            "latency_ms": 0.0,
            "upstreams": [{"upstream": "192.168.1.10:80", "probe_ok": False, "error": "Connection refused"}],
        }]
        await alerts.process_site_alerts(sites_dead, now=now + 10)
        assert mock_dispatch.call_count == 1
        call_args = mock_dispatch.call_args[0]
        assert "DOWN" in call_args[0]
        assert "mail.example.com" in call_args[0]

        # Verify incident was logged in DB
        incidents = db.get_recent_incidents()
        assert len(incidents) == 1
        assert incidents[0]["host"] == "mail.example.com"
        assert incidents[0]["event_type"] == "DOWN"


@pytest.mark.asyncio
async def test_alert_dispatched_on_site_recovered():
    now = time.time()
    sites_dead = [{"primary_host": "mail.example.com", "alive": False, "latency_ms": 0.0}]

    with mock.patch.object(alerts, "TELEGRAM_BOT_TOKEN", "fake-token"), \
         mock.patch.object(alerts, "TELEGRAM_CHAT_ID", "12345"), \
         mock.patch.object(alerts, "dispatch_alert", new_callable=mock.AsyncMock) as mock_dispatch:

        # Initial dead state registered
        await alerts.process_site_alerts(sites_dead, now=now)

        # Transitions to ALIVE
        sites_alive = [{"primary_host": "mail.example.com", "alive": True, "latency_ms": 12.5}]
        await alerts.process_site_alerts(sites_alive, now=now + 10)

        assert mock_dispatch.call_count == 1
        call_args = mock_dispatch.call_args[0]
        assert "RECOVERED" in call_args[0]
        assert "mail.example.com" in call_args[0]


@pytest.mark.asyncio
async def test_alert_throttled_by_cooldown():
    now = time.time()
    sites_alive = [{"primary_host": "mail.example.com", "alive": True, "latency_ms": 10.0}]
    sites_dead = [{"primary_host": "mail.example.com", "alive": False, "latency_ms": 0.0}]

    with mock.patch.object(alerts, "TELEGRAM_BOT_TOKEN", "fake-token"), \
         mock.patch.object(alerts, "TELEGRAM_CHAT_ID", "12345"), \
         mock.patch.object(alerts, "ALERT_COOLDOWN_MINUTES", 15), \
         mock.patch.object(alerts, "dispatch_alert", new_callable=mock.AsyncMock) as mock_dispatch:

        await alerts.process_site_alerts(sites_alive, now=now)
        await alerts.process_site_alerts(sites_dead, now=now + 10)
        assert mock_dispatch.call_count == 1

        # Another check 1 minute later (within 15m cooldown) should NOT dispatch duplicate alert
        await alerts.process_site_alerts(sites_dead, now=now + 70)
        assert mock_dispatch.call_count == 1
