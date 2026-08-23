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

| Route | Purpose |
|-------|---------|
| `GET /` | HTML dashboard (cards grouped by TLD, auto-refresh 12s) |
| `GET /api/state` | JSON: `{last_update, sites[], errors[]}` |
| `GET /topology` | HTML SVG route map (host → path → Caddy proxy → upstream) |
| `GET /api/topology` | JSON: `{nodes[], edges[]}` for the route map |
| `GET /logs` | HTML log analytics (per-host 5xx/error%, recent 5xx) |
| `GET /api/logs` | JSON: `{window_seconds, rows[], recent_5xx[]}` |

## Log analytics internals

- Source: Caddy JSON access log at `/caddy-logs/access.log` (mounted read-only
  from the host's Caddy log directory).
- `_ingest_logs()` tracks file position + inode so a log rotation (new file,
  new inode) restarts from offset 0. It uses `readline()` + `tell()` (not
  `for line in f` + `tell()`, which raises OSError on Python).
- `admin.api` logger entries (caddy-mon's own polling) are filtered out.
- Entries are kept in an in-memory ring buffer capped at ~5000 to bound memory.

## Why no Prometheus/Grafana

Caddy `/metrics` does **not** expose `caddy_http_*` traffic counters (only
admin + reverse_proxy_healthy + Go runtime). So:
- **status** comes from Caddy's already-computed `healthy` metric
- **latency** comes from a self-made probe (does not depend on Caddy logs)
- **traffic/5xx** comes from the access log (parsed in-process, no TSDB)
- no TSDB needed — the dashboard is a snapshot, not a time series

## Deployment

- `caddy-mon` runs as a Docker container on the **same network** as `caddy-proxy`
  (`caddy_default`), so the DNS name `caddy-proxy` resolves.
- The Caddyfile must expose the admin API on all interfaces (`admin :2019`)
  inside the container; port 2019 is NOT published to the host.
- The Caddy access-log directory is mounted read-only at `/caddy-logs` for
  log analytics.
- `CADDY_API` env var overrides the admin API URL (default `http://caddy-proxy:2019`).
- The deployment directory is a clone of this repo; `git pull && docker compose
  up -d --build` applies updates.

## Files

| File | Responsibility |
|------|---------------|
| `app.py` | FastAPI app: Caddy polling, health logic, log ingestion, all HTML pages |
| `Dockerfile` | Python 3.12-slim + FastAPI/uvicorn/httpx |
| `docker-compose.yml` | network join, port 8080, log mount, `CADDY_API` env |
| `AGENTS.md` | instructions for AI agents contributing to this repo |
