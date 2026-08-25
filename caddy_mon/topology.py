"""Route topology: API + SVG HTML page."""

from html import escape
from fastapi import Request
from fastapi.responses import HTMLResponse
from .config import TZ
from .caddy_source import _state, _group_hosts_by_tld
from datetime import datetime


def api_topology():
    """Return graph nodes + edges for the route map.

    Nodes: each host (left), each path matcher (left-mid), one proxy node
    per (site, path) branch (mid-right), each upstream dial (right).
    Edges: host -> path -> proxy -> upstream.
    """
    sites = _state["sites"]
    nodes = []
    edges = []
    node_ids = set()

    def add_node(nid, label, col, kind, healthy=None):
        if nid not in node_ids:
            nodes.append({"id": nid, "label": label, "col": col, "kind": kind, "healthy": healthy})
            node_ids.add(nid)

    for s in sites:
        site_id = f"site:{s['primary_host']}"
        add_node(site_id, s["primary_host"], 0, "host")
        for branch in s["paths"]:
            path_label = " / ".join(branch["paths"]) if branch["paths"] else "/"
            path_id = f"{site_id}|path|{path_label}"
            add_node(path_id, path_label, 1, "path")
            edges.append({"from": site_id, "to": path_id, "healthy": s["alive"]})
            proxy_id = f"{path_id}|proxy"
            add_node(proxy_id, "Caddy proxy", 2, "proxy")
            edges.append({"from": path_id, "to": proxy_id, "healthy": s["alive"]})
            for up in branch["upstreams"]:
                up_id = f"up:{up}"
                up_healthy = None
                for u in s["upstreams"]:
                    if u["upstream"] == up:
                        up_healthy = u["caddy_healthy"]
                        break
                add_node(up_id, up, 3, "upstream", healthy=up_healthy)
                edges.append({
                    "from": proxy_id,
                    "to": up_id,
                    "healthy": (up_healthy is True),
                })
    return {"nodes": nodes, "edges": edges}


def topology(request: Request):
    from .caddy_source import refresh
    refresh()
    data = api_topology()
    nodes = data["nodes"]
    edges = data["edges"]

    col_x = {0: 40, 1: 280, 2: 520, 3: 760}
    col_cursor = {0: 20, 1: 20, 2: 20, 3: 20}
    row_h = 46
    pos = {}
    for n in nodes:
        y = col_cursor[n["col"]]
        col_cursor[n["col"]] += row_h
        pos[n["id"]] = (col_x[n["col"]], y)

    svg = ['<svg width="960" height="{}" font-family="system-ui">'.format(
        max(col_cursor.values()) + 20)]

    for e in edges:
        x1, y1 = pos[e["from"]]
        x2, y2 = pos[e["to"]]
        color = "#16a34a" if e.get("healthy") is True else ("#dc2626" if e.get("healthy") is False else "#6b7280")
        svg.append(f'<line x1="{x1+150}" y1="{y1+18}" x2="{x2}" y2="{y2+18}" stroke="{color}" stroke-width="1.5" marker-end="url(#arrow)"/>')

    for n in nodes:
        x, y = pos[n["id"]]
        if n["kind"] == "host":
            fill, stroke = "#1e3a5f", "#3b82f6"
        elif n["kind"] == "path":
            fill, stroke = "#3a2e12", "#f59e0b"
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
                   f'<text x="{x+75}" y="{y+22}" fill="#e5e7eb" font-size="12" text-anchor="middle" font-family="system-ui">{escape(n["label"][:22])}</text></g>')
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
    <span><i class="box" style="background:#f59e0b"></i> path</span>
    <span><i class="box" style="background:#84cc16"></i> Caddy proxy</span>
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
