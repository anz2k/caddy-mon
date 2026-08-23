# Architecture

How caddy-mon works internally.

## Components

```
caddy-proxy (Caddy, :2019 admin API)
        ↑ DNS: caddy-proxy (same Docker network caddy_default)
caddy-mon (FastAPI, :8080)
        ↓ serves dashboard + /api/state
Browser (LAN, http://<host>:8080)
```

## Data flow

1. `refresh()` is called on every dashboard load and `/api/state` request.
2. It reads Caddy routes from `GET caddy-proxy:2019/config/apps/http/servers/srv0/routes`.
   - Each route has `match[].host` (hostnames) and a `reverse_proxy` handler
     with `upstreams[].dial` (IP:port).
3. It reads `GET caddy-proxy:2019/metrics` and parses
   `caddy_reverse_proxy_upstreams_healthy{upstream="IP:port"} 0|1`.
4. It runs a self-made HTTP GET probe to each upstream (for latency).
5. Health is decided: Caddy `healthy=1` wins; a probe failure is a false negative.
6. Result is cached in `_state` for `POLL_INTERVAL` (10s) to avoid hammering Caddy.

## Why no Prometheus/Grafana

Caddy `/metrics` does **not** expose `caddy_http_*` traffic counters (only
admin + reverse_proxy_healthy + Go runtime). So:
- **status** comes from Caddy's already-computed `healthy` metric
- **latency** comes from a self-made probe (does not depend on Caddy logs)
- no TSDB needed — the dashboard is a snapshot, not a time series

## Deployment

- `caddy-mon` runs as a Docker container on the **same network** as `caddy-proxy`
  (`caddy_default`), so the DNS name `caddy-proxy` resolves.
- The Caddyfile must expose the admin API on all interfaces (`admin :2019`)
  inside the container; port 2019 is NOT published to the host.
- The deployment directory is a clone of this repo; `git pull && docker compose
  up -d --build` applies updates.

## Files

| File | Responsibility |
|------|---------------|
| `app.py` | FastAPI app, Caddy polling, health logic, HTML dashboard |
| `Dockerfile` | Python 3.12-slim + FastAPI/uvicorn/httpx |
| `docker-compose.yml` | network join, port 8080, optional log mount |
| `AGENTS.md` | instructions for AI agents contributing to this repo |
