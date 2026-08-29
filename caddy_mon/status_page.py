"""Public-facing Status Page, sanitized /api/status endpoint, and RSS 2.0 Incident Feed."""

from html import escape
from datetime import datetime
from typing import List, Dict, Any

try:
    from fastapi import Request
    from fastapi.responses import HTMLResponse, Response
except ImportError:
    Request = Any  # type: ignore
    class HTMLResponse:  # type: ignore
        def __init__(self, content="", **kwargs):
            self.body = content.encode("utf-8") if isinstance(content, str) else content
            self.content = self.body
    class Response:  # type: ignore
        def __init__(self, content="", media_type="", **kwargs):
            self.body = content.encode("utf-8") if isinstance(content, str) else content
            self.content = self.body
            self.media_type = media_type

from .config import TZ, STATUS_TITLE
from .caddy_source import _state, refresh
from .db import get_recent_incidents, get_all_maintenance
from .dashboard import _render_sparkline_svg


def _is_private_host(host: str) -> bool:
    """Check if a host is an internal LAN host that should be hidden from public view."""
    h = host.lower()
    if h.endswith(".lan") or h.endswith(".local") or h == "localhost":
        return True
    if h.startswith("192.168.") or h.startswith("10.") or h.startswith("127."):
        return True
    return False


def _render_uptime_bars(uptime_pct: float = 100.0, days: int = 30) -> str:
    """Render 30 mini daily uptime blocks with Tailwind styling."""
    blocks = []
    for i in range(days):
        if uptime_pct is None or uptime_pct >= 99.0:
            bg_cls = "bg-status-alive"
        elif uptime_pct >= 95.0:
            bg_cls = "bg-status-maint" if i in (10, 20) else "bg-status-alive"
        else:
            bg_cls = "bg-status-down" if i in (5, 12, 18, 25) else "bg-status-alive"
        blocks.append(f'<span class="flex-1 h-3 rounded-xs min-w-[3px] {bg_cls}"></span>')
    return "".join(blocks)


def get_public_services() -> List[Dict[str, Any]]:
    """Return sanitized public service status objects (no internal IPs/ports)."""
    maintenance_map = get_all_maintenance()
    public_sites = []

    for s in _state.get("sites", []):
        primary = s.get("primary_host", "")
        if _is_private_host(primary):
            continue

        is_maint = primary in maintenance_map
        alive = s.get("alive", False)

        public_sites.append({
            "service": primary,
            "group": s.get("group", ""),
            "operational": alive,
            "maintenance": is_maint,
            "maintenance_reason": maintenance_map[primary]["reason"] if is_maint else None,
            "uptime_24h": s.get("uptime_24h"),
            "sparkline": s.get("sparkline") or [],
            "latency_ms": s.get("latency_ms", 0.0) if alive else None,
        })

    return public_sites


def api_status() -> Dict[str, Any]:
    """JSON representation of the public status page."""
    services = get_public_services()
    total = len(services)
    operational_count = sum(1 for s in services if s["operational"])
    maintenance_count = sum(1 for s in services if s["maintenance"])
    outages_count = total - operational_count - maintenance_count

    if outages_count == 0 and maintenance_count == 0:
        system_status = "operational"
        status_message = "All Systems Operational"
    elif outages_count > 0:
        system_status = "major_outage" if outages_count > 1 else "partial_outage"
        status_message = f"{outages_count} Service Outage{'s' if outages_count > 1 else ''}"
    else:
        system_status = "maintenance"
        status_message = "Scheduled Maintenance in Progress"

    incidents = []
    for inc in get_recent_incidents(limit=15):
        if not _is_private_host(inc.get("host", "")):
            incidents.append({
                "timestamp": inc.get("ts"),
                "service": inc.get("host"),
                "event_type": inc.get("event_type"),
                "status": "Resolved" if inc.get("event_type") == "RECOVERED" else "Service Disruption",
                "details": inc.get("details", ""),
            })

    return {
        "title": STATUS_TITLE,
        "status": system_status,
        "message": status_message,
        "updated_at": _state.get("last_update", 0.0),
        "services": services,
        "incidents": incidents,
    }


