"""Route topology: API + SVG HTML page with modern Tailwind CSS design."""

from html import escape
from datetime import datetime
from typing import Any

try:
    from fastapi import Request
    from fastapi.responses import HTMLResponse
except ImportError:
    Request = Any  # type: ignore
    HTMLResponse = Any  # type: ignore

from .config import TZ
from .caddy_source import _state, _group_hosts_by_tld


def api_topology():
    """Return graph nodes + edges for the route map.

    Nodes: each host (left), each path matcher (left-mid), one proxy node
    per (site, path) branch (mid-right), each upstream dial (right).
    Edges: host -> path -> proxy -> upstream.
    """
    sites = _state.get("sites", [])
    nodes = []
    edges = []
    node_ids = set()

    def add_node(nid, label, col, kind, healthy=None, transforms=None):
        if nid not in node_ids:
            nodes.append({"id": nid, "label": label, "col": col, "kind": kind, "healthy": healthy, "transforms": transforms})
            node_ids.add(nid)

    for s in sites:
        site_id = f"site:{s['primary_host']}"
        add_node(site_id, s["primary_host"], 0, "host")
        for branch in s.get("paths", []):
            path_label = " / ".join(branch["paths"]) if branch["paths"] else "/"
            path_id = f"{site_id}|path|{path_label}"
            add_node(path_id, path_label, 1, "path")
            edges.append({"from": site_id, "to": path_id, "healthy": s["alive"]})

            # Check transforms (rewrites, headers, handle_response)
            tr = branch.get("transforms") or s.get("transforms") or {}
            tr_summary = []
            if tr.get("rewrites"):
                tr_summary.append("rewrite: " + ", ".join(tr["rewrites"]))
            if tr.get("headers_up"):
                tr_summary.append("header: " + ", ".join(tr["headers_up"][:1]))
            if tr.get("handle_response"):
                tr_summary.append("handle_response")
            tr_text = " · ".join(tr_summary) if tr_summary else None

            proxy_id = f"{path_id}|proxy"
            add_node(proxy_id, "Caddy proxy", 2, "proxy", transforms=tr_text)
            edges.append({"from": path_id, "to": proxy_id, "healthy": s["alive"]})
            for up in branch.get("upstreams", []):
                up_id = f"up:{up}"
                up_healthy = None
                for u in s.get("upstreams", []):
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


