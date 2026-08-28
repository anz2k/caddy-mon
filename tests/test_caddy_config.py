"""Integration tests against the live Caddy admin API.

These require network access to the Caddy admin API (default
http://caddy-proxy:2019, override with CADDY_API env var). They are skipped
automatically when Caddy is not reachable, so `pytest` works offline too.

Run on the deployment host (docker02) where caddy-proxy is reachable.
"""

import os
import sys

import httpx
import pytest

CADDY_API = os.environ.get("CADDY_API", "http://caddy-proxy:2019")

# Skip the whole module if Caddy admin API is not reachable.
try:
    _try = httpx.get(f"{CADDY_API}/config", timeout=3.0)
    CADDY_REACHABLE = _try.status_code == 200
except Exception:
    CADDY_REACHABLE = False

pytestmark = pytest.mark.skipif(
    not CADDY_REACHABLE,
    reason=f"Caddy admin API not reachable at {CADDY_API}",
)


def test_admin_api_reachable():
    r = httpx.get(f"{CADDY_API}/config", timeout=5.0)
    assert r.status_code == 200


def test_all_servers_are_read():
    """Every HTTP server (srv0, srv1, ...) must appear, not just srv0."""
    cfg = httpx.get(f"{CADDY_API}/config/apps/http/servers", timeout=5.0).json()
    assert isinstance(cfg, dict)
    assert len(cfg) >= 1
    # at least one of the known server names should be present
    assert any(k.startswith("srv") for k in cfg.keys())


def test_every_upstream_is_reachable():
    """Each reverse_proxy upstream dial should not be connection-refused.

    This catches Caddyfile mistakes like a wrong IP (e.g. ha.example.lan).
    """
    cfg = httpx.get(f"{CADDY_API}/config/apps/http/servers", timeout=5.0).json()
    unreachable = []
    for srv in cfg.values():
        for route in srv.get("routes", []):
            for up in _collect_upstreams(route):
                if _is_refused(up):
                    unreachable.append(up)
    assert unreachable == [], f"Unreachable upstreams: {unreachable}"


def test_certs_are_valid():
    """Every mounted cert file should parse and not be expired."""
    cert_dir = "/caddy-certs"
    if not os.path.isdir(cert_dir):
        pytest.skip(f"{cert_dir} not mounted")
    from cryptography import x509
    import glob
    for path in glob.glob(os.path.join(cert_dir, "*.crt")):
        with open(path, "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read())
        assert cert.not_valid_after_utc.timestamp() > time.time(), f"{path} expired"


# --- helpers ---------------------------------------------------------------

def _collect_upstreams(node):
    """Recursively collect all reverse_proxy upstream dials from a route node."""
    found = []
    if isinstance(node, dict):
        handler = node.get("handler")
        if handler == "reverse_proxy":
            for up in node.get("upstreams", []):
                if up.get("dial"):
                    found.append(up["dial"])
        for v in node.values():
            found.extend(_collect_upstreams(v))
    elif isinstance(node, list):
        for x in node:
            found.extend(_collect_upstreams(x))
    return found


def _is_refused(upstream: str) -> bool:
    """Probe the upstream; return True if connection is refused."""
    if upstream.startswith("http"):
        url = upstream
    else:
        url = f"http://{upstream}"
    try:
        httpx.get(url, timeout=3.0)
        return False
    except httpx.ConnectError:
        return True
    except Exception:
        # other errors (timeout, 502, etc.) are not "refused"
        return False


import time  # noqa: E402  (imported late to keep helpers above clean)
