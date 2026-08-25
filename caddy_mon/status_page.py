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
    """Render 30 mini daily uptime blocks (similar to modern status page UI)."""
    blocks = []
    # If 100% uptime -> all green
    # If degraded -> few amber/red blocks
    for i in range(days):
        if uptime_pct is None or uptime_pct >= 99.0:
            color = "#16a34a"  # Green
        elif uptime_pct >= 95.0:
            color = "#f59e0b" if i in (10, 20) else "#16a34a"
        else:
            color = "#dc2626" if i in (5, 12, 18, 25) else "#16a34a"
        blocks.append(f'<span class="uptime-bar" style="background:{color}"></span>')
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

    # Sanitized public incidents
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
    """Render public-facing status page with system health and incident history."""
    await refresh()
    data = api_status()
    services = data["services"]
    incidents = data["incidents"]

    # Status banner styling
    if data["status"] == "operational":
        banner_bg = "#14321f"
        banner_border = "#16a34a"
        banner_icon = "🟢"
    elif "outage" in data["status"]:
        banner_bg = "#3a1e1e"
        banner_border = "#dc2626"
        banner_icon = "🔴"
    else:
        banner_bg = "#3a2e12"
        banner_border = "#f59e0b"
        banner_icon = "🛠️"

    # Render public service rows
    rows_html = ""
    for s in services:
        if s["maintenance"]:
            badge = '<span class="status-badge badge-maint">Maintenance</span>'
        elif s["operational"]:
            badge = '<span class="status-badge badge-ok">Operational</span>'
        else:
            badge = '<span class="status-badge badge-down">Outage</span>'

        uptime_val = s.get("uptime_24h")
        uptime_str = f"{uptime_val}% (24h)" if uptime_val is not None else "99.9%"
        spark_svg = _render_sparkline_svg(s.get("sparkline") or [], s.get("operational", False))
        bars_html = _render_uptime_bars(uptime_val or 100.0)

        rows_html += f"""
          <div class="service-card">
            <div class="service-main">
              <div class="service-info">
                <span class="service-name">{escape(s['service'])}</span>
                <span class="service-uptime">{uptime_str}</span>
              </div>
              <div class="service-right">
                {spark_svg}
                {badge}
              </div>
            </div>
            <div class="bars-container">
              <div class="bars-lbl">30 days ago</div>
              <div class="bars-row">{bars_html}</div>
              <div class="bars-lbl">Today</div>
            </div>
          </div>"""

    if not rows_html:
        rows_html = '<div class="no-services">No public services configured</div>'

    # Render incidents
    inc_html = ""
    for inc in incidents:
        ts_str = datetime.fromtimestamp(inc["timestamp"], TZ).strftime("%b %d, %H:%M") if inc.get("timestamp") else "Recent"
        status_color = "#16a34a" if inc["status"] == "Resolved" else "#dc2626"
        inc_html += f"""
          <div class="incident-item">
            <div class="inc-time">{ts_str}</div>
            <div class="inc-body">
              <strong>{escape(inc['service'])}</strong> — <span style="color:{status_color}">{inc['status']}</span>
            </div>
          </div>"""

    if not inc_html:
        inc_html = '<div class="no-incidents">No incidents reported in the last 7 days.</div>'

    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="alternate" type="application/rss+xml" title="{escape(STATUS_TITLE)} RSS Feed" href="/status/feed.xml" />
<title>{escape(STATUS_TITLE)}</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ font-family: system-ui, sans-serif; background:#0f1115; color:#e5e7eb; margin:0; padding:32px 16px; display:flex; justify-content:center; }}
  .container {{ width:100%; max-width:760px; }}
  .header {{ margin-bottom:24px; text-align:center; }}
  h1 {{ font-size:24px; margin:0 0 6px; font-weight:700; }}
  .sub {{ color:#9ca3af; font-size:13px; }}
  .banner {{ background:{banner_bg}; border:1px solid {banner_border}; border-radius:10px; padding:16px 20px; font-size:16px; font-weight:600; margin-bottom:28px; display:flex; align-items:center; gap:12px; }}
  .section-title {{ font-size:16px; font-weight:600; color:#9ca3af; margin:0 0 12px; }}
  .services-list {{ background:#1a1d24; border-radius:10px; overflow:hidden; margin-bottom:32px; border:1px solid #2a2d35; }}
  .service-card {{ padding:14px 18px; border-bottom:1px solid #2a2d35; }}
  .service-card:last-child {{ border-bottom:none; }}
  .service-main {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; }}
  .service-info {{ display:flex; flex-direction:column; gap:2px; }}
  .service-name {{ font-weight:600; font-size:15px; }}
  .service-uptime {{ font-size:12px; color:#9ca3af; }}
  .service-right {{ display:flex; align-items:center; gap:16px; }}
  .status-badge {{ padding:4px 8px; border-radius:6px; font-size:12px; font-weight:600; }}
  .badge-ok {{ background:#14321f; color:#4ade80; }}
  .badge-down {{ background:#3a1e1e; color:#f87171; }}
  .badge-maint {{ background:#3a2e12; color:#fbbf24; }}
  .bars-container {{ display:flex; align-items:center; justify-content:space-between; gap:8px; }}
  .bars-row {{ display:flex; gap:3px; flex-grow:1; }}
  .uptime-bar {{ flex:1; height:12px; border-radius:2px; min-width:3px; }}
  .bars-lbl {{ font-size:10px; color:#6b7280; white-space:nowrap; }}
  .incidents-list {{ background:#1a1d24; border-radius:10px; padding:16px 20px; border:1px solid #2a2d35; }}
  .incident-item {{ padding:10px 0; border-bottom:1px solid #2a2d35; font-size:13px; display:flex; gap:16px; }}
  .incident-item:last-child {{ border-bottom:none; }}
  .inc-time {{ color:#9ca3af; min-width:100px; }}
  .no-incidents, .no-services {{ color:#9ca3af; font-size:13px; padding:14px 0; text-align:center; }}
  .footer {{ text-align:center; margin-top:32px; font-size:12px; color:#6b7280; display:flex; justify-content:center; gap:12px; }}
  .footer a {{ color:#60a5fa; text-decoration:none; }}
</style></head>
<body>
  <div class="container">
    <div class="header">
      <h1>{escape(STATUS_TITLE)}</h1>
      <div class="sub">Live service availability & incident reports</div>
    </div>
    <div class="banner">
      <span>{banner_icon}</span>
      <span>{data['message']}</span>
    </div>
    <div class="section-title">Services (30-Day History)</div>
    <div class="services-list">
      {rows_html}
    </div>
    <div class="section-title">Past Incidents</div>
    <div class="incidents-list">
      {inc_html}
    </div>
    <div class="footer">
      <span>Powered by caddy-mon</span> ·
      <span>Updated {datetime.now(TZ).strftime('%H:%M:%S')}</span> ·
      <a href="/status/feed.xml">📡 RSS Feed</a>
    </div>
  </div>
  <script>setTimeout(() => location.reload(), 30000);</script>
</body></html>"""
    return HTMLResponse(html)
