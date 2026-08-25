"""Unit tests for Caddy control plane and configuration inspector."""

import pytest
from unittest import mock
import httpx

from caddy_mon import caddy_control


@pytest.mark.asyncio
async def test_get_caddy_raw_config_success():
    fake_config = {"apps": {"http": {"servers": {"srv0": {}}}}}
    fake_resp = httpx.Response(
        status_code=200,
        json=fake_config,
        request=httpx.Request("GET", "http://caddy-proxy:2019/config/"),
    )
    with mock.patch("httpx.AsyncClient.get", return_value=fake_resp):
        res = await caddy_control.get_caddy_raw_config()
        assert "apps" in res
        assert "http" in res["apps"]


@pytest.mark.asyncio
async def test_get_caddy_raw_config_error_handling():
    with mock.patch("httpx.AsyncClient.get", side_effect=httpx.ConnectError("Cannot connect")):
        res = await caddy_control.get_caddy_raw_config()
        assert "error" in res


@pytest.mark.asyncio
async def test_reload_caddy_success():
    fake_config = {"apps": {}}
    fake_get_resp = httpx.Response(
        status_code=200,
        json=fake_config,
        request=httpx.Request("GET", "http://caddy-proxy:2019/config/"),
    )
    fake_post_resp = httpx.Response(
        status_code=200,
        request=httpx.Request("POST", "http://caddy-proxy:2019/load"),
    )

    with mock.patch("httpx.AsyncClient.get", return_value=fake_get_resp), \
         mock.patch("httpx.AsyncClient.post", return_value=fake_post_resp):
        res = await caddy_control.reload_caddy()
        assert res["ok"] is True
        assert "reloaded" in res["message"].lower()
