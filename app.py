"""
caddy-mon — minimal Caddy reverse-proxy visibility.

Runs in the same Docker network as caddy-proxy (caddy_default), so the
DNS name `caddy-proxy` resolves and the admin API (port 2019) is reachable.

Data sources:
  - GET caddy-proxy:2019/config/apps/http/servers/srv0/routes
        -> each route: host matchers + reverse_proxy upstream dial
  - GET caddy-proxy:2019/metrics
        -> caddy_reverse_proxy_upstreams_healthy{upstream="IP:port"} 0/1
  - self-made HTTP GET probe to each upstream -> latency + status

No Prometheus, no Grafana.
"""

import json
import time
import httpx
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

TZ = ZoneInfo("Europe/Tallinn")

CADDY_API = "http://caddy-proxy:2019"
POLL_INTERVAL = 10  # seconds
PROBE_TIMEOUT = 3.0  # seconds, single probe

app = FastAPI()

# In-memory cache (no database needed)
_state = {
    "sites": [],
    "last_update": 0,
    "errors": [],
}


def _get_json(path: str):
    try:
        with httpx.Client(timeout=5.0) as c:
            r = c.get(f"{CADDY_API}{path}")
            r.raise_for_status()
            return r.json()
    except Exception as e:
        return {"_error": str(e)}


def _parse_routes(routes):
    """Return list of {hosts:[...], upstreams:[...]}."""
    out = []
    for r in routes or []:
        hosts = []
        ups = []
        for m in r.get("match", []) or []:
            hosts += m.get("host", [])
        # find reverse_proxy handler (in handle or terminal)
        def walk(node):
            if isinstance(node, dict):
                if node.get("handler") == "reverse_proxy":
                    for u in node.get("upstreams", []):
                        if u.get("dial"):
                            ups.append(u["dial"])
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for x in node:
                    walk(x)
        walk(r.get("handle"))
        walk(r.get("terminal"))
        if hosts and ups:
            out.append({"hosts": hosts, "upstreams": ups})
    return out


def _parse_healthy(metrics_text: str):
    """Caddy /metrics -> {upstream_dial: bool}."""
    result = {}
    for line in (metrics_text or "").splitlines():
        if line.startswith("caddy_reverse_proxy_upstreams_healthy"):
            # caddy_reverse_proxy_upstreams_healthy{upstream="<server-ip>:3000"} 1
            try:
                label_part = line.split("{", 1)[1].split("}", 1)[0]
                val = line.rsplit(" ", 1)[-1].strip()
                # label_part == upstream="<server-ip>:3000"
                key = label_part.split("=", 1)[1].strip('"')
                result[key] = (val == "1")
            except Exception:
                pass
    return result


def _probe(upstream: str):
    """Quick GET probe. Returns (ok, status, ms, error)."""
    # upstream is "IP:port"; assume http
    if upstream.startswith("https://") or upstream.startswith("http://"):
        return (False, 0, 0.0, "scheme_not_supported")
    url = f"http://{upstream}"
    try:
        start = time.monotonic()
        with httpx.Client(timeout=PROBE_TIMEOUT) as c:
            r = c.get(url, headers={"User-Agent": "caddy-mon-probe/0.1"})
        elapsed = (time.monotonic() - start) * 1000.0
        return (True, r.status_code, round(elapsed, 1), None)
    except Exception as e:
        return (False, 0, 0.0, str(e)[:80])


def refresh():
    now = time.time()
    if now - _state["last_update"] < POLL_INTERVAL and _state["sites"]:
        return

    errors = []
    routes = _get_json("/config/apps/http/servers/srv0/routes")
    if "_error" in routes:
        errors.append(f"Caddy admin API unreachable: {routes['_error']}")
        _state["errors"] = errors
        return

    # admin API returns the /routes endpoint as a bare list (not {"routes": [...]})
    if isinstance(routes, list):
        parsed = _parse_routes(routes)
    elif isinstance(routes, dict) and "routes" in routes:
        parsed = _parse_routes(routes["routes"])
    else:
        parsed = []

    # /metrics is Prometheus text (not JSON), fetch it directly
    metrics_text = ""
    try:
        with httpx.Client(timeout=5.0) as c:
            metrics_text = c.get(f"{CADDY_API}/metrics").text
    except Exception as e:
        errors.append(f"metrics: {e}")
    healthy = _parse_healthy(metrics_text)

    sites = []
    for s in parsed:
        up_probes = []
        for up in s["upstreams"]:
            ok, status, ms, err = _probe(up)
            up_probes.append({
                "upstream": up,
                "caddy_healthy": healthy.get(up),  # None = unknown
                "probe_ok": ok,
                "status": status,
                "ms": ms,
                "error": err,
            })
        # Site is alive if every upstream is OK (Caddy healthy=1 or probe succeeds)
        alive = all(
            (u["caddy_healthy"] is True) or (u["probe_ok"] and u["status"] < 500)
            for u in up_probes
        )
        worst_ms = max((u["ms"] for u in up_probes if u["probe_ok"]), default=0.0)
        group = _tld_group(s["hosts"][0])
        sites.append({
            "hosts": s["hosts"],
            "primary_host": s["hosts"][0],
            "group": group,
            "upstreams": up_probes,
            "alive": alive,
            "latency_ms": worst_ms,
        })

    # ORDERING: fixed = Caddy route order (Caddyfile order).
    # Do not sort by health, otherwise cards jump up/down on every refresh.
    # (if you want dead-first, remove this comment and enable sorting below)
    # sites.sort(key=lambda x: (x["alive"], -x["latency_ms"]))

    _state["sites"] = sites
    _state["last_update"] = now
    _state["errors"] = errors


