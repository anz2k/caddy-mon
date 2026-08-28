"""Unit tests for caddy_mon.auth module."""

import pytest
from unittest import mock
from types import SimpleNamespace

from caddy_mon import auth


def test_auth_disabled_when_empty():
    with mock.patch.object(auth, "AUTH_USER", ""), \
         mock.patch.object(auth, "AUTH_PASSWORD", ""):
        assert auth.require_auth(None) is True


def test_auth_success_with_valid_credentials():
    with mock.patch.object(auth, "AUTH_USER", "admin"), \
         mock.patch.object(auth, "AUTH_PASSWORD", "testpassword123"):
        creds = SimpleNamespace(username="admin", password="testpassword123")
        assert auth.require_auth(creds) == "admin"


def test_auth_fails_with_invalid_credentials():
    with mock.patch.object(auth, "AUTH_USER", "admin"), \
         mock.patch.object(auth, "AUTH_PASSWORD", "testpassword123"):
        creds = SimpleNamespace(username="admin", password="wrongpassword123")
        with pytest.raises(Exception) as exc_info:
            auth.require_auth(creds)
        # Should raise 401
        assert "401" in str(exc_info.value) or "credentials" in str(exc_info.value).lower()


def test_auth_fails_when_no_credentials_provided():
    with mock.patch.object(auth, "AUTH_USER", "admin"), \
         mock.patch.object(auth, "AUTH_PASSWORD", "testpassword123"):
        with pytest.raises(Exception):
            auth.require_auth(None)
