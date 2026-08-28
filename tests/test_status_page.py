"""Unit tests for public status page and sanitization."""

import pytest
from unittest import mock

from caddy_mon import status_page


def test_is_private_host():
    assert status_page._is_private_host("idm.example.lan") is True
    assert status_page._is_private_host("test.local") is True
    assert status_page._is_private_host("localhost") is True
    assert status_page._is_private_host("192.168.1.1") is True
    assert status_page._is_private_host("10.0.0.5") is True

    assert status_page._is_private_host("example.ee") is False
    assert status_page._is_private_host("sub.example.com") is False


def test_get_public_services_sanitizes_internal_hosts_and_dials():
    fake_sites = [
        {
            "primary_host": "public.example.ee",
            "group": "example.ee",
            "alive": True,
            "latency_ms": 15.0,
            "uptime_24h": 99.8,
            "sparkline": [10.0, 15.0],
            "upstreams": [{"upstream": "192.168.1.50:8080", "caddy_healthy": True}],  # Sensitive dial
        },
        {
            "primary_host": "internal.example.lan",
            "group": "example.lan",
            "alive": True,
            "latency_ms": 5.0,
            "uptime_24h": 100.0,
            "sparkline": [5.0],
            "upstreams": [{"upstream": "192.168.1.51:8080"}],
        },
    ]

    with mock.patch.object(status_page, "_state", {"sites": fake_sites, "last_update": 12345.0}), \
         mock.patch("caddy_mon.status_page.get_all_maintenance", return_value={}):

        services = status_page.get_public_services()
        # Internal host must be filtered out
        assert len(services) == 1
        s = services[0]
        assert s["service"] == "public.example.ee"
        assert s["operational"] is True
        assert s["uptime_24h"] == 99.8

        # Verify no internal dials are present in the public structure
        assert "upstreams" not in s
        assert "192.168.1.50" not in str(s)


def test_api_status_overall_system_health():
    fake_sites = [
        {"primary_host": "a.example.ee", "alive": True, "latency_ms": 10.0, "uptime_24h": 100.0},
        {"primary_host": "b.example.ee", "alive": True, "latency_ms": 12.0, "uptime_24h": 100.0},
    ]

    with mock.patch.object(status_page, "_state", {"sites": fake_sites, "last_update": 12345.0}), \
         mock.patch("caddy_mon.status_page.get_all_maintenance", return_value={}), \
         mock.patch("caddy_mon.status_page.get_recent_incidents", return_value=[]):

        res = status_page.api_status()
        assert res["status"] == "operational"
        assert res["message"] == "All Systems Operational"
        assert len(res["services"]) == 2
