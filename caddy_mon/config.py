"""Global configuration for caddy-mon."""

import os
from zoneinfo import ZoneInfo

# Caddy admin API base URL. Override with the CADDY_API env var if your
# Caddy admin listener or container name differs.
CADDY_API = os.environ.get("CADDY_API", "http://caddy-proxy:2019")

# How often refresh() re-polls Caddy (seconds).
POLL_INTERVAL = 10

# Single self-probe timeout (seconds).
PROBE_TIMEOUT = 3.0

# Path to the Caddy JSON access log inside the container.
LOG_PATH = "/caddy-logs/access.log"

# Local timezone for dashboard timestamps.
TZ = ZoneInfo("Europe/Tallinn")
