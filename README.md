# caddy-mon

A self-hosted Caddy reverse-proxy visibility dashboard — live health, latency,
uptime history, log analytics, TLS expiry, security insights, and incident
alerting, all pulled straight from the Caddy admin API and access logs.

No Grafana, no Prometheus, no external TSDB — just Caddy, which you already run.

## Features

### Health & Uptime

- **Live health dashboard** — every proxied site as a card with alive/dead status, latency, and last-update timestamp; grouped by parent domain, with alias hostnames listed under the primary host
- **Combined health check** — Caddy's `caddy_reverse_proxy_upstreams_healthy` gauge + caddy-mon's own HTTP probe; a stale `healthy=1` with `connection refused` is treated as dead
- **Per-upstream health** — each upstream dial carries its own badge (`healthy` / `unhealthy` / `?`) so you see exactly which replica is down in a multi-upstream site
- **24 h uptime & sparklines** — rolling uptime percentage and a mini SVG latency trendline on every card; data persisted in embedded SQLite and pruned after 7 days
- **Site deep-dive modal** — click any card for 24 h / 7 d latency stats (min/avg/max), per-site access logs, incident timeline, and visitor totals
- **Zero-flicker live updates** — Server-Sent Events (`/api/events`) push state changes to the browser without full-page reloads

### Proxy Visibility

- **Transport & connection details** — `dial_timeout`, `read_timeout`, `write_timeout`, `response_header_timeout`, and `keepalive` parsed from the Caddy JSON API (nanoseconds → `1h`/`30s`/`500ms`) and shown on cards and in the deep-dive modal
- **Load-balancing** — `selection_policy` (`round_robin`, `least_conn`, `ip_hash`, …), `retries`, `try_duration`, and `try_interval` surfaced per site
- **Health-check configuration** — active checks (`uri`/`interval`/`timeout`) and passive circuit-breaker thresholds (`max_fails`/`fail_duration`/`unhealthy_latency`) shown in the deep-dive modal
- **Request transforms & header rules** — `rewrite` (strip prefix, URI rewrite), upstream request headers, response headers, and `handle_response` fallbacks rendered on the topology map and in the deep-dive modal
- **Route topology** — SVG map `host → path → Caddy proxy → upstream` at `/topology` (JSON at `/api/topology`), with path matchers, nested `subroute` handlers, and transform badges
- **Multi-server Caddy** — discovers routes from all HTTP servers (`srv0`, `srv1`, …), not just `srv0`

### Traffic & Analytics

- **Log analytics** — per-host request / 5xx / error % over a configurable window; compact summary on each card and full view at `/logs` (JSON at `/api/logs?window=N`); recent 5xx errors with host, status, and URI
- **Latency distribution** — per-host `avg` / `p50` / `p95` / `p99` and a bucketed histogram (`0–10 ms` … `1 s+`) computed from the access-log `duration` field; shown on `/logs`
- **Traffic & visitor analytics** — privacy-friendly, server-side analytics at `/analytics` (JSON at `/api/analytics`): unique visitors, pageviews, bandwidth, top paths, referrers, browsers / OS, human vs bot classification, and an hourly SVG timeline; 24 h visitor summary also embedded in the deep-dive modal

### Operations

