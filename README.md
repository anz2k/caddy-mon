# caddy-mon

A self-hosted Caddy reverse-proxy visibility dashboard — live health, latency,
uptime history, log analytics, TLS expiry, security insights, and incident
alerting, all pulled straight from the Caddy admin API and access logs.

No Grafana, no Prometheus, no external TSDB — just Caddy, which you already run.

## Features

- **Live health dashboard** — live alive/dead + latency per proxied site with zero-flicker SSE updates
- **Dynamic Route CRUD & Deployment** — create new reverse-proxy routes (`[+ Add Site]`), delete routes, and manage upstreams on the fly
- **Audit Trail & Rollback Engine** — visual change log at `/audit` (`POST /api/routes`, `DELETE /api/routes/{host}`) with automatic pre-modification snapshots and one-click rollback
- **Live Search & Status Filtering** — instant search (`/` shortcut), quick status filter pills (`All`, `Alive`, `Down`, `Maint`, `>100ms`), and multi-attribute sorting
- **Transport Timeouts & Connection Insights** — parses and displays `dial_timeout`, `read_timeout`, and keepalive parameters per route
- **Site Deep-Dive Inspector Modal** — click any card to inspect 24h/7d latency stats (min/avg/max), host-specific access logs, and incident timelines
- **Automated ACME & Custom TLS Discovery** — monitors Let's Encrypt / ZeroSSL automatic certificates alongside manual certs with expiration countdowns
- **Public Status Page & RSS Feed** — clean public status overview (`/status`) with 30-day uptime history bars and RSS 2.0 incident feed (`/status/feed.xml`)
- **Security & Client Analytics** — top client IPs, 4xx/429 rate limit events, status code distribution, and LAN/WAN classification at `/security`
- **Caddy Control Plane & Config Inspector** — active JSON configuration viewer with download and zero-downtime reload (`/caddy/config`)
- **On-Demand Diagnostics** — instant "⚡ Test" probe button per card measuring status code, response headers, and latency
- **Maintenance Mode** — toggle planned maintenance (`🛠️ Maint`) per site to suppress DOWN alerts during maintenance windows
- **Optional Authentication** — HTTP Basic Auth (`AUTH_USER`/`AUTH_PASSWORD`) protecting admin routes while keeping `/status` public
- **24h Uptime & Sparklines** — rolling 24h uptime % badge and mini SVG latency trendlines per card
- **Incident alerting** — automatic Telegram and Webhook alerts when sites go DOWN or RECOVER
- **Route topology** — SVG map of host → path → Caddy proxy → upstream (at `/topology`), supports path-based routing and multi-upstream
- **Multi-server Caddy** — reads routes from all Caddy HTTP servers (srv0, srv1, ...), not just srv0
- **Combined health** — Caddy `healthy` metric + self-probe; connection-refused overrides a stale healthy=1
- **Log analytics** — per-host request/5xx/error% over a time window (compact line on each dashboard card + full view at `/logs`, JSON at `/api/logs`)
- **Embedded SQLite history** — persistent snapshots in `/data/caddy_mon.db` auto-pruned after 7 days
- **Local timezone** — dashboard timestamp uses the host's local timezone (not UTC)
- **Domain grouping** — sites grouped by parent domain (lope.ee, kaaber.ee, lope.lan) on the dashboard
- **Alias listing** — extra hostnames on a route are shown as a bulleted list under the primary host

See [docs/](docs/) for architecture and per-feature details.

## What it does

- Polls the Caddy admin API (`caddy-proxy:2019`) every 10s for:
  - all routes + their host matchers and upstream dials (from ALL servers, srv0/srv1/...)
  - `caddy_reverse_proxy_upstreams_healthy` metric (0/1 per upstream)
- Runs a self-made HTTP GET probe to each upstream (latency + status)
- Reads the Caddy access log (`/caddy-logs/access.log`) for request/error analytics
- Serves web pages on `:8080` (dashboard, topology, logs)

No Prometheus, no Grafana, no TSDB. Just Caddy, which you already have.

## How health is decided

Health combines Caddy's `healthy` metric with caddy-mon's own probe:

- Caddy reports `healthy=0` → site is **DEAD**
- Caddy reports `healthy=1` but the probe gets **connection refused** → site is
  **DEAD** (Caddy's health check was stale/wrong)
- Caddy reports `healthy=1` and the probe fails with a timeout (not refused) →
  site is **ALIVE** (Caddy knows the backend state)
- Caddy reports no health (`None`) → the probe is the only signal

## Run

```bash
cd <deployment-dir>
docker compose up -d --build
# then open the dashboard in a browser on port 8080
```

(`<deployment-dir>` is the directory on the host where this container runs.)

## Config

`docker-compose.yml`:
- joins the `caddy_default` network (so `caddy-proxy` DNS resolves)
- mounts the Caddy access-log directory read-only at `/caddy-logs` (for log analytics)
- port `8080` does not need to be exposed to WAN — LAN only
- `CADDY_API` env var overrides the admin API URL (default `http://caddy-proxy:2019`)

## Caddy requirement

The Caddy admin API must be reachable from this container. In `Caddyfile`,
the global options block needs:

```
{
    admin :2019
}
```

(Without this, the admin API only listens on `localhost` and is unreachable
from the `caddy-mon` container. Port 2019 is NOT published to the host, so
it stays reachable only to containers on the `caddy_default` network.)

## Testing

```bash
# install dev deps (pytest, cryptography) in a venv
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

# unit tests (no network needed)
pytest tests/test_*.py -v

# live Caddy config tests (needs Caddy admin API reachable; auto-skip otherwise)
pytest tests/test_caddy_config.py -v
```

Unit tests cover route parsing (`_parse_routes`), health-metric parsing
(`_parse_healthy`), TLS cert parsing (`cert_status`), TLD grouping, SQLite
persistence & 24h uptime calculations (`test_db.py`), incident alerting & cooldown
(`test_alerts.py`), and real-time SSE broadcasting (`test_sse.py`). The
`test_caddy_config.py` integration tests probe the live Caddy admin API and
every upstream dial.

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — how caddy-mon works internally
- [docs/FEATURES.md](docs/FEATURES.md) — detailed per-feature writeups
