# caddy-mon

Minimal Caddy reverse-proxy visibility dashboard. No Grafana, no Prometheus.

Shows one web page with the live status of every proxied site: alive/dead,
latency, and last check time — pulled straight from the Caddy admin API.

## What it does

- Polls the Caddy admin API (`caddy-proxy:2019`) every 10s for:
  - all routes + their host matchers and upstream dials
  - `caddy_reverse_proxy_upstreams_healthy` metric (0/1 per upstream)
- Runs a self-made HTTP GET probe to each upstream (latency + status)
- Serves a single web page on `:8080` (green/red light per site)

No Prometheus, no Grafana, no TSDB. Just Caddy, which you already have.

## How health is decided

- **Caddy `healthy` metric is authoritative.** If Caddy says `healthy=1`,
  the site is alive (a probe failure is treated as a false negative — e.g.
  Immich does not answer plain HTTP, but Caddy knows it is up).
- The probe is supplementary info (latency), not the decision maker.
- If Caddy reports no health (None), the probe becomes the only signal.

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
- mounts `/home/andres.kaaber/stacks/caddy/logs` read-only (log inspection, optional)
- port `8080` does not need to be exposed to WAN — LAN only

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

## Remove

```bash
docker compose down
```

## Technical note

Caddy `/metrics` does **not** expose `caddy_http_*` traffic metrics (only
admin/reverse_proxy_healthy + Go runtime). Therefore:
- status = `caddy_reverse_proxy_upstreams_healthy` (already computed by Caddy)
- latency = self-made GET probe (does not depend on Caddy logs)
- future: access.log parsing (rate/4xx%) can be added without architecture changes
