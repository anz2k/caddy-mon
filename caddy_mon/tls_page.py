"""TLS certificate expiry page and /api/tls with modern Tailwind CSS design."""

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
from .tls_source import cert_status


def api_tls():
    return {"entries": cert_status(), "warn_days": 30}


def tls_page(request: Request):
    entries = cert_status()
    rows = ""
    for e in entries:
        color_cls = "text-status-down font-bold" if e["warn"] else "text-status-alive font-semibold"
        hosts = ", ".join(e["hosts"]) if e["hosts"] else "(unknown)"
        not_after = datetime.fromisoformat(e["not_after"]).strftime("%Y-%m-%d") if e["not_after"] else "?"
        badge = (
            '<span class="bg-status-down/15 text-status-down text-[10px] font-bold px-1.5 py-0.5 rounded font-mono">Expiring Soon</span>'
            if e["warn"]
            else '<span class="bg-status-alive/15 text-status-alive text-[10px] font-bold px-1.5 py-0.5 rounded font-mono">Valid</span>'
        )
        rows += f"""
          <tr class="border-b border-white/5 hover:bg-white/[0.02] transition-colors">
            <td class="p-3 font-mono text-xs font-semibold text-on-surface">{escape(hosts)}</td>
            <td class="p-3 font-mono text-xs {color_cls}">{e['days_left']}d</td>
            <td class="p-3 font-mono text-xs text-on-surface-variant">{not_after}</td>
            <td class="p-3 font-mono text-xs">{badge}</td>
          </tr>"""

    if not rows:
        rows = '<tr><td colspan="4" class="p-4 text-center text-outline text-xs font-mono">No mounted certificates found in /caddy-certs</td></tr>'

    html = f"""<!doctype html>
<html class="dark" lang="en"><head>
<meta charset="utf-8"/>
<meta content="width=device-width, initial-scale=1.0" name="viewport"/>
<title>Caddy Mon - TLS Expiry</title>
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
      <div class="text-xs font-mono text-outline">Mounted at: <code>/caddy-certs</code></div>
    </div>

    <!-- Navigation Bar -->
    <nav class="flex gap-6 mt-2 overflow-x-auto pb-1 no-scrollbar border-b border-white/5 text-xs font-bold uppercase tracking-wider font-sans">
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/">Dashboard</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/topology">Topology</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/logs">Logs</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/security">Security</a>
      <a class="text-primary border-b-2 border-primary pb-2 whitespace-nowrap" href="/tls">TLS</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/caddy/config">Caddy Config</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/status">Status Page</a>
    </nav>
  </header>

  <main class="flex-1 w-full max-w-container-max mx-auto px-gutter py-8 flex flex-col gap-8">
    <section class="flex flex-col gap-3">
      <h2 class="text-sm font-bold uppercase tracking-wider text-outline font-mono">TLS Certificate Expiry</h2>
      <div class="bg-[#1e293b] border border-white/10 rounded-lg overflow-hidden">
        <table class="w-full text-left border-collapse">
          <thead>
            <tr class="bg-[#131b2e] text-[11px] font-mono uppercase tracking-wider text-outline border-b border-white/5">
              <th class="p-3">Matched Hostnames (SAN)</th>
              <th class="p-3">Days Left</th>
              <th class="p-3">Expiration Date</th>
              <th class="p-3">Status</th>
            </tr>
          </thead>
          <tbody>
            {rows}
          </tbody>
        </table>
      </div>
    </section>
  </main>
</body></html>"""
    return HTMLResponse(html)
