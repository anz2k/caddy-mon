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
LOG_PATH = os.environ.get("LOG_PATH", "/caddy-logs/access.log")

# Local timezone for dashboard timestamps.
TZ = ZoneInfo(os.environ.get("TZ", "Europe/Tallinn"))

# SQLite database file for historical snapshots and incident events.
# Falls back to local directory if /data is not mounted.
DB_PATH = os.environ.get(
    "DB_PATH",
    "/data/caddy_mon.db" if os.path.isdir("/data") else "caddy_mon.db",
)

# Number of days to keep historical site health records (auto-pruned).
HISTORY_RETENTION_DAYS = int(os.environ.get("HISTORY_RETENTION_DAYS", "7"))

# Incident alerting configuration (optional).
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
ALERT_COOLDOWN_MINUTES = int(os.environ.get("ALERT_COOLDOWN_MINUTES", "15"))
