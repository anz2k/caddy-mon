# AGENTS.md

Guidance for AI coding agents (and future contributors) working on **caddy-mon**.

## What this is

A minimal Caddy reverse-proxy visibility dashboard. No Grafana, no Prometheus.
It polls the Caddy admin API and shows each proxied site's alive/dead status,
latency, and last-check time on a single web page.

Stack: Python 3.12 (slim) + FastAPI + uvicorn + httpx, packaged as a Docker
container that runs on the same Docker network as `caddy-proxy` (`caddy_default`).

## Language rule (IMPORTANT)

This is a **public repo**. All code, comments, docstrings, UI strings, and
documentation MUST be in **English**. Write all repo content in English
regardless of what language the user/chat uses. Do not add text in any other
language (e.g. Estonian, Polish, etc.) anywhere in the repo.

## How to contribute

1. Edit files locally in `~/GIT/caddy-mon/` (the dev checkout; the server
   deployment copy lives at `~/stacks/caddy-mon/` on docker02).
2. Commit and push to `main` on GitHub (`anz2k/caddy-mon`).
3. After pushing, the server (docker02, `<server-ip>`) is updated automatically
   by the agent with:
   ```bash
   cd ~/stacks/caddy-mon && git pull && docker compose up -d --build
   ```
   You do NOT need to ask for permission to rebuild after a push.

## API reference

- **Caddy `caddy_reverse_proxy_upstreams_healthy` metric is authoritative.**
  If Caddy reports `healthy=1`, the site is alive. A failed self-probe is
  treated as a false negative (e.g. Immich does not answer plain HTTP, but
  Caddy knows it is up).
- The self-made HTTP GET probe is supplementary (latency only), not the
  decision maker.
- If Caddy reports no health (`None`), the probe becomes the only signal.

## Caddy requirement

The Caddy admin API must be reachable from this container. In the Caddyfile,
the global options block must contain:

```
{
    admin :2019
}
```

Without this, the admin API only listens on `localhost` inside the caddy-proxy
container and is unreachable from `caddy-mon`. Port 2019 is NOT published to
the host — it stays reachable only to containers on the `caddy_default` network.

## Notes

- Caddy `/metrics` does NOT expose `caddy_http_*` traffic metrics (only
  admin + reverse_proxy_healthy + Go runtime). Latency comes from the probe.
- Site order in the UI is fixed to the Caddyfile route order (no sorting by
  health) so cards don't jump around on every refresh.
- Future extensions (access.log parsing for rate/4xx%, Telegram alerts, TLS
  for the dashboard itself) can be added without architecture changes.