async def topology(request: Request):
    from .caddy_source import refresh
    await refresh()
    data = api_topology()
    nodes = data["nodes"]
    edges = data["edges"]

    col_x = {0: 40, 1: 280, 2: 520, 3: 760}
    col_cursor = {0: 20, 1: 20, 2: 20, 3: 20}
    row_h = 48
    pos = {}
    for n in nodes:
        y = col_cursor[n["col"]]
        col_cursor[n["col"]] += row_h
        pos[n["id"]] = (col_x[n["col"]], y)

    svg_height = max(col_cursor.values()) + 30
    svg = [f'<svg width="960" height="{svg_height}" class="font-sans">']

    for e in edges:
        x1, y1 = pos[e["from"]]
        x2, y2 = pos[e["to"]]
        color = "#10b981" if e.get("healthy") is True else ("#e11d48" if e.get("healthy") is False else "#88929b")
        svg.append(f'<line x1="{x1+150}" y1="{y1+18}" x2="{x2}" y2="{y2+18}" stroke="{color}" stroke-width="1.5" marker-end="url(#arrow)"/>')

    for n in nodes:
        x, y = pos[n["id"]]
        if n["kind"] == "host":
            fill, stroke = "#1e293b", "#0ea5e9"
        elif n["kind"] == "path":
            fill, stroke = "#1e293b", "#f59e0b"
        elif n["kind"] == "proxy":
            fill, stroke = "#1e293b", "#84cc16"
        else:
            h = n.get("healthy")
            if h is True:
                fill, stroke = "#131b2e", "#10b981"
            elif h is False:
                fill, stroke = "#1e293b", "#e11d48"
            else:
                fill, stroke = "#1e293b", "#88929b"

        tr_info = n.get("transforms")
        extra_svg = ""
        if tr_info:
            extra_svg = f'<title>{escape(tr_info)}</title><text x="{x+75}" y="{y+31}" fill="#f59e0b" font-size="8" font-family="JetBrains Mono, monospace" text-anchor="middle">⚙ {escape(tr_info[:18])}</text>'

        text_y = y + 17 if tr_info else y + 22
        svg.append(
            f'<g><rect x="{x}" y="{y}" width="150" height="36" rx="6" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>'
            f'<text x="{x+75}" y="{text_y}" fill="#f8fafc" font-size="11" font-weight="600" text-anchor="middle" font-family="JetBrains Mono, monospace">{escape(n["label"][:22])}</text>'
            f'{extra_svg}</g>'
        )
    svg.append('<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto"><path d="M0,0 L0,6 L8,3 z" fill="#88929b"/></marker></defs>')
    svg.append('</svg>')

    sites = _state.get("sites", [])
    total = len(sites)
    alive = sum(1 for s in sites if s.get("alive"))

    html = f"""<!doctype html>
<html class="dark" lang="en"><head>
<meta charset="utf-8"/>
<meta content="width=device-width, initial-scale=1.0" name="viewport"/>
<title>Caddy Mon - Route Topology</title>
<script src="https://cdn.tailwindcss.com?plugins=forms,container-queries"></script>
<link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&display=swap" rel="stylesheet"/>
<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet"/>
<script id="tailwind-config">
  tailwind.config = {{
    darkMode: "class",
    theme: {{
      extend: {{
        colors: {{
          background: "#0f172a",
          "surface-container": "#1e293b",
          primary: "#0ea5e9",
          "on-surface": "#f8fafc",
          "on-surface-variant": "#bec8d2",
          outline: "#88929b",
          "status-alive": "#10b981",
          "status-down": "#e11d48",
          "status-maint": "#f59e0b",
        }},
        fontFamily: {{
          sans: ["Geist", "sans-serif"],
          mono: ["JetBrains Mono", "monospace"],
        }},
        spacing: {{
          "container-max": "1440px",
          "gutter": "1.5rem",
        }}
      }}
    }}
  }}
</script>
<style>
  body {{ background-color: #0f172a; color: #f8fafc; font-family: 'Geist', sans-serif; }}
  .no-scrollbar::-webkit-scrollbar {{ display: none; }}
  .no-scrollbar {{ -ms-overflow-style: none; scrollbar-width: none; }}
</style>
</head>
<body class="bg-background text-on-surface min-h-screen flex flex-col antialiased">
  <header class="bg-background docked full-width top-0 flex flex-col gap-2 w-full pt-6 px-gutter max-w-container-max mx-auto">
    <div class="flex justify-between items-center w-full">
      <h1 class="text-2xl font-bold text-on-surface tracking-tight font-sans">Caddy Mon</h1>
      <div class="flex items-center gap-2 text-xs font-mono text-on-surface-variant">
        <span>{total} sites</span>
        <span>•</span>
        <span class="text-status-alive font-bold">{alive} alive</span>
      </div>
    </div>

    <!-- Navigation Bar -->
    <nav class="flex gap-6 mt-2 overflow-x-auto pb-1 no-scrollbar border-b border-white/5 text-xs font-bold uppercase tracking-wider font-sans">
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/">Dashboard</a>
      <a class="text-primary border-b-2 border-primary pb-2 whitespace-nowrap" href="/topology">Topology</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/analytics">Analytics</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/logs">Logs</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/security">Security</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/tls">TLS</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/caddy/config">Caddy Config</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/audit">Audit Trail</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/status">Status Page</a>
    </nav>
  </header>

  <main class="flex-1 w-full max-w-container-max mx-auto px-gutter py-8 flex flex-col gap-6">
    <div class="flex flex-wrap gap-4 text-xs font-mono text-on-surface-variant">
      <span class="flex items-center gap-1.5"><i class="w-2.5 h-2.5 rounded-xs bg-[#0ea5e9]"></i> host</span>
      <span class="flex items-center gap-1.5"><i class="w-2.5 h-2.5 rounded-xs bg-[#f59e0b]"></i> path matcher</span>
      <span class="flex items-center gap-1.5"><i class="w-2.5 h-2.5 rounded-xs bg-[#84cc16]"></i> Caddy proxy</span>
      <span class="flex items-center gap-1.5"><i class="w-2.5 h-2.5 rounded-xs bg-[#10b981]"></i> healthy upstream</span>
      <span class="flex items-center gap-1.5"><i class="w-2.5 h-2.5 rounded-xs bg-[#e11d48]"></i> unhealthy upstream</span>
    </div>

    <div class="bg-[#1e293b] border border-white/10 rounded-lg p-6 overflow-x-auto">
      {''.join(svg)}
    </div>
  </main>
  <script>setTimeout(() => location.reload(), 15000);</script>
</body></html>"""
    return HTMLResponse(html)
