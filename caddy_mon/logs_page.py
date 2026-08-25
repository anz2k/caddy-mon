"""Log analytics page and /api/logs with modern Tailwind CSS design."""

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
from .log_source import log_stats


def api_logs(window: int = 3600):
    return log_stats(window=window)


def logs_page(request: Request, window: int = 3600):
    data = log_stats(window=window)
    rows = data["rows"]
    table_rows = ""
    for r in rows:
        err_style = "text-status-down font-bold" if r["error_pct"] > 0 else "text-outline"
        table_rows += f"""
          <tr class="border-b border-white/5 hover:bg-white/[0.02] transition-colors">
            <td class="p-3 font-mono text-xs font-semibold text-on-surface">{escape(r['host'])}</td>
            <td class="p-3 font-mono text-xs text-on-surface">{r['requests']}</td>
            <td class="p-3 font-mono text-xs {err_style}">{r['errors_5xx']}</td>
            <td class="p-3 font-mono text-xs {err_style}">{r['error_pct']}%</td>
            <td class="p-3 font-mono text-xs text-on-surface-variant">{r['avg_ms']}ms</td>
          </tr>"""

    if not table_rows:
        table_rows = '<tr><td colspan="5" class="p-4 text-center text-outline text-xs font-mono">No traffic recorded in this window</td></tr>'

    recent = data["recent_5xx"]
    recent_html = ""
    for e in recent[:20]:
        ts = datetime.fromtimestamp(e["ts"], TZ).strftime("%H:%M:%S") if e["ts"] else "?"
        uri = escape((e["uri"] or "")[:60])
        host = escape(e["host"] or "")
        status = e["status"] if isinstance(e["status"], int) else "?"
        recent_html += f"""
          <tr class="border-b border-white/5 hover:bg-white/[0.02] transition-colors">
            <td class="p-3 font-mono text-xs text-outline">{ts}</td>
            <td class="p-3 font-mono text-xs font-semibold text-on-surface">{host}</td>
            <td class="p-3 font-mono text-xs font-bold text-status-down">{status}</td>
            <td class="p-3 font-mono text-xs text-on-surface-variant">{uri}</td>
          </tr>"""

    if not recent_html:
        recent_html = '<tr><td colspan="4" class="p-4 text-center text-outline text-xs font-mono">No 5xx errors in window</td></tr>'

    html = f"""<!doctype html>
<html class="dark" lang="en"><head>
<meta charset="utf-8"/>
<meta content="width=device-width, initial-scale=1.0" name="viewport"/>
<title>Caddy Mon - Log Analytics</title>
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
      <div class="text-xs font-mono text-on-surface-variant">Window: <span class="text-primary font-bold">{window // 60}m</span></div>
    </div>

    <!-- Navigation Bar -->
    <nav class="flex gap-6 mt-2 overflow-x-auto pb-1 no-scrollbar border-b border-white/5 text-xs font-bold uppercase tracking-wider font-sans">
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/">Dashboard</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/topology">Topology</a>
      <a class="text-primary border-b-2 border-primary pb-2 whitespace-nowrap" href="/logs">Logs</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/security">Security</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/tls">TLS</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/caddy/config">Caddy Config</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/status">Status Page</a>
    </nav>
  </header>

  <main class="flex-1 w-full max-w-container-max mx-auto px-gutter py-8 flex flex-col gap-8">
    <section class="flex flex-col gap-3">
      <h2 class="text-sm font-bold uppercase tracking-wider text-outline font-mono">Per-Host Request Summary ({window // 60}m)</h2>
      <div class="bg-[#1e293b] border border-white/10 rounded-lg overflow-hidden">
        <table class="w-full text-left border-collapse">
          <thead>
            <tr class="bg-[#131b2e] text-[11px] font-mono uppercase tracking-wider text-outline border-b border-white/5">
              <th class="p-3">Host</th>
              <th class="p-3">Requests</th>
              <th class="p-3">5xx Errors</th>
              <th class="p-3">Error %</th>
              <th class="p-3">Avg Latency</th>
            </tr>
          </thead>
          <tbody>
            {table_rows}
          </tbody>
        </table>
      </div>
    </section>

    <section class="flex flex-col gap-3">
      <h2 class="text-sm font-bold uppercase tracking-wider text-outline font-mono">Recent 5xx Errors</h2>
      <div class="bg-[#1e293b] border border-white/10 rounded-lg overflow-hidden">
        <table class="w-full text-left border-collapse">
          <thead>
            <tr class="bg-[#131b2e] text-[11px] font-mono uppercase tracking-wider text-outline border-b border-white/5">
              <th class="p-3">Time</th>
              <th class="p-3">Host</th>
              <th class="p-3">Status</th>
              <th class="p-3">URI</th>
            </tr>
          </thead>
          <tbody>
            {recent_html}
          </tbody>
        </table>
      </div>
    </section>
  </main>
  <script>setTimeout(() => location.reload(), 30000);</script>
</body></html>"""
    return HTMLResponse(html)
