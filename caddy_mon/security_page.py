"""Security & Client Traffic Analytics module with modern Tailwind CSS design."""

import time
from html import escape
from datetime import datetime
from typing import Dict, Any, List

try:
    from fastapi import Request
    from fastapi.responses import HTMLResponse
except ImportError:
    Request = Any  # type: ignore
    HTMLResponse = Any  # type: ignore

from .config import TZ
from .log_source import ingest_logs, _LOG_CACHE


def is_lan_ip(ip: str) -> bool:
    """Check if an IP address belongs to RFC 1918 private / loopback ranges."""
    ip = ip.strip().lower()
    if ip in ("127.0.0.1", "::1", "localhost"):
        return True
    if ip.startswith("192.168.") or ip.startswith("10."):
        return True
    if ip.startswith("172."):
        parts = ip.split(".")
        if len(parts) >= 2:
            try:
                second = int(parts[1])
                if 16 <= second <= 31:
                    return True
            except ValueError:
                pass
    return False


def security_analytics(window: int = 3600) -> Dict[str, Any]:
    """Aggregate traffic, status codes, top client IPs, and suspicious requests."""
    ingest_logs()
    now = time.time()
    cutoff = now - window

    status_dist = {"2xx": 0, "3xx": 0, "4xx": 0, "429": 0, "5xx": 0}
    clients: Dict[str, Dict[str, Any]] = {}
    suspicious: List[Dict[str, Any]] = []

    for e in _LOG_CACHE:
        ts = e.get("ts")
        if ts is None or ts < cutoff:
            continue

        st = e.get("status")
        ip = e.get("client_ip") or "unknown"
        host = e.get("host") or ""
        uri = e.get("uri") or "/"
        method = e.get("method") or "GET"

        # Status code bucket
        if isinstance(st, int):
            if 200 <= st < 300:
                status_dist["2xx"] += 1
            elif 300 <= st < 400:
                status_dist["3xx"] += 1
            elif st == 429:
                status_dist["429"] += 1
                status_dist["4xx"] += 1
            elif 400 <= st < 500:
                status_dist["4xx"] += 1
            elif st >= 500:
                status_dist["5xx"] += 1

        # Client aggregation
        c = clients.setdefault(ip, {
            "ip": ip,
            "requests": 0,
            "errors_4xx": 0,
            "errors_5xx": 0,
            "hosts": {},
            "is_lan": is_lan_ip(ip),
        })
        c["requests"] += 1
        if isinstance(st, int):
            if 400 <= st < 500:
                c["errors_4xx"] += 1
            elif st >= 500:
                c["errors_5xx"] += 1
        if host:
            c["hosts"][host] = c["hosts"].get(host, 0) + 1

        # Suspicious request tracking (401, 403, 404, 429)
        if isinstance(st, int) and (st in (401, 403, 404, 429)):
            if len(suspicious) < 50:
                suspicious.append({
                    "ts": ts,
                    "ip": ip,
                    "host": host,
                    "uri": uri,
                    "method": method,
                    "status": st,
                })

    # Format top clients
    top_clients = []
    for ip, data in clients.items():
        top_host = max(data["hosts"].items(), key=lambda x: x[1])[0] if data["hosts"] else ""
        top_clients.append({
            "ip": ip,
            "requests": data["requests"],
            "errors_4xx": data["errors_4xx"],
            "errors_5xx": data["errors_5xx"],
            "top_host": top_host,
            "is_lan": data["is_lan"],
        })
    top_clients.sort(key=lambda x: x["requests"], reverse=True)

    return {
        "window_seconds": window,
        "total_requests": sum(c["requests"] for c in clients.values()),
        "status_distribution": status_dist,
        "top_clients": top_clients[:30],
        "suspicious_requests": suspicious[:50],
    }