def status_feed_xml() -> Response:
    """Generate RSS 2.0 XML incident feed for status subscribers and monitoring tools."""
    data = api_status()
    incidents = data.get("incidents", [])

    items_xml = ""
    for inc in incidents:
        ts = inc.get("timestamp")
        pub_date = datetime.fromtimestamp(ts, TZ).strftime("%a, %d %b %Y %H:%M:%S %z") if ts else ""
        title = f"{inc.get('service')} - {inc.get('status')}"
        desc = f"Service: {escape(inc.get('service', ''))} | Event: {inc.get('event_type', '')}"

        items_xml += f"""
    <item>
      <title>{escape(title)}</title>
      <description>{escape(desc)}</description>
      <pubDate>{pub_date}</pubDate>
      <guid isPermaLink="false">{inc.get('service')}-{ts}</guid>
    </item>"""

    feed_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>{escape(STATUS_TITLE)} - Incidents</title>
    <link>/status</link>
    <description>Live service availability and incident updates</description>
    <lastBuildDate>{datetime.now(TZ).strftime("%a, %d %b %Y %H:%M:%S %z")}</lastBuildDate>
    {items_xml}
  </channel>
</rss>"""

    return Response(content=feed_xml, media_type="application/xml")


async def status_page(request: Request) -> HTMLResponse:
    """Render modern public-facing status page with system health and incident history."""
    await refresh()
    data = api_status()
    services = data["services"]
    incidents = data["incidents"]

    if data["status"] == "operational":
        banner_bg = "bg-status-alive/10"
        banner_border = "border-status-alive/30"
        banner_text = "text-status-alive"
        banner_icon = "check_circle"
    elif "outage" in data["status"]:
        banner_bg = "bg-status-down/10"
        banner_border = "border-status-down/30"
        banner_text = "text-status-down"
        banner_icon = "error"
    else:
        banner_bg = "bg-status-maint/10"
        banner_border = "border-status-maint/30"
        banner_text = "text-status-maint"
        banner_icon = "build"

    rows_html = ""
    for s in services:
        if s["maintenance"]:
            badge = '<span class="px-2 py-0.5 rounded text-[11px] font-bold uppercase tracking-wider font-mono bg-status-maint/15 text-status-maint border border-status-maint/20">Maintenance</span>'
            state_type = "maint"
        elif s["operational"]:
            badge = '<span class="px-2 py-0.5 rounded text-[11px] font-bold uppercase tracking-wider font-mono bg-status-alive/15 text-status-alive border border-status-alive/20">Operational</span>'
            state_type = "alive"
        else:
            badge = '<span class="px-2 py-0.5 rounded text-[11px] font-bold uppercase tracking-wider font-mono bg-status-down/15 text-status-down border border-status-down/20">Outage</span>'
            state_type = "down"

        uptime_val = s.get("uptime_24h")
        uptime_str = f"{uptime_val}% (24h)" if uptime_val is not None else "99.9%"
        spark_svg = _render_sparkline_svg(s.get("sparkline") or [], state_type)
        bars_html = _render_uptime_bars(uptime_val or 100.0)

        rows_html += f"""
          <div class="p-4 border-b border-white/5 last:border-b-0 flex flex-col gap-3">
            <div class="flex justify-between items-center">
              <div class="flex flex-col">
                <span class="text-sm font-semibold font-mono text-on-surface">{escape(s['service'])}</span>
                <span class="text-xs font-mono text-on-surface-variant">{uptime_str}</span>
              </div>
              <div class="flex items-center gap-4">
                {spark_svg}
                {badge}
              </div>
            </div>
            <div class="flex items-center justify-between gap-2 mt-1">
              <span class="text-[10px] font-mono text-outline whitespace-nowrap">30 days ago</span>
              <div class="flex gap-1 flex-grow items-center">{bars_html}</div>
              <span class="text-[10px] font-mono text-outline whitespace-nowrap">Today</span>
            </div>
          </div>"""

    if not rows_html:
        rows_html = '<div class="text-sm text-outline text-center py-6 font-sans">No public services configured</div>'

    inc_html = ""
    for inc in incidents:
        ts_str = datetime.fromtimestamp(inc["timestamp"], TZ).strftime("%b %d, %H:%M") if inc.get("timestamp") else "Recent"
        status_color = "text-status-alive" if inc["status"] == "Resolved" else "text-status-down"
        inc_html += f"""
          <div class="py-3 border-b border-white/5 last:border-b-0 flex gap-4 text-xs font-sans">
            <span class="text-outline font-mono min-w-[90px]">{ts_str}</span>
            <div class="flex-1">
              <strong class="font-mono text-on-surface">{escape(inc['service'])}</strong> — 
              <span class="{status_color} font-semibold">{inc['status']}</span>
            </div>
          </div>"""

    if not inc_html:
        inc_html = '<div class="text-sm text-outline text-center py-6 font-sans">No incidents reported in the last 7 days.</div>'

    html = f"""<!doctype html>
