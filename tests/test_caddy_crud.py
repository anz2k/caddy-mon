"""Unit tests for Caddy Reverse-Proxy CRUD engine, validation, snapshots, and audit trail."""

import json
import os
import time
import tempfile
from unittest import mock
import pytest

from caddy_mon import db
from caddy_mon.caddy_crud import (
    validate_route_input,
    create_caddy_route,
    delete_caddy_route,
    rollback_caddy_config,
)


@pytest.fixture(autouse=True)
def temp_db():
    """Run each test with an isolated temporary SQLite database."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = os.path.join(tmpdir, "test_caddy_mon.db")
        with mock.patch.object(db, "DB_PATH", db_file):
            db.init_db()
            yield db_file


def test_validate_route_input():
    # Valid domain and upstream
    ok, err = validate_route_input("app.example.com", aliases=["www.app.example.com"], upstreams=["192.168.1.50:8080"])
    assert ok is True
    assert err == ""

    # Invalid primary host
    ok, err = validate_route_input("app..invalid", upstreams=["127.0.0.1:8000"])
    assert ok is False
    assert "Invalid primary hostname" in err

    # Invalid alias
    ok, err = validate_route_input("app.example.com", aliases=["bad alias with space"], upstreams=["127.0.0.1:8000"])
    assert ok is False
    assert "Invalid alias hostname" in err

    # Missing upstream
    ok, err = validate_route_input("app.example.com", upstreams=[])
    assert ok is False
    assert "upstream endpoint" in err

    # Invalid port
    ok, err = validate_route_input("app.example.com", upstreams=["127.0.0.1:99999"])
    assert ok is False
    assert "Invalid port number" in err

    # Path prefix must start with /
    ok, err = validate_route_input("app.example.com", upstreams=["127.0.0.1:8000"], path_prefix="api")
    assert ok is False
    assert "must start with '/'" in err


@pytest.mark.asyncio
async def test_create_caddy_route_success():
    fake_config = {
        "apps": {
            "http": {
                "servers": {
                    "srv0": {
                        "routes": []
                    }
                }
            }
        }
    }

    mock_resp = mock.MagicMock()
    mock_resp.status_code = 200

    with mock.patch("caddy_mon.caddy_crud._fetch_current_config", return_value=fake_config), \
         mock.patch("httpx.AsyncClient.post", return_value=mock_resp):
        res = await create_caddy_route(
            user="admin_test",
            primary_host="newapp.example.com",
            aliases=["alias.example.com"],
            upstreams=["192.168.1.60:3000"],
            path_prefix="/v1",
        )
        assert res["ok"] is True
        assert res["host"] == "newapp.example.com"

        # Verify audit log was recorded
        logs = db.get_audit_logs(limit=10)
        assert len(logs) >= 1
        assert logs[0]["action"] == "CREATE_ROUTE"
        assert logs[0]["user"] == "admin_test"
        assert logs[0]["host"] == "newapp.example.com"

        # Verify snapshot was created
        snaps = db.get_config_snapshots(limit=10)
        assert len(snaps) >= 1


@pytest.mark.asyncio
async def test_delete_caddy_route_success():
    fake_config = {
        "apps": {
            "http": {
                "servers": {
                    "srv0": {
                        "routes": [
                            {
                                "match": [{"host": ["delete-me.example.com"]}],
                                "handle": [{"handler": "reverse_proxy", "upstreams": [{"dial": "10.0.0.1:80"}]}],
                            }
                        ]
                    }
                }
            }
        }
    }

    mock_resp = mock.MagicMock()
    mock_resp.status_code = 200

    with mock.patch("caddy_mon.caddy_crud._fetch_current_config", return_value=fake_config), \
         mock.patch("httpx.AsyncClient.delete", return_value=mock_resp):
        res = await delete_caddy_route(
            user="admin_test",
            host="delete-me.example.com",
        )
        assert res["ok"] is True
        assert res["host"] == "delete-me.example.com"

        # Verify audit log
        logs = db.get_audit_logs(limit=10)
        assert len(logs) >= 1
        assert logs[0]["action"] == "DELETE_ROUTE"
        assert logs[0]["host"] == "delete-me.example.com"


@pytest.mark.asyncio
async def test_rollback_caddy_config_success():
    snap_id = db.save_config_snapshot(
        user="test_admin",
        description="Backup before test",
        config_json=json.dumps({"test": "ok"}),
    )

    mock_resp = mock.MagicMock()
    mock_resp.status_code = 200

    with mock.patch("caddy_mon.caddy_crud._fetch_current_config", return_value={"test": "current"}), \
         mock.patch("httpx.AsyncClient.post", return_value=mock_resp):
        res = await rollback_caddy_config(
            user="test_admin",
            snapshot_id=snap_id,
        )
        assert res["ok"] is True

        # Verify audit log
        logs = db.get_audit_logs(limit=10)
        assert len(logs) >= 1
        assert logs[0]["action"] == "ROLLBACK_CONFIG"
