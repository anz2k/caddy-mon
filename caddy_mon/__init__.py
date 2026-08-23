"""caddy-mon — minimal Caddy reverse-proxy visibility.

A small FastAPI app that reads from the Caddy admin API and the Caddy access
log, and shows per-site health, topology, and log analytics on a web page.
"""

from .config import CADDY_API, POLL_INTERVAL, PROBE_TIMEOUT, LOG_PATH, TZ  # noqa: F401

__all__ = ["CADDY_API", "POLL_INTERVAL", "PROBE_TIMEOUT", "LOG_PATH", "TZ"]
