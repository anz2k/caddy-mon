"""Optional HTTP Basic Authentication for caddy-mon admin interface."""

import secrets
from typing import Optional, Any

try:
    from fastapi import Depends, HTTPException, status
    from fastapi.security import HTTPBasic, HTTPBasicCredentials
except ImportError:
    # Allow imports in environments without fastapi (e.g. lightweight testing)
    Depends = lambda f: f  # type: ignore
    class HTTPException(Exception):  # type: ignore
        def __init__(self, status_code: int = 400, detail: str = "", headers: dict = None):
            super().__init__(f"{status_code}: {detail}")
            self.status_code = status_code
            self.detail = detail
            self.headers = headers
    status = object  # type: ignore
    HTTPBasic = lambda **kwargs: None  # type: ignore
    HTTPBasicCredentials = None  # type: ignore

from .config import AUTH_USER, AUTH_PASSWORD

security = HTTPBasic(auto_error=False) if callable(HTTPBasic) else None


def require_auth(credentials: Optional[Any] = Depends(security) if security else None):
    """Enforce Basic Auth if AUTH_USER and AUTH_PASSWORD are configured."""
    if not (AUTH_USER and AUTH_PASSWORD):
        return True

    if not credentials:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Basic realm='caddy-mon'"},
        )

    username_match = secrets.compare_digest(
        getattr(credentials, "username", "").encode("utf-8"),
        AUTH_USER.encode("utf-8"),
    )
    password_match = secrets.compare_digest(
        getattr(credentials, "password", "").encode("utf-8"),
        AUTH_PASSWORD.encode("utf-8"),
    )

    if not (username_match and password_match):
        raise HTTPException(
            status_code=401,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic realm='caddy-mon'"},
        )

    return getattr(credentials, "username", "admin")
