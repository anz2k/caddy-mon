# Features

Detailed writeups of each caddy-mon feature.

## Health dashboard

**What it adds:** A single web page showing every proxied site as a card with
alive/dead status (green/red), latency in ms, and the last-update timestamp.
Auto-refreshes every 12 seconds. Sites are grouped by parent domain
(lope.ee, kaaber.ee, lope.lan), and extra hostnames (aliases) on a route are
shown as a bulleted list under the primary host.

**How it works:** `refresh()` polls Caddy, builds the site list, and the
dashboard route renders an HTML grid grouped by TLD. The same data is available
as JSON at `/api/state` for external consumers.

**Why:** Gives immediate visibility into proxy health without Grafana/Prometheus.

## Health decision logic

**What it adds:** Site health combines Caddy's `caddy_reverse_proxy_upstreams_healthy`
metric with caddy-mon's own HTTP probe, so a stale Caddy health signal does not
hide a dead backend.

**How it works:** For each upstream, the site is considered dead if:
- Caddy reports `healthy=0` (unhealthy), OR
- Caddy reports `healthy=1` but the probe gets **connection refused** (Caddy's
  health check was stale/wrong), OR
- Caddy reports no health (`None`) and the probe fails.

If Caddy reports `healthy=1` and the probe fails with a **timeout** (not
connection refused), the site is still treated as alive — Caddy knows the
backend state better than a single probe. If Caddy gives no signal (`None`),
the probe is the only source of truth.

**Why:** Example — if a backend container crashes, Caddy's `healthy` metric
may lag (still showing `1`) while the port is already refusing connections.
The combined check catches this immediately instead of showing a false "alive".

## Route topology

**What it adds:** A visual map of how traffic flows:
`host` → `path` → `Caddy proxy` → `upstream IP:port`. Available at `/topology`
(auto-refreshes every 12s). Also exposed as JSON at `/api/topology`.

**How it works:** `_parse_routes()` walks the Caddy route tree recursively
(supporting nested `subroute` handlers and path matchers). For each site it
collects `(path, upstreams)` branches. `api_topology()` builds graph nodes:
host (left), path matcher (mid-left), Caddy proxy (mid-right), upstream dial
(right), with edges host → path → proxy → upstream. The `/topology` route
renders a 4-column SVG (no external graph library).

**Why:** Shows path-based routing and multi-upstream setups at a glance —
e.g. `example.ee/` → service A, `example.ee/api` → service B. Useful for
complex Caddy configs, not just simple host→upstream maps.

## Log analytics

**What it adds:** A per-host request summary over a configurable time window.
A compact one-line summary (requests · 5xx · error%) appears **under each
card on the dashboard** (red if there are 5xx errors, gray otherwise), and a
full table view is available at `/logs` (auto-refreshes every 30s) with JSON
at `/api/logs?window=N` (seconds, default 3600). The full view also lists
recent 5xx errors with timestamp, host, status, and URI.

**How it works:** `ingest_logs()` tails `/caddy-logs/access.log` (Caddy JSON
access log) incrementally — it tracks file position + inode so a log rotation
restarts cleanly, and filters out caddy-mon's own `admin.api` polling noise.
Parsed entries are kept in an in-memory ring buffer (last ~5000). `log_stats()`
aggregates entries within the window into per-host stats.

**Why:** Surfaces backend errors that health checks miss — e.g. "pildid.lope.ee
returned 502 N times in the last hour" shows up here even if Caddy still
reports the upstream as healthy.

## TLS expiry

**What it adds:** A compact `🔒 NNd` line under each dashboard card shows the
TLS certificate expiry for that host's certificate (red if fewer than 30 days
remain). A full table view is available at `/tls` with JSON at `/api/tls`.

**How it works:** Cert files mounted at `/caddy-certs` are parsed with the
`cryptography` library. `cert_status()` reads each PEM, extracts SANs and the
`not_valid_after` date, and computes days left. `refresh()` matches each site's
hosts against cert SANs via `_site_tls()` and attaches the result to the card.
The `/tls` page lists all mounted certs sorted by urgency.

**Why:** Manual (non-ACME) certificates — like `idm.lope.lan` and
`trek.lope.lan` — do not auto-renew and can expire silently. This surfaces the
countdown before it becomes an outage. (Sites covered by ACME-managed certs
show `n/a` because those cert files are not mounted.)

## Multi-server Caddy support

**What it adds:** caddy-mon reads routes from **all** Caddy HTTP servers
(`/config/apps/http/servers` → each `srv0`, `srv1`, ... → `.routes`), not just
a hardcoded `srv0`.

**How it works:** `refresh()` fetches the full servers map and merges routes
from every server into the site list. This matters when your Caddy defines
multiple servers (e.g. for different listen ports or site groups).

**Why:** A single-server assumption silently drops sites defined on `srv1`,
`srv2`, etc.

## Planned / future (not yet implemented)

See the development plan (private, not in this repo) for the backlog:
Telegram alerts.
