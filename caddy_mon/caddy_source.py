"""Caddy admin-API access: fetching routes, parsing, probing, refreshing."""

import time
import asyncio
import httpx
from typing import Optional

from .config import CADDY_API, POLL_INTERVAL, PROBE_TIMEOUT
from .db import (
    init_db,
    record_snapshot,
    get_site_uptime_24h,
    get_site_sparkline,
    prune_old_history,
)
from .alerts import process_site_alerts
from .sse import broadcaster

# In-memory cache
_state = {
    "sites": [],
    "errors": [],
    "last_update": 0.0,
}

_refresh_lock = asyncio.Lock()


def _fmt_duration(value) -> str:
    """Format a Caddy duration value into a short human-readable string.

    In the Caddy JSON config, durations (dial_timeout, read_timeout, etc.)
    are Go ``time.Duration`` values expressed in **nanoseconds** (an integer),
    e.g. 3600000000000 == 3600s == 1h. Caddyfile-style strings like "30s"
    only appear in the Caddyfile, never in the admin-API JSON.

    Accepts an int (ns) or a string (already formatted) and returns a compact
    string such as "1h", "30s", "500ms".
    """
    if value is None:
        return ""
    if isinstance(value, str):
        # Already a human string (e.g. from a test fixture) — pass through.
        return value
    try:
        ns = int(value)
    except (TypeError, ValueError):
        return str(value)
    if ns <= 0:
        return "0s"
    s = ns / 1_000_000_000
    if s < 1:
        return f"{int(ns / 1_000_000)}ms"
    if s < 60:
        return f"{int(s)}s"
    if s < 3600:
        return f"{int(s // 60)}m"
    return f"{int(s // 3600)}h"


