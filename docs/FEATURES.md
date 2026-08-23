# Features

Detailed writeups of each caddy-mon feature.

## Health dashboard

**What it adds:** A single web page showing every proxied site as a card with
alive/dead status (green/red), latency in ms, and the last-update timestamp.
Auto-refreshes every 12 seconds.

**How it works:** `refresh()` polls Caddy, builds the site list, and the
dashboard route renders an HTML grid. The same data is available as JSON at
`/api/state` for external consumers.

**Why:** Gives immediate visibility into proxy health without Grafana/Prometheus.

## Caddy-authoritative health

**What it adds:** Site health is taken from Caddy's
`caddy_reverse_proxy_upstreams_healthy` metric, not from caddy-mon's own probe.

**How it works:** `_parse_healthy()` extracts the 0/1 value per upstream from
`/metrics`. In the site decision, `caddy_healthy is True` means alive
regardless of probe result.

**Why:** Caddy knows the real backend state (including TLS/protocol quirks).
A self-probe can fail for reasons Caddy has already accounted for.

## Local timezone

**What it adds:** The "updated" timestamp on the dashboard uses the host's
local timezone instead of UTC.

**How it works:** `TZ = ZoneInfo("Europe/Tallinn")` and
`datetime.now(TZ).strftime(...)`. The container has `tzdata` installed so
`ZoneInfo` resolves.

**Why:** The server runs in UTC but the user is in a different zone; a UTC
timestamp is confusing ("why does it say 07:23 when it's 10:23?").

## Fixed site order

**What it adds:** Site cards stay in the order defined by the Caddyfile
(Caddy route order), and do not shuffle on every refresh.

**How it works:** `refresh()` does NOT sort by health. The list order follows
the route list from the admin API, which mirrors the Caddyfile.

**Why:** Sorting by health made cards jump up/down on every 12s refresh,
which was visually distracting.

## False-negative handling

**What it adds:** If Caddy reports `healthy=1` but the self-probe fails, the
site is shown as alive (not "probe FAIL").

**How it works:** In the card renderer, a probe error is only displayed when
`caddy_healthy is None` (Caddy gave no health signal). When Caddy says healthy,
the probe error is suppressed and "Caddy: alive" is shown instead.

**Why:** Example — Immich (`pildid.lope.ee`) does not answer plain HTTP on its
port, so the probe fails, but Caddy knows it is up. Showing "FAIL" there was
a false alarm.

---

## Planned / future (not yet implemented)

See the development plan (private, not in this repo) for the backlog:
route map / topology, log analytics, TLS expiry tracking, Telegram alerts.