- **On-demand diagnostics** — `⚡ Test` button on each card (`POST /api/probe/{host}`) for an immediate upstream probe with status code, headers, and latency
- **Maintenance mode** — `🛠️ Maint` toggle per site; while enabled the dashboard shows `MAINTENANCE` and `DOWN` alerts are suppressed
- **Live search & filtering** — instant search (`/` shortcut), status pills (`All` / `Alive` / `Down` / `Maint` / `>100 ms`), and sorting by domain group, latency, uptime, or alphabetically
- **Caddy control plane** — live JSON config inspector with download and zero-downtime reload at `/caddy/config` (`GET /api/caddy/config`, `POST /api/caddy/reload`)
- **Dynamic route CRUD** — create and delete reverse-proxy routes from the dashboard (`POST /api/routes`, `DELETE /api/routes/{host}`) with FQDN/upstream validation and automatic TLS provisioning (Let's Encrypt / ZeroSSL)
- **Audit trail & rollback** — every route change and rollback is logged to SQLite with operator, timestamp, and before/after payload; restore any snapshot from `/audit` (`POST /api/caddy/rollback/{id}`)
- **Incident alerting** — Telegram and generic webhook (Discord/Slack) notifications on `ALIVE → DEAD` and `DEAD → ALIVE` transitions, with cooldown (`ALERT_COOLDOWN_MINUTES`) and an `incident_events` archive

### Security & TLS

- **Security & client analytics** — top client IPs, LAN vs WAN split, status-code distribution (`2xx`/`3xx`/`4xx`/`429`/`5xx`), and suspicious-request listing at `/security`
- **Trusted-proxy audit** — detects `X-Forwarded-For` masking (e.g. Cloudflare / Docker NAT gateway) and suggests the correct `trusted_proxies` Caddyfile setting when >80 % of traffic appears to come from a single gateway
- **TLS expiry monitoring** — `🔒 NNd` countdown on each card and full inventory at `/tls` (JSON at `/api/tls`); parses both manually mounted certs (`/caddy-certs`) and ACME-managed certs (`/caddy-data`, `/data/caddy/certificates`) via `cryptography`
- **Public status page** — clean, sanitized overview at `/status` (30-day daily uptime bars, incident timeline) plus JSON at `/api/status` and an RSS 2.0 feed at `/status/feed.xml`; internal IPs and `.lan`/`.local` hosts are hidden
- **Optional authentication** — HTTP Basic Auth (`AUTH_USER`/`AUTH_PASSWORD`) protecting all admin routes while keeping `/status` public; local timezone (`TZ`) for timestamps

See [docs/FEATURES.md](docs/FEATURES.md) for per-feature details and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for internals.

## How it works

- Polls the Caddy admin API (`http://caddy-proxy:2019` by default) every 10 s for:
  - all routes + host matchers and upstream dials (from **all** servers, `srv0`/`srv1`/…)
  - the `caddy_reverse_proxy_upstreams_healthy{upstream="IP:port"}` gauge (`0`/`1` per upstream)
- Probes each unique upstream with an HTTP `GET` (3 s timeout) to measure latency and status
- Tails the Caddy JSON access log (`/caddy-logs/access.log`) incrementally (tracks file position + inode, filters `admin.api` noise, keeps last ~5 000 entries in memory) for request/error/latency analytics
- Serves the dashboard and all feature pages on `:8080`

No Prometheus, no Grafana, no TSDB — just Caddy, which you already have.

### How health is decided

Health combines Caddy's `healthy` metric with caddy-mon's own probe:

- Caddy reports `healthy=0` → **DEAD**
- Caddy reports `healthy=1` but the probe gets **connection refused** → **DEAD** (stale Caddy signal)
- Caddy reports `healthy=1` and the probe times out (not refused) → **ALIVE** (Caddy knows the backend state)
- Caddy reports no health (`None`) → the probe is the only signal

## Quick start

```bash
# 1. copy env template and fill in your local paths
cp .env.example .env
# edit CADDY_STACK_DIR to point at your Caddy stack (contains logs/ and certs/)

# 2. build and run (joins the existing caddy_default network)
docker compose up -d --build

# 3. open the dashboard
# http://<host>:8080        — dashboard (protected if AUTH_* is set)
# http://<host>:8080/status — public status page (always open)
```

`<deployment-dir>` is the directory on the host where this compose file runs. Port `8080` does not need to be exposed to the WAN — LAN only.

## Configuration

`docker-compose.yml` joins the `caddy_default` network (so the DNS name `caddy-proxy` resolves), mounts the Caddy access-log directory read-only at `/caddy-logs`, optionally mounts TLS cert directories, and persists history in `./data:/data`.

All settings are environment variables (see [.env.example](.env.example)):

| Variable | Default | Description |
|---|---|---|
| `CADDY_STACK_DIR` | *(required)* | Host path to your Caddy stack (must contain `logs/` and `certs/` subdirs) |
| `CADDY_API` | `http://caddy-proxy:2019` | Caddy admin API base URL |
| `LOG_PATH` | `/caddy-logs/access.log` | Access-log path inside the container |
| `DB_PATH` | `/data/caddy_mon.db` | SQLite database file |
| `HISTORY_RETENTION_DAYS` | `7` | Days to keep `site_snapshots` history |
| `TZ` | `Europe/Tallinn` | Timezone for dashboard timestamps |
| `AUTH_USER` / `AUTH_PASSWORD` | *(empty = open)* | Basic Auth for admin routes; `/status` stays public |
| `STATUS_TITLE` | `System Status` | Title shown on the public status page |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | *(empty)* | Telegram alerting |
| `WEBHOOK_URL` | *(empty)* | Generic webhook (Discord/Slack) alerting |
| `ALERT_COOLDOWN_MINUTES` | `15` | Alert cooldown to suppress flapping |

`.env` is gitignored — never commit secrets.

## Caddy requirement

The Caddy admin API must be reachable from the `caddy-mon` container. In your `Caddyfile` global options:

```
{
    admin :2019
}
```

Without this, the admin API only listens on `localhost` and is unreachable from `caddy-mon`. Do **not** publish port `2019` to the host — it stays reachable only to containers on the `caddy_default` network.

Access-log must be JSON. Example (already the default in most Caddy images):

```json
{
  "logging": {
    "logs": {
      "default": {
        "writer": { "output": "file", "filename": "/data/caddy/logs/access.log" },
        "encoder": { "format": "json" }
      }
    }
  }
}
```

## API reference

All JSON APIs require Basic Auth when `AUTH_*` is set, except the three public status routes.

| Route | Purpose | Access |
|---|---|---|
| `GET /status` | Public status page (30-day bars, incident timeline) | Public |
| `GET /api/status` | Sanitized status JSON | Public |
| `GET /status/feed.xml` | RSS 2.0 incident feed | Public |
| `GET /` | Dashboard (cards, sparklines, search, SSE) | Protected |
| `GET /api/state` | `{last_update, sites[], errors[]}` | Protected |
| `GET /api/events` | Server-Sent Events stream | Protected |
| `GET /api/history/{host}` | `{host, uptime_24h, sparkline[]}` | Protected |
| `GET /api/site/{host}/details` | Deep-dive: history, recent logs, incidents, traffic | Protected |
| `GET /api/export` | Full infrastructure export | Protected |
| `POST /api/routes` | Create reverse-proxy route (with snapshot & audit) | Protected |
| `DELETE /api/routes/{host}` | Delete reverse-proxy route | Protected |
| `GET /audit` | Audit trail & snapshot manager (HTML) | Protected |
| `GET /api/audit` | `{audit_logs[], snapshots[]}` | Protected |
| `POST /api/caddy/rollback/{id}` | Roll back Caddy config to a snapshot | Protected |
| `GET /api/incidents` | Recent `DOWN`/`RECOVERED` events | Protected |
| `POST /api/probe/{host}` | On-demand upstream probe | Protected |
| `POST /api/maintenance/{host}` | Toggle maintenance mode | Protected |
| `GET /api/maintenance` | Active maintenance map | Protected |
| `GET /security` | Security & client analytics (HTML) | Protected |
| `GET /api/security` | `{status_distribution, top_clients[], suspicious_requests[]}` | Protected |
| `GET /caddy/config` | Caddy JSON config inspector (HTML) | Protected |
| `GET /api/caddy/config` | Raw Caddy JSON config | Protected |
| `POST /api/caddy/reload` | Zero-downtime Caddy reload | Protected |
| `GET /topology` | Route map `host → path → proxy → upstream` (SVG) | Protected |
| `GET /api/topology` | `{nodes[], edges[]}` | Protected |
| `GET /analytics` | Traffic & visitor analytics (HTML) | Protected |
| `GET /api/analytics` | `{summary, timeline[], top_paths[], browsers[], …}` | Protected |
| `GET /logs` | Log analytics with latency distribution (HTML) | Protected |
| `GET /api/logs` | `{window_seconds, rows[], recent_5xx[]}` | Protected |
| `GET /tls` | TLS expiry inventory (HTML) | Protected |
| `GET /api/tls` | `{entries[], warn_days}` | Protected |

## Testing

```bash
# install dev deps in a venv
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

# unit tests (no network needed)
pytest tests/test_*.py -v

# live Caddy integration tests (needs Caddy admin API reachable; auto-skipped otherwise)
pytest tests/test_caddy_config.py -v
```

Coverage includes route parsing (`_parse_routes`), health-metric parsing (`_parse_healthy`), transport / load-balancing / health-check / transform extraction, TLD grouping, access-log ingestion and latency percentiles, TLS cert parsing, SQLite persistence and 24 h uptime, alerting and cooldown, SSE broadcasting, diagnostics, and maintenance mode. `test_caddy_config.py` probes the live Caddy admin API and every upstream dial.

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — components, data flow, endpoint table, and code layout
- [docs/FEATURES.md](docs/FEATURES.md) — detailed per-feature writeups

## Tech stack

Python 3.12 · FastAPI · uvicorn · httpx · cryptography · SQLite · Docker · Tailwind CSS

## License

[MIT](LICENSE)
