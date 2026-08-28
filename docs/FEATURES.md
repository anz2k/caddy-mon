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

## Real-Time SSE streaming

**What it adds:** Zero-flicker live updates via Server-Sent Events (`/api/events`). The browser no longer performs full-page reloads; status badges, latencies, and timestamps update smoothly in place.

**How it works:** `caddy_mon.sse.EventBroadcaster` manages subscriber queues. Whenever background polling completes or state changes, a `state_update` event is dispatched to all connected browsers.

## History, 24h Uptime & Sparklines

**What it adds:** An embedded SQLite time-series history that tracks rolling 24-hour uptime percentage (e.g. `99.8% (24h)`) and renders mini SVG latency sparklines directly on each dashboard site card.

**How it works:** `caddy_mon.db` records health snapshots into `site_snapshots`. `get_site_uptime_24h()` computes the percentage of healthy checks, and `get_site_sparkline()` generates a 12-point latency trendline. Snapshots older than 7 days are automatically pruned.

## Incident alerting (Telegram & Webhooks)

**What it adds:** Automated notifications when a site transitions from `ALIVE` to `DEAD` (down alert with upstream error diagnostics) and when it recovers (`DEAD` to `ALIVE`).

**How it works:** `caddy_mon.alerts` monitors state transitions against the `alert_state` SQLite table. Cooldown throttling (`ALERT_COOLDOWN_MINUTES`) prevents alert floods during flapping, and incidents are archived in `incident_events`.

## Public Status Page & RSS Feed

**What it adds:** A clean, public-facing status page at `/status`, sanitized JSON at `/api/status`, and an RSS 2.0 incident feed at `/status/feed.xml`.
**How it works:** Displays overall system operational status ("All Systems Operational" / "Partial Outage" / "Major Outage"), 30-day daily uptime history bars, 24h uptime %, and an incident timeline. Strictly sanitizes private network details: internal LAN IPs (`192.168.x.x`), internal port numbers, and `.lan` / `.local` domains are hidden.

## Security & Client Analytics

**What it adds:** A dedicated security and traffic analytics dashboard at `/security` (JSON at `/api/security`).
**How it works:** Aggregates client IPs, distinguishes LAN vs WAN traffic, counts status code distributions (2xx, 3xx, 4xx, 429 rate limits, 5xx), and lists top suspicious requests (e.g. 404/403 scanning attempts).

## Caddy Control Plane & Config Inspector

**What it adds:** A live Caddy JSON configuration inspector and zero-downtime reloader at `/caddy/config` (`GET /api/caddy/config`, `POST /api/caddy/reload`).
**How it works:** Directly queries the Caddy Admin API (`GET /config/`), renders formatted JSON with one-click download, and allows administrators to safely reload Caddy configuration on the fly.

## On-Demand Diagnostics

**What it adds:** Instant "⚡ Test" button on each site card triggering `POST /api/probe/{host}`.
**How it works:** `caddy_mon.diagnostics` performs a real-time HTTP probe on the target host's upstreams, capturing connect time, latency, HTTP response code, and response headers (`Server`, `Content-Type`), returning diagnostic feedback immediately without waiting for the background polling cycle.

## Maintenance Mode

**What it adds:** An interactive "🛠️ Maint" toggle button on each card allowing administrators to place a site into planned maintenance mode.
**How it works:** When a site is in maintenance mode, its status changes to amber `MAINTENANCE` on the dashboard and status page, and automated `DOWN` alerts to Telegram / Webhooks are suppressed to avoid false alarms.

## Live Search & Status Filtering

**What it adds:** Instant search input (with `/` keyboard shortcut), status filter pills (`All`, `Alive`, `Down`, `Maint`, `>100ms`), and multi-attribute sorting (`Domain Groups`, `Latency`, `Uptime`, `Alphabetical`).
**How it works:** Real-time client-side filtering matching hostnames, aliases, or upstream IP addresses with zero lag.

## Site Deep-Dive Inspector Modal

**What it adds:** An interactive modal / drawer accessible by clicking on any site card or info button.
**How it works:** Queries `GET /api/site/{host}/details`, presenting 24h & 7d latency graphs, min/avg/max latency statistics, a live stream of the host's recent requests from the access log, and dedicated incident history.

## Automated ACME & Custom TLS Discovery

**What it adds:** Automated scanning of Caddy's ACME certificate storage (`/caddy-data` and `/data/caddy/certificates`) in addition to manual certs (`/caddy-certs`).
**How it works:** Recursively parses X.509 certificate PEMs, matching domain SANs and calculating expiration dates for Let's Encrypt, ZeroSSL, and custom certificates.

## Transport Timeouts & Connection Insights

**What it adds:** Extraction and visual display of Caddy reverse-proxy transport timeouts (`dial_timeout`, `read_timeout`, `write_timeout`, `response_header_timeout`, `keepalive`) and load-balancing parameters on dashboard cards and inside the Deep-Dive modal.

