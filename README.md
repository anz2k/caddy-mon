# caddy-mon

Minimal Caddy reverse-proxy visibility dashboard. No Grafana, no Prometheus.

Shows one web page with the live status of every proxied site: alive/dead,
latency, and last check time — pulled straight from the Caddy admin API.

## Features

- **Health dashboard** — live alive/dead + latency per proxied site, auto-refresh every 12s
- **Route topology** — SVG map of host → path → Caddy proxy → upstream (at `/topology`), supports path-based routing and multi-upstream
- **Multi-server Caddy** — reads routes from all Caddy HTTP servers (srv0, srv1, ...), not just srv0
- **Combined health** — Caddy `healthy` metric + self-probe; connection-refused overrides a stale healthy=1
- **Log analytics** — per-host request/5xx/error% over a time window (at `/logs`, JSON at `/api/logs`)
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

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — how caddy-mon works internally
- [docs/FEATURES.md](docs/FEATURES.md) — detailed per-feature writeups
