"""Unit tests for on-demand diagnostic probing."""

import pytest
from unittest import mock
import httpx

from caddy_mon import diagnostics


@pytest.mark.asyncio
async def test_run_detailed_probe_success():
    fake_response = httpx.Response(
        status_code=200,
        headers={"server": "Caddy", "content-type": "text/html"},
        request=httpx.Request("GET", "http://192.168.1.10:80"),
    )

    with mock.patch("httpx.AsyncClient.get", return_value=fake_response):
        res = await diagnostics._run_detailed_probe("192.168.1.10:80")
        assert res["ok"] is True
        assert res["status_code"] == 200
        assert res["headers"]["server"] == "Caddy"
        assert res["error"] is None


@pytest.mark.asyncio
async def test_run_detailed_probe_connection_refused():
    with mock.patch("httpx.AsyncClient.get", side_effect=httpx.ConnectError("Connection refused")):
        res = await diagnostics._run_detailed_probe("192.168.1.10:80")
        assert res["ok"] is False
        assert res["status_code"] == 0
        assert "Connection refused" in res["error"]


@pytest.mark.asyncio
async def test_probe_host_detailed():
    with mock.patch.object(diagnostics, "_state", {
        "sites": [{
            "primary_host": "app.example.ee",
            "hosts": ["app.example.ee"],
            "upstreams": [{"upstream": "192.168.1.10:80", "probe_ok": True}],
        }]
    }):
        fake_probe_result = {
            "upstream": "192.168.1.10:80",
            "ok": True,
            "status_code": 200,
            "latency_ms": 14.2,
            "headers": {},
            "error": None,
        }
        with mock.patch("caddy_mon.diagnostics._run_detailed_probe", return_value=fake_probe_result):
            res = await diagnostics.probe_host_detailed("app.example.ee")
            assert res["host"] == "app.example.ee"
            assert res["ok"] is True
            assert res["latency_ms"] == 14.2
            assert len(res["upstreams"]) == 1