**How it works:** `caddy_mon.caddy_source` inspects the `transport` block in each Caddy reverse-proxy handler. Caddy stores durations in the admin-API JSON as Go `time.Duration` nanoseconds (e.g. `3600000000000`), so `caddy_source._fmt_duration()` converts them into compact human-readable strings (`1h`, `30s`, `500ms`) before display. The card shows e.g. `⏱️ dial 1h · read 1h` and the Deep-Dive modal lists all connection parameters under "Transport & Connection Settings", so operators immediately understand timeout behaviors for long-lived WebSockets, SSE streams, or standard APIs. Routes without an explicit `transport` block show no indicator (Caddy defaults apply).

## Per-Upstream Health Status

**What it adds:** Each upstream dial within a site is shown with its own Caddy health badge (`Caddy healthy` / `Caddy unhealthy` / `Caddy ?`) rather than only a single site-level alive/dead state.

**How it works:** `caddy_source._parse_healthy()` reads the Caddy `/metrics` gauge `caddy_reverse_proxy_upstreams_healthy{upstream="IP:port"}` — one value per upstream — and `refresh()` attaches that value to each upstream in `up_probes`. The dashboard renders a colored badge next to every upstream address on the card, so when a site has multiple upstreams you can see exactly which one is down (e.g. 2 of 3 healthy) instead of only the aggregate site status.

## Trusted Proxies & Client IP Audit

**What it adds:** Automated proxy diagnostic analyzer on the `/security` dashboard inspecting traffic distribution to detect reverse-proxy and CDN masking (e.g., Cloudflare, Docker NAT gateways).

**How it works:** `caddy_mon.security_page` evaluates incoming client IP concentration. If traffic is heavily dominated (>80%) by a known proxy network or Docker bridge gateway without client IP forwarding, it flags a warning and recommends the exact `trusted_proxies` configuration needed in Caddyfile to restore authentic visitor IPs via `X-Forwarded-For`.

## Request Transforms & Header Rules

**What it adds:** Extraction and visual rendering of route path rewrites (`strip /api`, `uri -> /v1{path}`), custom upstream request headers (`header_up`), response headers (`header_down`), and fallback handlers (`handle_response`) on the `/topology` SVG map and in the Site Deep-Dive modal.

**How it works:** `caddy_mon.caddy_source` traverses nested Caddy handler trees to detect `rewrite`, `headers`, and `handle_response` blocks. The topology view highlights transformed proxy hops with badges and SVG tooltips, and the site inspector provides a dedicated section showing all active transformation rules.

## Traffic & Visitor Analytics

**What it adds:** A server-side, privacy-friendly traffic and visitor analytics dashboard at `/analytics` (JSON at `/api/analytics`) and 24h visitor metrics integrated into each site's Deep-Dive modal.

**How it works:** `caddy_mon.log_source` parses Caddy JSON access logs to extract client IPs, request URIs, referrers, User-Agent strings, and response payloads. The engine categorizes traffic into human visitors vs search engine crawlers / bots (`Googlebot`, `Bingbot`, scanners), identifies top visited paths, referrers, browsers (Chrome, Safari, Firefox), and operating systems (Windows, macOS, iOS, Android). Hourly rollups are stored in SQLite (`traffic_hourly`), and the UI renders KPI cards, an hourly SVG timeline chart, and domain breakdown tables.

**Error metrics:** The dashboard distinguishes **server errors (5xx)** from **client errors (4xx)**. The "Server Error Rate" KPI and the per-domain 5xx column measure only 5xx responses — these indicate real backend or availability problems. The 4xx column tracks client errors (404s, 400s) separately, since high 4xx volumes are often expected (autodiscover/autoconfig probing, scanners, missing paths) and should not mask genuine outages. The prior combined 4xx+5xx error rate has been replaced by this clearer split.

## Dynamic Route CRUD & Deployment

**What it adds:** An interactive "➕ Add Site" modal on the dashboard and route deletion controls (`POST /api/routes`, `DELETE /api/routes/{host}`).
**How it works:** `caddy_mon.caddy_crud` validates FQDNs, aliases, ports, and upstreams, automatically takes a pre-modification configuration snapshot in SQLite, generates a native Caddy JSON route block, and injects it into Caddy's active memory with zero downtime. Caddy automatically provisions Let's Encrypt / ZeroSSL TLS certificates for any new domain.

## Audit Trail & Snapshot Rollback

**What it adds:** An administrative change log and backup manager at `/audit` (JSON at `/api/audit`, rollback via `POST /api/caddy/rollback/{id}`).
**How it works:** Records every route creation, deletion, and rollback in SQLite (`audit_log` table) along with before/after payloads, operator usernames, and timestamps. If an error occurs, administrators can restore any previous configuration snapshot with one click.

## Planned / future (not yet implemented)

- SSO / OIDC forward auth integration (Authelia / Authentik).
