"""Unit tests for security analytics and client IP classification."""

import time
from unittest import mock

from caddy_mon import security_page


def test_is_lan_ip():
    assert security_page.is_lan_ip("192.168.1.50") is True
    assert security_page.is_lan_ip("10.0.0.1") is True
    assert security_page.is_lan_ip("127.0.0.1") is True
    assert security_page.is_lan_ip("172.16.0.1") is True
    assert security_page.is_lan_ip("172.31.255.255") is True
    assert security_page.is_lan_ip("::1") is True

    assert security_page.is_lan_ip("8.8.8.8") is False
    assert security_page.is_lan_ip("1.1.1.1") is False
    assert security_page.is_lan_ip("172.32.0.1") is False


def test_security_analytics_aggregates_top_clients_and_status_codes():
    now = time.time()
    fake_log_cache = [
        # Client 1: LAN IP, 200 OK
        {
            "ts": now - 10,
            "host": "pilv.lope.ee",
            "client_ip": "192.168.1.100",
            "status": 200,
            "method": "GET",
            "uri": "/files",
        },
        # Client 1: LAN IP, 200 OK
        {
            "ts": now - 8,
            "host": "pilv.lope.ee",
            "client_ip": "192.168.1.100",
            "status": 200,
            "method": "GET",
            "uri": "/avatar.png",
        },
        # Client 2: WAN IP, 404 Not Found (Scanning)
        {
            "ts": now - 5,
            "host": "www.lope.ee",
            "client_ip": "203.0.113.195",
            "status": 404,
            "method": "GET",
            "uri": "/wp-login.php",
        },
        # Client 2: WAN IP, 429 Rate Limit
        {
            "ts": now - 2,
            "host": "www.lope.ee",
            "client_ip": "203.0.113.195",
            "status": 429,
            "method": "POST",
            "uri": "/login",
        },
    ]

    with mock.patch.object(security_page, "_LOG_CACHE", fake_log_cache), \
         mock.patch("caddy_mon.security_page.ingest_logs"):
        res = security_page.security_analytics(window=3600)
        assert res["total_requests"] == 4
        dist = res["status_distribution"]
        assert dist["2xx"] == 2
        assert dist["4xx"] == 2
        assert dist["429"] == 1

        top_clients = res["top_clients"]
        assert len(top_clients) == 2
        assert top_clients[0]["requests"] == 2

        suspicious = res["suspicious_requests"]
        assert len(suspicious) == 2
        assert suspicious[0]["status"] == 404
        assert suspicious[1]["status"] == 429
