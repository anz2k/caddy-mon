"""Caddy admin-API access: fetching routes, parsing, probing, refreshing."""

import time
import asyncio
import httpx
from .config import CADDY_API, POLL_INTERVAL, PROBE_TIMEOUT

# In-memory cache (no database needed).
_state = {
    "sites": [],
    "errors": [],
    "last_update": 0.0,
}


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


def _parse_routes(routes):
    """Return list of {hosts:[...], paths:[...], upstreams:[...]}.

    Supports nested `subroute` handlers and path matchers. A path matcher
    scoped to a reverse_proxy branch becomes its own (path, upstreams) entry.
    """
    out = []

    def walk(node, inherited_paths):
        nonlocal branches
        if isinstance(node, dict):
            if node.get("handler") == "reverse_proxy":
                ups = [u.get("dial") for u in node.get("upstreams", []) if u.get("dial")]
                paths = inherited_paths or ["/"]
                for p in paths:
                    branches.append({"paths": [p], "upstreams": ups})
            routes_block = node.get("routes")
            if isinstance(routes_block, list):
                for sub in routes_block:
                    sub_paths = []
                    for m in sub.get("match", []) or []:
                        for p in (m.get("path") or []):
                            sub_paths.append(p)
                    walk(sub.get("handle"), sub_paths or inherited_paths)
            for k, v in node.items():
                if isinstance(v, (dict, list)) and not any(
                    key in node for key in ("handle", "terminal", "routes", "subroutes")
                ):
                    walk(v, inherited_paths)
            if "terminal" in node:
                walk(node.get("terminal"), inherited_paths)
        elif isinstance(node, list):
            for x in node:
                walk(x, inherited_paths)

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
        for b in branches:
            key = (tuple(b["paths"]), tuple(b["upstreams"]))
            if key not in seen:
                seen.add(key)
                deduped.append(b)
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


async def refresh():
    """Poll Caddy and rebuild _state["sites"]."""
    from .log_source import ingest_logs, host_log_stats  # local import to avoid cycle

    now = time.time()
    if now - _state["last_update"] < POLL_INTERVAL and _state["sites"]:
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

    sites = []
    for s in parsed:
        # Probe all upstreams for this site in parallel.
        results = await asyncio.gather(
            *(_probe_async(up) for up in s["upstreams"]),
            return_exceptions=True,
        )
        up_probes = []
        for up, res in zip(s["upstreams"], results):
            if isinstance(res, Exception):
                ok, status, ms, err = False, 0, 0.0, str(res)[:80]
            else:
                ok, status, ms, err = res
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
        log = host_log_stats(s["hosts"][0], window=3600)
        tls = _site_tls(s["hosts"])
        sites.append({
            "hosts": s["hosts"],
            "primary_host": s["hosts"][0],
            "group": group,
            "paths": s["paths"],
            "upstreams": up_probes,
            "alive": alive,
            "latency_ms": worst_ms,
            "log": log,
            "tls": tls,
        })

    _state["sites"] = sites
    _state["errors"] = errors
    _state["last_update"] = now


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