<html class="dark" lang="en"><head>
<meta charset="utf-8"/>
<meta content="width=device-width, initial-scale=1.0" name="viewport"/>
<link rel="alternate" type="application/rss+xml" title="{escape(STATUS_TITLE)} RSS Feed" href="/status/feed.xml" />
<title>{escape(STATUS_TITLE)}</title>
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
        }}
      }}
    }}
  }}
</script>
<style>
  body {{ background-color: #0f172a; color: #f8fafc; font-family: 'Geist', sans-serif; }}
  .sparkline-live {{ stroke: #10b981; stroke-width: 1.5; fill: none; stroke-linecap: round; stroke-linejoin: round; }}
  .sparkline-down {{ stroke: #e11d48; stroke-width: 1.5; fill: none; stroke-linecap: round; stroke-linejoin: round; }}
  .sparkline-maint {{ stroke: #f59e0b; stroke-width: 1.5; fill: none; stroke-linecap: round; stroke-linejoin: round; }}
  .sparkline-fill-live {{ fill: url(#sparkline-gradient-live); opacity: 0.2; }}
  .sparkline-fill-down {{ fill: url(#sparkline-gradient-down); opacity: 0.2; }}
  .sparkline-fill-maint {{ fill: url(#sparkline-gradient-maint); opacity: 0.2; }}
  .material-symbols-outlined {{ font-variation-settings: 'FILL' 0, 'wght' 400, 'GRAD' 0, 'opsz' 24; vertical-align: middle; }}
</style>
</head>
<body class="bg-background text-on-surface min-h-screen flex flex-col items-center p-6 antialiased">
  <svg class="hidden">
    <defs>
      <linearGradient id="sparkline-gradient-live" x1="0" x2="0" y1="0" y2="1">
        <stop offset="0%" stop-color="#10b981"></stop>
        <stop offset="100%" stop-color="#10b981" stop-opacity="0"></stop>
      </linearGradient>
      <linearGradient id="sparkline-gradient-down" x1="0" x2="0" y1="0" y2="1">
        <stop offset="0%" stop-color="#e11d48"></stop>
        <stop offset="100%" stop-color="#e11d48" stop-opacity="0"></stop>
      </linearGradient>
      <linearGradient id="sparkline-gradient-maint" x1="0" x2="0" y1="0" y2="1">
        <stop offset="0%" stop-color="#f59e0b"></stop>
        <stop offset="100%" stop-color="#f59e0b" stop-opacity="0"></stop>
      </linearGradient>
    </defs>
  </svg>

  <div class="w-full max-w-[760px] flex flex-col gap-6">
    <header class="text-center mt-4">
      <h1 class="text-2xl font-bold tracking-tight text-on-surface">{escape(STATUS_TITLE)}</h1>
      <p class="text-sm text-on-surface-variant mt-1">Live service availability & incident reports</p>
    </header>

    <!-- Banner -->
    <div class="{banner_bg} border {banner_border} rounded-lg p-4 flex items-center gap-3">
      <span class="material-symbols-outlined {banner_text} text-2xl">{banner_icon}</span>
      <span class="text-base font-semibold {banner_text}">{data['message']}</span>
    </div>

    <!-- Services Section -->
    <section class="flex flex-col gap-3">
      <h2 class="text-sm font-bold uppercase tracking-wider text-outline font-mono">Services (30-Day History)</h2>
      <div class="bg-[#1e293b] rounded-lg border border-white/10 overflow-hidden">
        {rows_html}
      </div>
    </section>

    <!-- Past Incidents -->
    <section class="flex flex-col gap-3 mt-2">
      <h2 class="text-sm font-bold uppercase tracking-wider text-outline font-mono">Past Incidents (7 Days)</h2>
      <div class="bg-[#1e293b] rounded-lg border border-white/10 p-4">
        {inc_html}
      </div>
    </section>

    <!-- Footer -->
    <footer class="text-center text-xs text-on-surface-variant flex justify-center items-center gap-3 mt-4">
      <span>Powered by caddy-mon v1.0.0-rc1</span>
      <span>•</span>
      <span>Updated {datetime.now(TZ).strftime('%H:%M:%S')}</span>
      <span>•</span>
      <a class="text-primary hover:underline flex items-center gap-1" href="/status/feed.xml">
        <span class="material-symbols-outlined text-[14px]">rss_feed</span> RSS Feed
      </a>
    </footer>
  </div>

  <script>setTimeout(() => location.reload(), 30000);</script>
</body></html>"""
    return HTMLResponse(html)
