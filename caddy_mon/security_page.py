"""Security & Client Traffic Analytics module."""

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
    """Classify if an IP belongs to private LAN/loopback ranges."""
    if not ip:
        return False
    if ip.startswith("192.168.") or ip.startswith("10.") or ip.startswith("127."):
        return True
    if ip == "::1" or ip.startswith("fe80:"):
        return True
    if ip.startswith("172."):
        parts = ip.split(".")
        if len(parts) >= 2 and parts[1].isdigit():
            second = int(parts[1])
            if 16 <= second <= 31:
                return True
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

        # Suspicious request tracking (403, 404, 429)
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
    """Render HTML page for Security & Client Traffic Analytics."""
    data = security_analytics(window=window)
    dist = data["status_distribution"]
    top_clients = data["top_clients"]
    suspicious = data["suspicious_requests"]

    # Render top client rows
    client_rows = ""
    for c in top_clients:
        badge = '<span class="badge-lan">LAN</span>' if c["is_lan"] else '<span class="badge-wan">WAN</span>'
        err_style = 'color:#f87171' if (c["errors_4xx"] > 0 or c["errors_5xx"] > 0) else 'color:#9ca3af'
        client_rows += f"""
          <tr>
            <td><code>{escape(c['ip'])}</code> {badge}</td>
            <td><strong>{c['requests']}</strong></td>
            <td style="{err_style}">{c['errors_4xx']} / {c['errors_5xx']}</td>
            <td><code>{escape(c['top_host'])}</code></td>
          </tr>"""

    if not client_rows:
        client_rows = '<tr><td colspan="4" style="text-align:center;color:#9ca3af">No traffic recorded in this window</td></tr>'

    # Render suspicious request rows
    susp_rows = ""
    for s in suspicious:
        ts_str = datetime.fromtimestamp(s["ts"], TZ).strftime("%H:%M:%S") if s.get("ts") else ""
        badge_color = "#f59e0b" if s["status"] in (401, 403, 429) else "#9ca3af"
        susp_rows += f"""
          <tr>
            <td style="color:#9ca3af">{ts_str}</td>
            <td><span style="color:{badge_color};font-weight:700">{s['status']}</span></td>
            <td><code>{escape(s['ip'])}</code></td>
            <td><code>{escape(s['host'])}</code></td>
            <td><span style="color:#60a5fa">{escape(s['method'])}</span> <code>{escape(s['uri'])}</code></td>
          </tr>"""

    if not susp_rows:
        susp_rows = '<tr><td colspan="5" style="text-align:center;color:#9ca3af">No 4xx/429 client errors recorded</td></tr>'

    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Security & Traffic Analytics</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ font-family: system-ui, sans-serif; background:#0f1115; color:#e5e7eb; margin:0; padding:24px; }}
  h1 {{ font-size:20px; margin:0 0 4px; }}
  .sub {{ color:#9ca3af; font-size:13px; margin-bottom:20px; }}
  a {{ color:#60a5fa; text-decoration:none; }}
  a:hover {{ text-decoration:underline; }}
  .metrics-grid {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(130px, 1fr)); gap:12px; margin-bottom:24px; }}
  .metric-card {{ background:#1a1d24; border:1px solid #2a2d35; border-radius:8px; padding:12px; text-align:center; }}
  .metric-val {{ font-size:20px; font-weight:700; margin-top:4px; }}
  .metric-lbl {{ font-size:11px; color:#9ca3af; text-transform:uppercase; font-weight:600; }}
  .section {{ margin-bottom:28px; }}
  h2 {{ font-size:16px; margin:0 0 12px; font-weight:600; color:#e5e7eb; }}
  table {{ width:100%; border-collapse:collapse; background:#1a1d24; border-radius:8px; overflow:hidden; border:1px solid #2a2d35; font-size:13px; }}
  th, td {{ padding:10px 14px; text-align:left; border-bottom:1px solid #2a2d35; }}
  th {{ background:#13161c; color:#9ca3af; font-size:11px; text-transform:uppercase; }}
  tr:last-child td {{ border-bottom:none; }}
  code {{ background:#0f1115; padding:2px 6px; border-radius:4px; font-size:12px; }}
  .badge-lan {{ background:#1e293b; color:#38bdf8; font-size:10px; font-weight:700; padding:2px 5px; border-radius:4px; }}
  .badge-wan {{ background:#3a1e1e; color:#f87171; font-size:10px; font-weight:700; padding:2px 5px; border-radius:4px; }}
</style></head>
<body>
  <h1>Security & Client Traffic Analytics</h1>
  <div class="sub">
    Window: {window // 60}m · Total requests: {data['total_requests']} · <a href="/">← Back to Dashboard</a>
  </div>

  <div class="metrics-grid">
    <div class="metric-card">
      <div class="metric-lbl">2xx Success</div>
      <div class="metric-val" style="color:#4ade80">{dist['2xx']}</div>
    </div>
    <div class="metric-card">
      <div class="metric-lbl">3xx Redirects</div>
      <div class="metric-val" style="color:#60a5fa">{dist['3xx']}</div>
    </div>
    <div class="metric-card">
      <div class="metric-lbl">4xx Client Err</div>
      <div class="metric-val" style="color:#f59e0b">{dist['4xx']}</div>
    </div>
    <div class="metric-card">
      <div class="metric-lbl">429 Rate Limits</div>
      <div class="metric-val" style="color:#ec4899">{dist['429']}</div>
    </div>
    <div class="metric-card">
      <div class="metric-lbl">5xx Server Err</div>
      <div class="metric-val" style="color:#f87171">{dist['5xx']}</div>
    </div>
  </div>

  <div class="section">
    <h2>Top Client IPs</h2>
    <table>
      <thead>
        <tr>
          <th>Client IP</th>
          <th>Requests</th>
          <th>4xx / 5xx</th>
          <th>Primary Target</th>
        </tr>
      </thead>
      <tbody>
        {client_rows}
      </tbody>
    </table>
  </div>

  <div class="section">
    <h2>Recent 4xx / Rate-Limit Events</h2>
    <table>
      <thead>
        <tr>
          <th>Time</th>
          <th>Status</th>
          <th>Client IP</th>
          <th>Host</th>
          <th>Request</th>
        </tr>
      </thead>
      <tbody>
        {susp_rows}
      </tbody>
    </table>
  </div>
</body></html>"""
    return HTMLResponse(html)