def security_page(request: Request, window: int = 3600) -> HTMLResponse:
    """Render HTML page for Security & Client Traffic Analytics with Tailwind CSS."""
    data = security_analytics(window=window)
    dist = data["status_distribution"]
    top_clients = data["top_clients"]
    suspicious = data["suspicious_requests"]

    # Render top client rows
    client_rows = ""
    for c in top_clients:
        badge = (
            '<span class="bg-primary/15 text-primary text-[10px] font-bold px-1.5 py-0.5 rounded font-mono">LAN</span>'
            if c["is_lan"]
            else '<span class="bg-status-down/15 text-status-down text-[10px] font-bold px-1.5 py-0.5 rounded font-mono">WAN</span>'
        )
        err_style = "text-status-down font-bold" if (c["errors_4xx"] > 0 or c["errors_5xx"] > 0) else "text-outline"
        client_rows += f"""
          <tr class="border-b border-white/5 hover:bg-white/[0.02] transition-colors">
            <td class="p-3 font-mono text-xs"><span class="text-on-surface">{escape(c['ip'])}</span> {badge}</td>
            <td class="p-3 font-mono text-xs font-semibold text-on-surface">{c['requests']}</td>
            <td class="p-3 font-mono text-xs {err_style}">{c['errors_4xx']} / {c['errors_5xx']}</td>
            <td class="p-3 font-mono text-xs text-on-surface-variant">{escape(c['top_host'])}</td>
          </tr>"""

    if not client_rows:
        client_rows = '<tr><td colspan="4" class="p-4 text-center text-outline text-xs font-mono">No traffic recorded in this window</td></tr>'

    # Render suspicious request rows
    susp_rows = ""
    for s in suspicious:
        ts_str = datetime.fromtimestamp(s["ts"], TZ).strftime("%H:%M:%S") if s.get("ts") else ""
        badge_color = "text-status-maint" if s["status"] in (401, 403, 429) else "text-outline"
        susp_rows += f"""
          <tr class="border-b border-white/5 hover:bg-white/[0.02] transition-colors">
            <td class="p-3 font-mono text-xs text-outline">{ts_str}</td>
            <td class="p-3 font-mono text-xs font-bold {badge_color}">{s['status']}</td>
            <td class="p-3 font-mono text-xs text-on-surface">{escape(s['ip'])}</td>
            <td class="p-3 font-mono text-xs text-on-surface-variant">{escape(s['host'])}</td>
            <td class="p-3 font-mono text-xs text-on-surface-variant"><span class="text-primary font-bold">{escape(s['method'])}</span> {escape(s['uri'])}</td>
          </tr>"""

    if not susp_rows:
        susp_rows = '<tr><td colspan="5" class="p-4 text-center text-outline text-xs font-mono">No 4xx/429 client errors recorded</td></tr>'

    html = f"""<!doctype html>
<html class="dark" lang="en"><head>
<meta charset="utf-8"/>
<meta content="width=device-width, initial-scale=1.0" name="viewport"/>
<title>Caddy Mon - Security & Traffic</title>
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
  .material-symbols-outlined {{ font-variation-settings: 'FILL' 0, 'wght' 400, 'GRAD' 0, 'opsz' 24; vertical-align: middle; }}
  .no-scrollbar::-webkit-scrollbar {{ display: none; }}
  .no-scrollbar {{ -ms-overflow-style: none; scrollbar-width: none; }}
</style>
</head>
<body class="bg-background text-on-surface min-h-screen flex flex-col antialiased">
  <header class="bg-background docked full-width top-0 flex flex-col gap-2 w-full pt-6 px-gutter max-w-container-max mx-auto">
    <div class="flex justify-between items-center w-full">
      <h1 class="text-2xl font-bold text-on-surface tracking-tight font-sans">Caddy Mon</h1>
      <div class="flex items-center gap-2 text-xs font-mono text-on-surface-variant">
        <span>Window: <span class="text-primary font-bold">{window // 60}m</span></span>
        <span>•</span>
        <span>Total reqs: <span class="text-on-surface font-bold">{data['total_requests']}</span></span>
      </div>
    </div>

    <!-- Navigation Bar -->
    <nav class="flex gap-6 mt-2 overflow-x-auto pb-1 no-scrollbar border-b border-white/5 text-xs font-bold uppercase tracking-wider font-sans">
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/">Dashboard</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/topology">Topology</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/logs">Logs</a>
      <a class="text-primary border-b-2 border-primary pb-2 whitespace-nowrap" href="/security">Security</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/tls">TLS</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/caddy/config">Caddy Config</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/status">Status Page</a>
    </nav>
  </header>

  <main class="flex-1 w-full max-w-container-max mx-auto px-gutter py-8 flex flex-col gap-8">
    <!-- Metrics Grid -->
    <div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-5 gap-4">
      <div class="bg-[#1e293b] border border-white/10 rounded-lg p-4 text-center flex flex-col gap-1">
        <span class="text-[11px] font-mono uppercase tracking-wider text-outline">2xx Success</span>
        <span class="text-2xl font-bold font-mono text-status-alive">{dist['2xx']}</span>
      </div>
      <div class="bg-[#1e293b] border border-white/10 rounded-lg p-4 text-center flex flex-col gap-1">
        <span class="text-[11px] font-mono uppercase tracking-wider text-outline">3xx Redirect</span>
        <span class="text-2xl font-bold font-mono text-primary">{dist['3xx']}</span>
      </div>
      <div class="bg-[#1e293b] border border-white/10 rounded-lg p-4 text-center flex flex-col gap-1">
        <span class="text-[11px] font-mono uppercase tracking-wider text-outline">4xx Client Err</span>
        <span class="text-2xl font-bold font-mono text-status-maint">{dist['4xx']}</span>
      </div>
      <div class="bg-[#1e293b] border border-white/10 rounded-lg p-4 text-center flex flex-col gap-1">
        <span class="text-[11px] font-mono uppercase tracking-wider text-outline">429 Rate Limits</span>
        <span class="text-2xl font-bold font-mono text-pink-400">{dist['429']}</span>
      </div>
      <div class="bg-[#1e293b] border border-white/10 rounded-lg p-4 text-center flex flex-col gap-1">
        <span class="text-[11px] font-mono uppercase tracking-wider text-outline">5xx Server Err</span>
        <span class="text-2xl font-bold font-mono text-status-down">{dist['5xx']}</span>
      </div>
    </div>

    <!-- Top Clients Table -->
    <section class="flex flex-col gap-3">
      <h2 class="text-sm font-bold uppercase tracking-wider text-outline font-mono">Top Client IPs ({window // 60}m)</h2>
      <div class="bg-[#1e293b] border border-white/10 rounded-lg overflow-hidden">
        <table class="w-full text-left border-collapse">
          <thead>
            <tr class="bg-[#131b2e] text-[11px] font-mono uppercase tracking-wider text-outline border-b border-white/5">
              <th class="p-3">Client IP</th>
              <th class="p-3">Requests</th>
              <th class="p-3">4xx / 5xx</th>
              <th class="p-3">Primary Target</th>
            </tr>
          </thead>
          <tbody>
            {client_rows}
          </tbody>
        </table>
      </div>
    </section>

    <!-- Recent 4xx / Rate-Limit Events -->
    <section class="flex flex-col gap-3">
      <h2 class="text-sm font-bold uppercase tracking-wider text-outline font-mono">Recent 4xx / Rate-Limit Events</h2>
      <div class="bg-[#1e293b] border border-white/10 rounded-lg overflow-hidden">
        <table class="w-full text-left border-collapse">
          <thead>
            <tr class="bg-[#131b2e] text-[11px] font-mono uppercase tracking-wider text-outline border-b border-white/5">
              <th class="p-3">Time</th>
              <th class="p-3">Status</th>
              <th class="p-3">Client IP</th>
              <th class="p-3">Host</th>
              <th class="p-3">Request</th>
            </tr>
          </thead>
          <tbody>
            {susp_rows}
          </tbody>
        </table>
      </div>
    </section>
  </main>
</body></html>"""
    return HTMLResponse(html)
