"""Dashboard (home page) and /api/state."""

from fastapi import Request
from fastapi.responses import HTMLResponse
from .config import TZ
from .caddy_source import _state, refresh, _group_hosts_by_tld
from datetime import datetime


def _render_card(s):
    """Render one site card as HTML."""
    color = "#16a34a" if s["alive"] else "#dc2626"
    up_html = ""
    for u in s["upstreams"]:
        if u["caddy_healthy"] is True:
            badge = "Caddy healthy"
            bcolor = "#16a34a"
        elif u["caddy_healthy"] is False:
            badge = "Caddy UNhealthy"
            bcolor = "#dc2626"
        else:
            badge = "?"
            bcolor = "#6b7280"
        show_probe_err = (not u["probe_ok"]) and (u["caddy_healthy"] is None)
        probe = f"{u['status']} / {u['ms']}ms" if u["probe_ok"] else (
            f"probe failed: {u['error']}" if show_probe_err else "Caddy: alive")
        up_html += f"""
          <div class="up">
            <span class="badge" style="background:{bcolor}">{badge}</span>
            <code>{u['upstream']}</code>
            <span class="probe">{probe}</span>
          </div>"""
    aliases = [h for h in s["hosts"] if h != s["primary_host"]]
    hosts_html = f'<div class="host">{s["primary_host"]}</div>'
    if aliases:
        hosts_html += '<div class="aliases"><span class="aliases-label">also:</span>'
        for a in aliases:
            hosts_html += f'<div class="alias">↳ {a}</div>'
        hosts_html += '</div>'
    log = s.get("log")
    if log:
        log_color = "#f87171" if log["errors_5xx"] > 0 else "#6b7280"
        log_html = (f'<div class="logstat" style="color:{log_color}">'
                    f'📊 {log["requests"]} req · {log["errors_5xx"]} 5xx ({log["error_pct"]}%)</div>')
    else:
        log_html = '<div class="logstat" style="color:#6b7280">📊 no traffic (1h)</div>'
    return f"""
      <div class="card" style="border-left:6px solid {color}">
        {hosts_html}
        <div class="status" style="color:{color}">{('ALIVE' if s['alive'] else 'DEAD')} · {s['latency_ms']}ms</div>
        {up_html}
        {log_html}
      </div>"""


def dashboard(request: Request):
    refresh()
    sites = _state["sites"]
    errors = _state["errors"]
    grouped = _group_hosts_by_tld(sites)
    total = sum(len(g["sites"]) for g in grouped)
    alive = sum(1 for g in grouped for s in g["sites"] if s["alive"])

    groups_html = ""
    for g in grouped:
        cards = "".join(_render_card(s) for s in g["sites"])
        groups_html += f"""
          <div class="domain-group">
            <h2>{g['group']}</h2>
            <div class="grid">{cards}</div>
          </div>"""

    err_html = "".join(f"<p class='err'>⚠ {e}</p>" for e in errors)

    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Caddy Mon</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ font-family: system-ui, sans-serif; background:#0f1115; color:#e5e7eb; margin:0; padding:24px; }}
  h1 {{ font-size:20px; margin:0 0 4px; }}
  .sub {{ color:#9ca3af; font-size:13px; margin-bottom:20px; }}
  .err {{ color:#f87171; }}
  a {{ color:#60a5fa; }}
  .domain-group {{ margin-bottom:28px; }}
  .domain-group h2 {{ font-size:15px; color:#9ca3af; margin:0 0 12px; font-weight:600; border-bottom:1px solid #2a2d35; padding-bottom:6px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(300px,1fr)); gap:14px; }}
  .card {{ background:#1a1d24; border-radius:10px; padding:14px 16px; }}
  .host {{ font-weight:600; font-size:16px; }}
  .aliases {{ margin-bottom:6px; }}
  .aliases-label {{ color:#6b7280; font-size:11px; font-weight:600; }}
  .alias {{ color:#9ca3af; font-size:11px; padding-left:8px; word-break:break-all; }}
  .logstat {{ font-size:11px; margin-top:8px; color:#6b7280; }}
  .status {{ font-weight:700; font-size:14px; margin-bottom:8px; }}
  .up {{ display:flex; align-items:center; gap:8px; font-size:12px; margin-top:6px; flex-wrap:wrap; }}
  .badge {{ color:#fff; padding:2px 6px; border-radius:5px; font-size:11px; white-space:nowrap; }}
  code {{ background:#0f1115; padding:2px 6px; border-radius:4px; font-size:11px; }}
  .probe {{ color:#9ca3af; }}
</style></head>
<body>
  <h1>Caddy Mon</h1>
  <div class="sub">Caddy reverse-proxy live status · {total} sites · {alive} alive · updated {datetime.now(TZ).strftime('%H:%M:%S')} · <a href="/topology" style="color:#60a5fa">topology</a> · <a href="/logs" style="color:#60a5fa">logs</a></div>
  {err_html}
  <div class="groups">{groups_html}</div>
  <script>
    setTimeout(() => location.reload(), 12000);
  </script>
</body></html>"""
    return HTMLResponse(html)


def api_state():
    refresh()
    return {
        "last_update": _state["last_update"],
        "sites": _state["sites"],
        "errors": _state["errors"],
    }