def _group_hosts_by_tld(sites):
    """Group sites by their parent domain (TLD-group) for display."""
    groups = {}
    for s in sites:
        g = groups.setdefault(s["group"], {"group": s["group"], "sites": []})
        g["sites"].append(s)
    return sorted(groups.values(), key=lambda x: x["group"])


def _tld_group(host: str) -> str:
    """Return the group key for a host: the last two labels (e.g. lope.ee)."""
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    refresh()
    sites = _state["sites"]
    errors = _state["errors"]
    grouped = _group_hosts_by_tld(sites)
    total = sum(len(g["sites"]) for g in grouped)
    alive = sum(1 for g in grouped for s in g["sites"] if s["alive"])

    groups_html = ""
    for g in grouped:
        cards = ""
        for s in g["sites"]:
            color = "#16a34a" if s["alive"] else "#dc2626"
            up_html = ""
            for u in s["upstreams"]:
                if u["caddy_healthy"] is True:
                    badge = "Caddy healthy"
                    bcolor = "#16a34a"
                elif u["caddy_healthy"] is False:
                    badge = "Caddy UNhealthy"
                    bcolor = "#dc2626"
                else:
                    badge = "?"
                    bcolor = "#6b7280"
                # If Caddy reports healthy=1, a probe failure is a false negative
                # (e.g. Immich doesn't answer plain HTTP) -> don't show it as a failure.
                # If Caddy reports no health (None), the probe is our only signal.
                show_probe_err = (not u["probe_ok"]) and (u["caddy_healthy"] is None)
                probe = f"{u['status']} / {u['ms']}ms" if u["probe_ok"] else (f"probe failed: {u['error']}" if show_probe_err else "Caddy: alive")
                up_html += f"""
                  <div class="up">
                    <span class="badge" style="background:{bcolor}">{badge}</span>
                    <code>{u['upstream']}</code>
                    <span class="probe">{probe}</span>
                  </div>"""
            hosts = " · ".join(s["hosts"])
            cards += f"""
              <div class="card" style="border-left:6px solid {color}">
                <div class="host">{s['primary_host']}</div>
                <div class="hosts-all">{hosts}</div>
                <div class="status" style="color:{color}">{('ALIVE' if s['alive'] else 'DEAD')} · {s['latency_ms']}ms</div>
                {up_html}
              </div>"""
        groups_html += f"""
          <div class="domain-group">
            <h2>{g['group']}</h2>
            <div class="grid">{cards}</div>
          </div>"""

    err_html = "".join(f"<p class='err'>⚠ {e}</p>" for e in errors)

    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Caddy Mon</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ font-family: system-ui, sans-serif; background:#0f1115; color:#e5e7eb; margin:0; padding:24px; }}
  h1 {{ font-size:20px; margin:0 0 4px; }}
  .sub {{ color:#9ca3af; font-size:13px; margin-bottom:20px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(300px,1fr)); gap:14px; }}
  .domain-group {{ margin-bottom:28px; }}
  .domain-group h2 {{ font-size:15px; color:#9ca3af; margin:0 0 12px; font-weight:600; border-bottom:1px solid #2a2d35; padding-bottom:6px; }}
  .card {{ background:#1a1d24; border-radius:10px; padding:14px 16px; }}
  .host {{ font-weight:600; font-size:16px; }}
  .hosts-all {{ color:#9ca3af; font-size:11px; margin-bottom:6px; word-break:break-all; }}
  .status {{ font-weight:700; font-size:14px; margin-bottom:8px; }}
  .up {{ display:flex; align-items:center; gap:8px; font-size:12px; margin-top:6px; flex-wrap:wrap; }}
  .badge {{ color:#fff; padding:2px 6px; border-radius:5px; font-size:11px; white-space:nowrap; }}
  .probe {{ color:#9ca3af; }}
  code {{ color:#cbd5e1; }}
  .err {{ color:#fbbf24; }}
  .count {{ color:#9ca3af; font-size:13px; }}
</style></head>
<body>
  <h1>Caddy Mon</h1>
  <div class="sub">Caddy reverse-proxy live status · {total} sites · {alive} alive · updated {datetime.now(TZ).strftime('%H:%M:%S')} · <a href="/topology" style="color:#60a5fa">topology</a></div>
  {err_html}
  <div class="groups">{groups_html}</div>
  <script>
    // Auto-refresh every 12s
    setTimeout(() => location.reload(), 12000);
  </script>
</body></html>"""
    return HTMLResponse(html)


@app.get("/api/state")
def api_state():
    refresh()
    return {
        "last_update": _state["last_update"],
        "sites": _state["sites"],
        "errors": _state["errors"],
    }


@app.get("/api/topology")
def api_topology():
    """Return graph nodes + edges for the route map.

    Nodes: each host (left), one proxy node per site (middle),
    each upstream dial (right).
    Edges: host -> proxy -> upstream.
    """
    refresh()
    nodes = []
    edges = []
    node_ids = set()

    def add_node(nid, label, col, kind, healthy=None):
        if nid not in node_ids:
            nodes.append({"id": nid, "label": label, "col": col, "kind": kind, "healthy": healthy})
            node_ids.add(nid)

    for s in _state["sites"]:
        site_id = f"site:{s['primary_host']}"
        add_node(site_id, s["primary_host"], 0, "host")
        # proxy node (the reverse_proxy handler)
        proxy_id = f"proxy:{s['primary_host']}"
        add_node(proxy_id, "Caddy proxy", 1, "proxy")
        edges.append({"from": site_id, "to": proxy_id, "healthy": s["alive"]})
        for u in s["upstreams"]:
            up_id = f"up:{u['upstream']}"
            add_node(up_id, u["upstream"], 2, "upstream", healthy=u["caddy_healthy"])
            edges.append({"from": proxy_id, "to": up_id, "healthy": u["caddy_healthy"]})
    return {"nodes": nodes, "edges": edges}


@app.get("/topology", response_class=HTMLResponse)
def topology(request: Request):
    refresh()
    data = api_topology()
    nodes = data["nodes"]
    edges = data["edges"]

    # Layout: 3 columns (x by col), stacked vertically per column
    col_x = {0: 40, 1: 320, 2: 600}
    col_cursor = {0: 20, 1: 20, 2: 20}
    row_h = 46
    pos = {}
    for n in nodes:
        y = col_cursor[n["col"]]
        col_cursor[n["col"]] += row_h
        pos[n["id"]] = (col_x[n["col"]], y)

    # SVG dimensions
    max_y = max((y for _, y in pos.values()), default=100) + 60
    width = 900
    height = max_y

    svg = [f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">']
    # edges first (behind nodes)
    for e in edges:
        x1, y1 = pos[e["from"]]
        x2, y2 = pos[e["to"]]
        color = "#16a34a" if e.get("healthy") is True else ("#dc2626" if e.get("healthy") is False else "#6b7280")
        svg.append(f'<line x1="{x1+150}" y1="{y1+18}" x2="{x2}" y2="{y2+18}" stroke="{color}" stroke-width="1.5" marker-end="url(#arrow)"/>')
    # nodes
    for n in nodes:
        x, y = pos[n["id"]]
        if n["kind"] == "host":
            fill, stroke = "#1e3a5f", "#3b82f6"
        elif n["kind"] == "proxy":
            fill, stroke = "#3f6212", "#84cc16"
        else:
            h = n.get("healthy")
            if h is True:
                fill, stroke = "#14321f", "#16a34a"
            elif h is False:
                fill, stroke = "#3a1e1e", "#f87171"
            else:
                fill, stroke = "#2a2a2a", "#9ca3af"
        svg.append(f'<g><rect x="{x}" y="{y}" width="150" height="36" rx="6" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>'
                   f'<text x="{x+75}" y="{y+22}" fill="#e5e7eb" font-size="12" text-anchor="middle" font-family="system-ui">{n["label"][:22]}</text></g>')
    svg.append('<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L0,6 L9,3 z" fill="#9ca3af"/></marker></defs>')
    svg.append('</svg>')

    total = len(_state["sites"])
    alive = sum(1 for s in _state["sites"] if s["alive"])
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Caddy Mon — Topology</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ font-family: system-ui, sans-serif; background:#0f1115; color:#e5e7eb; margin:0; padding:24px; }}
  h1 {{ font-size:20px; margin:0 0 4px; }}
  .sub {{ color:#9ca3af; font-size:13px; margin-bottom:20px; }}
  .legend {{ display:flex; gap:16px; margin-bottom:16px; font-size:12px; color:#9ca3af; }}
  .legend span {{ display:inline-flex; align-items:center; gap:6px; }}
  .box {{ width:12px; height:12px; border-radius:3px; display:inline-block; }}
  a {{ color:#60a5fa; }}
</style></head>
<body>
  <h1>Caddy Mon — Route Topology</h1>
  <div class="sub">{total} sites · {alive} alive · updated {datetime.now(TZ).strftime('%H:%M:%S')} · <a href="/">dashboard</a></div>
  <div class="legend">
    <span><i class="box" style="background:#3b82f6"></i> host</span>
    <span><i class="box" style="background:#84cc16"></i> reverse_proxy</span>
    <span><i class="box" style="background:#16a34a"></i> upstream (green=healthy)</span>
    <span><i class="box" style="background:#f87171"></i> upstream (red=unhealthy)</span>
    <span><i class="box" style="background:#9ca3af"></i> upstream (gray=unknown)</span>
  </div>
  {''.join(svg)}
  <script>
    setTimeout(() => location.reload(), 12000);
  </script>
</body></html>"""
    return HTMLResponse(html)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
