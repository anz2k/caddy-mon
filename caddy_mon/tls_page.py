"""TLS certificate expiry page and /api/tls."""

from fastapi import Request
from fastapi.responses import HTMLResponse
from .config import TZ
from .tls_source import cert_status
from datetime import datetime


def api_tls():
    return {"entries": cert_status(), "warn_days": 30}


def tls_page(request: Request):
    entries = cert_status()
    rows = ""
    for e in entries:
        color = "#f87171" if e["warn"] else "#16a34a"
        hosts = ", ".join(e["hosts"]) if e["hosts"] else "(unknown)"
        not_after = datetime.fromisoformat(e["not_after"]).strftime("%Y-%m-%d") if e["not_after"] else "?"
        rows += f"""<tr>
          <td>{hosts}</td>
          <td style="color:{color}">{e['days_left']}d</td>
          <td>{not_after}</td>
        </tr>"""
    if not rows:
        rows = '<tr><td colspan="3" style="color:#9ca3af">No certificates found</td></tr>'
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Caddy Mon — TLS Expiry</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ font-family: system-ui, sans-serif; background:#0f1115; color:#e5e7eb; margin:0; padding:24px; }}
  h1 {{ font-size:20px; margin:0 0 4px; }}
  .sub {{ color:#9ca3af; font-size:13px; margin-bottom:20px; }}
  a {{ color:#60a5fa; }}
  table {{ border-collapse:collapse; width:100%; max-width:800px; }}
  th,td {{ text-align:left; padding:8px 10px; border-bottom:1px solid #2a2d35; font-size:13px; }}
  th {{ color:#9ca3af; font-weight:600; }}
</style></head>
<body>
  <h1>Caddy Mon — TLS Expiry</h1>
  <div class="sub">certificates mounted at /caddy-certs · <a href="/">dashboard</a></div>
  <table>
    <tr><th>Hosts</th><th>Days left</th><th>Expires</th></tr>
    {rows}
  </table>
  <script>setTimeout(() => location.reload(), 3600000);</script>
</body></html>"""
    return HTMLResponse(html)
