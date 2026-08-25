# Architecture

How caddy-mon works internally.

## Components

```
caddy-proxy (Caddy, :2019 admin API)
        ↑ DNS: caddy-proxy (same Docker network caddy_default)
caddy-mon (FastAPI, :8080)
        ↓ serves dashboard + /topology + /logs + their JSON APIs
Browser (LAN, http://<host>:8080)
        ↑ Caddy access log mounted read-only at /caddy-logs
```

## Data flow

1. `refresh()` is called on every dashboard load and `/api/state` request.
2. It reads Caddy routes from `GET caddy-proxy:2019/config/apps/http/servers`
   (ALL servers: srv0, srv1, ...), merging each server's `.routes`.
   - Each route has `match[].host` (hostnames) and a `reverse_proxy` handler
     with `upstreams[].dial` (IP:port). Path matchers and nested `subroute`
     handlers are walked recursively.
3. It reads `GET caddy-proxy:2019/metrics` and parses
   `caddy_reverse_proxy_upstreams_healthy{upstream="IP:port"} 0|1`.
4. It runs a self-made HTTP GET probe to each upstream (for latency).
5. Health is decided by combining Caddy `healthy` with the probe: if Caddy
   says healthy=1 but the probe gets connection refused, the site is dead
   (stale Caddy signal). A probe timeout with healthy=1 is still alive.
6. Result is cached in `_state` for `POLL_INTERVAL` (10s) to avoid hammering Caddy.
7. Separately, `_ingest_logs()` tails `/caddy-logs/access.log` (Caddy JSON
   access log) incrementally and keeps the last ~5000 entries in memory.
   `_log_stats()` aggregates them per host over a configurable window.

## Multi-server Caddy support

caddy-mon reads routes from **all** Caddy HTTP servers
(`/config/apps/http/servers` → each `srv0`, `srv1`, ... → `.routes`), not just
`srv0`. Your Caddy may define multiple servers (e.g. for different listen
ports or site groups). All are merged into the dashboard/topology view.

## Endpoints

| Route | Purpose | Access |
|-------|---------|--------|
| `GET /status` | Public system status overview (sanitized services, incident timeline) | **Public** |
| `GET /api/status` | Sanitized public status JSON API | **Public** |
| `GET /` | HTML dashboard (cards with 24h uptime, sparklines, diagnostics, SSE) | Protected |
| `GET /api/state` | JSON: `{last_update, sites[], errors[]}` | Protected |
| `GET /api/events` | Server-Sent Events (SSE) live streaming updates | Protected |
| `GET /api/history/{host}` | JSON: `{host, uptime_24h, sparkline[]}` | Protected |
| `GET /api/incidents` | JSON: `{incidents[]}` (recent DOWN/RECOVERED events) | Protected |
| `POST /api/probe/{host}` | Immediate on-demand diagnostic probe of all upstreams for a host | Protected |
| `POST /api/maintenance/{host}` | Enable or disable maintenance mode for a host | Protected |
| `GET /api/maintenance` | JSON: `{maintenance{}}` active maintenance mapping | Protected |
| `GET /topology` | HTML SVG route map (host → path → Caddy proxy → upstream) | Protected |
| `GET /api/topology` | JSON: `{nodes[], edges[]}` for the route map | Protected |
| `GET /logs` | HTML log analytics (per-host 5xx/error%, recent 5xx) | Protected |
| `GET /api/logs` | JSON: `{window_seconds, rows[], recent_5xx[]}` | Protected |
| `GET /tls` | HTML TLS certificate expiry table | Protected |
| `GET /api/tls` | JSON: `{entries[], warn_days}` | Protected |

## History & persistence internals

- Source: embedded SQLite database at `${DB_PATH}` (`/data/caddy_mon.db` by default, mounted via Docker volume with auto-fallback to `/tmp`).
- `site_snapshots` records periodic health, alive status, and latency.
- `site_maintenance` records planned maintenance windows and reasons.
- `get_site_uptime_24h(host)` calculates rolling 24h uptime percentage.
- `get_site_sparkline(host)` computes 12-point bucketed average latency for mini SVG rendering.
- `prune_old_history(days=7)` automatically purges snapshots older than retention limit.

## Alerting & maintenance internals

- Telegram Bot and generic Webhooks (Discord/Slack) dispatch alerts on state changes:
  - `ALIVE -> DEAD`: Site down alert with failing upstream diagnostics.
  - `DEAD -> ALIVE`: Site recovered alert with downtime duration.
- Maintenance Mode: Sites flagged under active maintenance suppress DOWN alerts to avoid false alarms.
- Cooldown timer (`ALERT_COOLDOWN_MINUTES`) prevents notification storms during flapping.
- All events are logged to `incident_events` table in SQLite.

## Code layout

The app is split into a modular `caddy_mon` package; `app.py` is a thin FastAPI entry point.

| Module | Responsibility |
|--------|---------------|
| `app.py` | FastAPI app: lifespan background worker, route registration, auth dependencies |
| `caddy_mon/config.py` | Configuration settings (`CADDY_API`, `DB_PATH`, Telegram/Webhook tokens, `AUTH_*`, TZ) |
| `caddy_mon/auth.py` | Optional HTTP Basic Auth security dependency using constant-time comparison |
| `caddy_mon/db.py` | SQLite database layer: snapshots, 24h uptime, sparklines, maintenance, incidents, retention |
| `caddy_mon/alerts.py` | Incident alerting: Telegram bot, Webhooks, transition detection, maintenance suppression |
| `caddy_mon/sse.py` | Server-Sent Events broadcaster: live client queue management, keepalive |
| `caddy_mon/status_page.py` | Public status page HTML + sanitized `/api/status` endpoint |
| `caddy_mon/diagnostics.py` | On-demand detailed upstream probing (`POST /api/probe/{host}`) |
| `caddy_mon/caddy_source.py` | Caddy admin API: routes, health metrics, async probes, background loop |
| `caddy_mon/log_source.py` | Access-log ingestion: `ingest_logs()`, `host_log_stats()`, `log_stats()` |
| `caddy_mon/tls_source.py` | TLS cert parsing: `cert_status()`, expiry tracking |
| `caddy_mon/dashboard.py` | Dashboard HTML page with SVG sparklines + `/api/state` |
| `caddy_mon/topology.py` | Route-map API + SVG HTML page (`/topology`, `/api/topology`) |
| `caddy_mon/logs_page.py` | Log analytics HTML page + `/api/logs` |
| `caddy_mon/tls_page.py` | TLS expiry HTML page + `/api/tls` |
| `tests/` | Comprehensive unit tests (43 unit tests) + live Caddy integration tests |
