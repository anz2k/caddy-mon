"""Log analytics page and /api/logs."""

from fastapi import Request
from fastapi.responses import HTMLResponse
from .config import TZ
from .log_source import log_stats
from datetime import datetime


def api_logs(window: int = 3600):
    return log_stats(window=window)


def logs_page(request: Request, window: int = 3600):
    data = log_stats(window=window)
    rows = data["rows"]
    table_rows = ""
    for r in rows:
        err_color = "#f87171" if r["error_pct"] > 0 else "#9ca3af"
        table_rows += f"""<tr>
          <td>{r['host']}</td>
          <td>{r['requests']}</td>
          <td style="color:{err_color}">{r['errors_5xx']}</td>
          <td style="color:{err_color}">{r['error_pct']}%</td>
          <td>{r['avg_ms']}ms</td>
        </tr>"""
    recent = data["recent_5xx"]
    recent_html = ""
    for e in recent[:20]:
        ts = datetime.fromtimestamp(e["ts"], TZ).strftime("%H:%M:%S") if e["ts"] else "?"
        uri = (e["uri"] or "")[:60]
        recent_html += f"""<tr><td>{ts}</td><td>{e['host']}</td><td>{e['status']}</td><td style="color:#9ca3af">{uri}</td></tr>"""
    if not recent_html:
        recent_html = '<tr><td colspan="4" style="color:#9ca3af">No 5xx errors in window</td></tr>'
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Caddy Mon — Log Analytics</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ font-family: system-ui, sans-serif; background:#0f1115; color:#e5e7eb; margin:0; padding:24px; }}
  h1 {{ font-size:20px; margin:0 0 4px; }}
  .sub {{ color:#9ca3af; font-size:13px; margin-bottom:20px; }}
  a {{ color:#60a5fa; }}
  table {{ border-collapse:collapse; width:100%; max-width:800px; margin-bottom:28px; }}
  th,td {{ text-align:left; padding:8px 10px; border-bottom:1px solid #2a2d35; font-size:13px; }}
  th {{ color:#9ca3af; font-weight:600; }}
  h2 {{ font-size:15px; color:#9ca3af; margin:0 0 12px; }}
</style></head>
<body>
  <h1>Caddy Mon — Log Analytics</h1>
  <div class="sub">last {window//60} min · <a href="/">dashboard</a> · <a href="/topology">topology</a></div>
  <h2>Per-host request summary</h2>
  <table>
    <tr><th>Host</th><th>Requests</th><th>5xx</th><th>Error %</th><th>Avg</th></tr>
    {table_rows}
  </table>
  <h2>Recent 5xx errors</h2>
  <table>
    <tr><th>Time</th><th>Host</th><th>Status</th><th>URI</th></tr>
    {recent_html}
  </table>
  <script>setTimeout(() => location.reload(), 30000);</script>
</body></html>"""
    return HTMLResponse(html)