async def _get_json(path: str):
    """GET CADDY_API + path, return dict (or {"_error": ...} on failure)."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(f"{CADDY_API}{path}")
            if r.status_code != 200:
                return {"_error": f"HTTP {r.status_code}"}
            return r.json()
    except Exception as e:
        return {"_error": str(e)[:120]}


def _extract_transforms(node: dict) -> dict:
    """Extract rewrite, header modifications, and handle_response rules from a Caddy handler node."""
    tr = {
        "rewrites": [],
        "headers_up": [],
        "headers_down": [],
        "handle_response": [],
    }
    handler = node.get("handler")
    if handler == "rewrite":
        if node.get("strip_path_prefix"):
            tr["rewrites"].append(f"strip {node['strip_path_prefix']}")
        if node.get("strip_path_suffix"):
            tr["rewrites"].append(f"strip suffix {node['strip_path_suffix']}")
        if node.get("uri"):
            tr["rewrites"].append(f"rewrite -> {node['uri']}")

    # Dedicated headers handler
    if handler == "headers":
        req = node.get("request") or {}
        for action, hmap in req.items():
            if isinstance(hmap, dict):
                for hk, hvals in hmap.items():
                    val_str = ", ".join(str(v) for v in hvals) if isinstance(hvals, list) else str(hvals)
                    tr["headers_up"].append(f"{action.upper()} {hk}: {val_str}")
        resp = node.get("response") or {}
        for action, hmap in resp.items():
            if isinstance(hmap, dict):
                for hk, hvals in hmap.items():
                    val_str = ", ".join(str(v) for v in hvals) if isinstance(hvals, list) else str(hvals)
                    tr["headers_down"].append(f"{action.upper()} {hk}: {val_str}")

    # Reverse proxy inline headers, rewrite, and handle_response
    if handler == "reverse_proxy":
        # inline rewrite
        rw = node.get("rewrite") or {}
        if isinstance(rw, dict):
            if rw.get("strip_path_prefix"):
                tr["rewrites"].append(f"strip {rw['strip_path_prefix']}")
            if rw.get("uri"):
                tr["rewrites"].append(f"rewrite -> {rw['uri']}")

        # inline headers
        headers_blk = node.get("headers") or {}
        if isinstance(headers_blk, dict):
            req = headers_blk.get("request") or {}
            for action, hmap in req.items():
                if isinstance(hmap, dict):
                    for hk, hvals in hmap.items():
                        val_str = ", ".join(str(v) for v in hvals) if isinstance(hvals, list) else str(hvals)
                        tr["headers_up"].append(f"{action.upper()} {hk}: {val_str}")
            resp = headers_blk.get("response") or {}
            for action, hmap in resp.items():
                if isinstance(hmap, dict):
                    for hk, hvals in hmap.items():
                        val_str = ", ".join(str(v) for v in hvals) if isinstance(hvals, list) else str(hvals)
                        tr["headers_down"].append(f"{action.upper()} {hk}: {val_str}")

        # handle_response
        hr_list = node.get("handle_response") or []
        if isinstance(hr_list, list):
            for hr in hr_list:
                if isinstance(hr, dict):
                    codes = hr.get("match", {}).get("status_code", [])
                    codes_str = ", ".join(str(c) for c in codes) if codes else "any"
                    tr["handle_response"].append(f"catch status [{codes_str}]")

    return {k: v for k, v in tr.items() if v}


def _parse_routes(routes):
    """Return list of {hosts:[...], paths:[...], upstreams:[...], transport: {...}, load_balancing: {...}, transforms: {...}}.

    Supports nested `subroute` handlers, path matchers, transport timeouts, load-balancing policies, and request/response transforms.
    """
    out = []

    def walk(node, inherited_paths, inherited_transforms=None):
        nonlocal branches
        current_transforms = dict(inherited_transforms or {})
        if isinstance(node, dict):
            extracted = _extract_transforms(node)
            for k, v in extracted.items():
                current_transforms.setdefault(k, [])
                for item in v:
                    if item not in current_transforms[k]:
                        current_transforms[k].append(item)

            if node.get("handler") == "reverse_proxy":
                ups = [u.get("dial") for u in node.get("upstreams", []) if u.get("dial")]
                paths = inherited_paths or ["/"]

                # Extract transport settings (dial_timeout, read_timeout, write_timeout, keepalive)
                tr = node.get("transport") or {}
                tr_info = {}
                if isinstance(tr, dict):
                    for k in ("dial_timeout", "read_timeout", "write_timeout", "response_header_timeout", "protocol"):
                        if tr.get(k):
                            # Durations arrive from the Caddy JSON API in nanoseconds.
                            tr_info[k] = _fmt_duration(tr.get(k)) if k != "protocol" else str(tr.get(k))
                    keepalive = tr.get("keepalive") or {}
                    if isinstance(keepalive, dict) and keepalive.get("idle_timeout"):
                        tr_info["keepalive_idle"] = _fmt_duration(keepalive.get("idle_timeout"))

                # Extract load-balancing policy & retries
                lb = node.get("load_balancing") or {}
                lb_info = {}
                if isinstance(lb, dict):
                    sel = lb.get("selection_policy")
                    if isinstance(sel, dict) and sel.get("policy"):
                        lb_info["policy"] = sel.get("policy")
                    elif isinstance(sel, str):
                        lb_info["policy"] = sel
                    if lb.get("retries") is not None:
                        lb_info["retries"] = lb.get("retries")
                    if lb.get("try_duration"):
                        lb_info["try_duration"] = _fmt_duration(lb.get("try_duration"))

                for p in paths:
                    entry = {"paths": [p], "upstreams": ups}
                    if tr_info:
                        entry["transport"] = tr_info
                    if lb_info:
                        entry["load_balancing"] = lb_info
                    if current_transforms:
                        entry["transforms"] = {k: list(v) for k, v in current_transforms.items() if v}
                    branches.append(entry)

            routes_block = node.get("routes")
            if isinstance(routes_block, list):
                sub_accum = dict(current_transforms)
                for sub in routes_block:
                    sub_paths = []
                    for m in sub.get("match", []) or []:
                        for p in (m.get("path") or []):
                            sub_paths.append(p)
                    # Extract any transforms from sub handle before walking
                    h = sub.get("handle")
                    if isinstance(h, list):
                        for hitem in h:
                            if isinstance(hitem, dict):
                                extracted = _extract_transforms(hitem)
                                for k, v in extracted.items():
                                    sub_accum.setdefault(k, [])
                                    for item in v:
                                        if item not in sub_accum[k]:
                                            sub_accum[k].append(item)
                    elif isinstance(h, dict):
                        extracted = _extract_transforms(h)
                        for k, v in extracted.items():
                            sub_accum.setdefault(k, [])
                            for item in v:
                                if item not in sub_accum[k]:
                                    sub_accum[k].append(item)

                    walk(sub.get("handle"), sub_paths or inherited_paths, sub_accum)
            for k, v in node.items():
                if isinstance(v, (dict, list)) and not any(
                    key in node for key in ("handle", "terminal", "routes", "subroutes")
                ):
                    walk(v, inherited_paths, current_transforms)
            if "terminal" in node:
                walk(node.get("terminal"), inherited_paths, current_transforms)
        elif isinstance(node, list):
            accum_transforms = dict(inherited_transforms or {})
            for x in node:
                if isinstance(x, dict):
                    extracted = _extract_transforms(x)
                    for k, v in extracted.items():
                        accum_transforms.setdefault(k, [])
                        for item in v:
                            if item not in accum_transforms[k]:
                                accum_transforms[k].append(item)
                walk(x, inherited_paths, accum_transforms)

    for r in routes or []:
        hosts = []
        for m in r.get("match", []) or []:
            for h in (m.get("host") or []):
                hosts.append(h)
        # Collect (path, upstreams) branches from handlers
        branches = []
        walk(r.get("handle"), [])
        walk(r.get("terminal"), [])
        # Also check top-level routes key (some Caddy configs nest differently)
        if not branches:
            walk(r.get("routes"), [])

        # Flatten: if no path-specific branches, treat as root path "/"
        if not branches:
            branches = [{"paths": ["/"], "upstreams": []}]
        # Deduplicate identical (paths, upstreams) branches
        seen = set()
        deduped = []
        site_transport = {}
        site_lb = {}
        site_transforms = {}
        for b in branches:
            key = (tuple(b["paths"]), tuple(b["upstreams"]))
            if key not in seen:
                seen.add(key)
                deduped.append(b)
            if b.get("transport"):
                site_transport.update(b["transport"])
            if b.get("load_balancing"):
                site_lb.update(b["load_balancing"])
            if b.get("transforms"):
                for k, v in b["transforms"].items():
                    site_transforms.setdefault(k, [])
                    for item in v:
                        if item not in site_transforms[k]:
                            site_transforms[k].append(item)

        branches = deduped
        # Aggregate upstreams across branches for the site-level view
        all_ups = []
        for b in branches:
            for u in b["upstreams"]:
                if u not in all_ups:
                    all_ups.append(u)

        if hosts:
            out.append({
                "hosts": hosts,
                "paths": branches,
                "upstreams": all_ups,
                "transport": site_transport or None,
                "load_balancing": site_lb or None,
                "transforms": site_transforms or None,
            })
    return out


def _parse_healthy(metrics_text: str):
    """Caddy /metrics -> {upstream_dial: bool}."""
    result = {}
    for line in (metrics_text or "").splitlines():
        if line.startswith("caddy_reverse_proxy_upstreams_healthy"):
            try:
                label_part = line.split("{", 1)[1].split("}", 1)[0]
                val = line.rsplit(" ", 1)[-1].strip()
                key = label_part.split("=", 1)[1].strip('"')
                result[key] = (val == "1")
            except Exception:
                pass
    return result


async def _probe_async(upstream: str):
    """Quick GET probe. Returns (ok, status, ms, error)."""
    if upstream.startswith("https://") or upstream.startswith("http://"):
        return (False, 0, 0.0, "scheme_not_supported")
    url = f"http://{upstream}"
    try:
        start = time.monotonic()
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as c:
            r = await c.get(url, headers={"User-Agent": "caddy-mon-probe/0.1"})
        elapsed = (time.monotonic() - start) * 1000.0
        return (True, r.status_code, round(elapsed, 1), None)
    except Exception as e:
        return (False, 0, 0.0, str(e)[:80])


def _site_tls(hosts):
    """Find the TLS cert covering any of `hosts`, return {days_left, warn} or None.

    Matches against cert SANs. The first matching cert (soonest-expiring)
    wins so the most urgent expiry shows on the dashboard card.
    """
    from .tls_source import cert_status
    best = None
    for entry in cert_status():
        cert_hosts = set(entry.get("hosts") or [])
        if cert_hosts & set(hosts):
            if best is None or entry["days_left"] < best["days_left"]:
                best = {"days_left": entry["days_left"], "warn": entry["warn"]}
    return best


async def refresh(force: bool = False):
    """Poll Caddy, update SQLite history, trigger alerts, broadcast SSE updates."""
    from .log_source import ingest_logs, host_log_stats  # local import to avoid cycle

    now = time.time()
    if not force and (now - _state["last_update"] < POLL_INTERVAL) and _state["sites"]:
        return

    async with _refresh_lock:
        # Double check cache inside lock
        if not force and (time.time() - _state["last_update"] < POLL_INTERVAL) and _state["sites"]:
            return

        ingest_logs()

        errors = []
        servers_cfg = await _get_json("/config/apps/http/servers")
        if "_error" in servers_cfg:
            errors.append(f"Caddy admin API unreachable: {servers_cfg['_error']}")
            _state["errors"] = errors
            return

        all_routes = []
        if isinstance(servers_cfg, dict):
            for srv_name, srv_cfg in servers_cfg.items():
                if not isinstance(srv_cfg, dict):
                    continue
                srv_routes = srv_cfg.get("routes", [])
                if isinstance(srv_routes, list):
                    all_routes.extend(srv_routes)
        elif isinstance(servers_cfg, list):
            all_routes = servers_cfg

        parsed = _parse_routes(all_routes) if all_routes else []

        metrics_text = ""
        try:
            async with httpx.AsyncClient(timeout=5.0) as c:
                metrics_text = (await c.get(f"{CADDY_API}/metrics")).text
        except Exception as e:
            errors.append(f"metrics: {e}")
        healthy = _parse_healthy(metrics_text)

        # Global parallel probing: probe all unique upstreams across all sites at once
        unique_upstreams = list({up for s in parsed for up in s["upstreams"]})
        probe_results = {}
        if unique_upstreams:
            raw_results = await asyncio.gather(
                *(_probe_async(up) for up in unique_upstreams),
                return_exceptions=True,
            )
            for up, res in zip(unique_upstreams, raw_results):
                if isinstance(res, Exception):
                    probe_results[up] = (False, 0, 0.0, str(res)[:80])
                else:
                    probe_results[up] = res

        sites = []
        for s in parsed:
            up_probes = []
            for up in s["upstreams"]:
                ok, status, ms, err = probe_results.get(
                    up, (False, 0, 0.0, "not_probed")
                )
                up_probes.append({
                    "upstream": up,
                    "caddy_healthy": healthy.get(up),
                    "probe_ok": ok,
                    "status": status,
                    "ms": ms,
                    "error": err,
                })

            def upstream_ok(u):
                h = u["caddy_healthy"]
                if h is False:
                    return False
                if h is True:
                    if (not u["probe_ok"]) and "refused" in (u["error"] or "").lower():
                        return False
                    return True
                return u["probe_ok"] and u["status"] < 500

            alive = all(upstream_ok(u) for u in up_probes)
            worst_ms = max((u["ms"] for u in up_probes if u["probe_ok"]), default=0.0)
            group = _tld_group(s["hosts"][0])
            log = host_log_stats(s["hosts"], window=3600)
            tls = _site_tls(s["hosts"])
            primary = s["hosts"][0]
            uptime_24h = get_site_uptime_24h(primary, now=now)
            sparkline = get_site_sparkline(primary, now=now)

            sites.append({
                "hosts": s["hosts"],
                "primary_host": primary,
                "group": group,
                "paths": s["paths"],
                "upstreams": up_probes,
                "alive": alive,
                "latency_ms": worst_ms,
                "log": log,
                "tls": tls,
                "uptime_24h": uptime_24h,
                "sparkline": sparkline,
                "transport": s.get("transport"),
                "load_balancing": s.get("load_balancing"),
                "transforms": s.get("transforms"),
            })

        _state["sites"] = sites
        _state["errors"] = errors
        _state["last_update"] = now

        # Persist to SQLite, check alerts, broadcast to active SSE subscribers
        record_snapshot(sites, now=now)
        prune_old_history(now=now)
        await process_site_alerts(sites, now=now)
        await broadcaster.broadcast(
            "state_update",
            {
                "last_update": now,
                "sites": sites,
                "errors": errors,
            },
        )


async def background_poll_loop():
    """Continuous background worker ensuring 24/7 metrics history and alerting."""
    init_db()
    while True:
        try:
            await refresh(force=True)
        except Exception:
            pass
        await asyncio.sleep(POLL_INTERVAL)


def _tld_group(host: str) -> str:
    """Group a host by its parent domain (last two labels).

    example.ee -> example.ee, sub.example.ee -> example.ee
    192.168.1.9 -> 1.9 (best effort for IPs)
    """
    parts = host.split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host


def _group_hosts_by_tld(sites):
    """Group sites by their parent domain (TLD-group) for display."""
    groups = {}
    for s in sites:
        g = groups.setdefault(s["group"], {"group": s["group"], "sites": []})
        g["sites"].append(s)
    return sorted(groups.values(), key=lambda x: x["group"])
