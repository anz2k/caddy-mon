"""Dashboard (home page) and /api/state."""

from html import escape
from datetime import datetime
from fastapi import Request
from fastapi.responses import HTMLResponse

from .config import TZ
from .caddy_source import _state, refresh, _group_hosts_by_tld


def _render_sparkline_svg(points, alive: bool) -> str:
    """Render a mini SVG latency sparkline (width 90, height 18)."""
    if not points:
        points = [0.0] * 12
    max_val = max(points) if points else 1.0
    if max_val <= 0.0:
        max_val = 1.0

    width = 90
    height = 18
    n = len(points)
    step = width / (n - 1) if n > 1 else width

    coords = []
    for i, val in enumerate(points):
        x = round(i * step, 1)
        # Invert y: higher latency = higher on the chart
        y = round((height - 3) - ((val / max_val) * (height - 6)), 1)
        coords.append(f"{x},{y}")

    stroke = "#16a34a" if alive else "#dc2626"
    pts_str = " ".join(coords)
    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" class="sparkline">'
        f'<polyline fill="none" stroke="{stroke}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" points="{pts_str}"/>'
        f"</svg>"
    )


def _render_card(s):
    """Render one site card as HTML."""
    color = "#16a34a" if s["alive"] else "#dc2626"
    up_html = ""
    for u in s.get("upstreams", []):
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
        err_msg = escape(str(u.get("error") or ""))
        probe = f"{u['status']} / {u['ms']}ms" if u["probe_ok"] else (
            f"probe failed: {err_msg}" if show_probe_err else "Caddy: alive")
        up_html += f"""
          <div class="up">
            <span class="badge" style="background:{bcolor}">{badge}</span>
            <code>{escape(u['upstream'])}</code>
            <span class="probe">{probe}</span>
          </div>"""

    aliases = [h for h in s["hosts"] if h != s["primary_host"]]
    hosts_html = f'<div class="host">{escape(s["primary_host"])}</div>'
    if aliases:
        hosts_html += '<div class="aliases"><span class="aliases-label">also:</span>'
        for a in aliases:
            hosts_html += f'<div class="alias">↳ {escape(a)}</div>'
        hosts_html += '</div>'

    # Uptime & Sparkline
    uptime = s.get("uptime_24h")
    if uptime is not None:
        up_color = "#16a34a" if uptime >= 99.0 else ("#f59e0b" if uptime >= 95.0 else "#dc2626")
        uptime_html = f'<span class="uptime-badge" style="color:{up_color}">{uptime}% (24h)</span>'
    else:
        uptime_html = '<span class="uptime-badge" style="color:#6b7280">new</span>'

    spark_svg = _render_sparkline_svg(s.get("sparkline") or [], s.get("alive", False))

    log = s.get("log")
    if log:
        log_color = "#f87171" if log["errors_5xx"] > 0 else "#6b7280"
        log_html = (f'<div class="logstat" style="color:{log_color}">'
                    f'📊 {log["requests"]} req · {log["errors_5xx"]} 5xx ({log["error_pct"]}%)</div>')
    else:
        log_html = '<div class="logstat" style="color:#6b7280">📊 no traffic (1h)</div>'

    tls = s.get("tls")
    if tls:
        tls_color = "#f87171" if tls["warn"] else "#6b7280"
        tls_label = f"⚠ {tls['days_left']}d" if tls["warn"] else f"{tls['days_left']}d"
        tls_html = f'<div class="tlsstat" style="color:{tls_color}">🔒 {tls_label}</div>'
    else:
        tls_html = '<div class="tlsstat" style="color:#6b7280">🔒 n/a</div>'

    return f"""
      <div class="card" style="border-left:6px solid {color}" data-host="{escape(s['primary_host'])}">
        <div class="card-header">
          {hosts_html}
          {uptime_html}
        </div>
        <div class="status-row">
          <div class="status" style="color:{color}">{('ALIVE' if s['alive'] else 'DEAD')} · {s['latency_ms']}ms</div>
          {spark_svg}
        </div>
        {up_html}
        {log_html}
        {tls_html}
      </div>"""


async def dashboard(request: Request):
    await refresh()
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
            <h2>{escape(g['group'])}</h2>
            <div class="grid">{cards}</div>
          </div>"""

    err_html = "".join(f"<p class='err'>⚠ {escape(e)}</p>" for e in errors)

    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Caddy Mon</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ font-family: system-ui, sans-serif; background:#0f1115; color:#e5e7eb; margin:0; padding:24px; }}
  h1 {{ font-size:20px; margin:0 0 4px; display:inline-block; }}
  .header-row {{ display:flex; align-items:center; justify-content:space-between; margin-bottom:4px; }}
  .live-dot {{ display:inline-block; width:8px; height:8px; border-radius:50%; background:#16a34a; margin-right:6px; }}
  .live-status {{ font-size:12px; color:#9ca3af; display:flex; align-items:center; }}
  .sub {{ color:#9ca3af; font-size:13px; margin-bottom:20px; }}
  .err {{ color:#f87171; }}
  a {{ color:#60a5fa; }}
  .domain-group {{ margin-bottom:28px; }}
  .domain-group h2 {{ font-size:15px; color:#9ca3af; margin:0 0 12px; font-weight:600; border-bottom:1px solid #2a2d35; padding-bottom:6px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(320px,1fr)); gap:14px; }}
  .card {{ background:#1a1d24; border-radius:10px; padding:14px 16px; transition:border-left-color 0.3s; }}
  .card-header {{ display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:4px; }}
  .host {{ font-weight:600; font-size:16px; }}
  .uptime-badge {{ font-size:11px; font-weight:600; background:#0f1115; padding:2px 6px; border-radius:4px; }}
  .status-row {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; }}
  .status {{ font-weight:700; font-size:14px; }}
  .sparkline {{ opacity:0.85; }}
  .aliases {{ margin-bottom:6px; }}
  .aliases-label {{ color:#6b7280; font-size:11px; font-weight:600; }}
  .alias {{ color:#9ca3af; font-size:11px; padding-left:8px; word-break:break-all; }}
  .logstat {{ font-size:11px; margin-top:8px; color:#6b7280; }}
  .tlsstat {{ font-size:11px; margin-top:4px; color:#6b7280; }}
  .up {{ display:flex; align-items:center; gap:8px; font-size:12px; margin-top:6px; flex-wrap:wrap; }}
  .badge {{ color:#fff; padding:2px 6px; border-radius:5px; font-size:11px; white-space:nowrap; }}
  code {{ background:#0f1115; padding:2px 6px; border-radius:4px; font-size:11px; }}
  .probe {{ color:#9ca3af; }}
</style></head>
<body>
  <div class="header-row">
    <h1>Caddy Mon</h1>
    <div class="live-status"><span class="live-dot" id="live-dot"></span><span id="conn-text">Live SSE</span></div>
  </div>
  <div class="sub">
    Caddy reverse-proxy live status · <span id="stat-total">{total}</span> sites · <span id="stat-alive">{alive}</span> alive · 
    updated <span id="updated-time">{datetime.now(TZ).strftime('%H:%M:%S')}</span> · 
    <a href="/topology">topology</a> · <a href="/logs">logs</a> · <a href="/tls">tls</a>
  </div>
  <div id="err-container">{err_html}</div>
  <div class="groups" id="groups-container">{groups_html}</div>

  <script>
    // Real-Time SSE with zero-flicker updates and fallback polling
    const liveDot = document.getElementById('live-dot');
    const connText = document.getElementById('conn-text');
    let evtSource = null;

    function connectSSE() {{
      evtSource = new EventSource('/api/events');
      
      evtSource.addEventListener('connected', () => {{
        liveDot.style.background = '#16a34a';
        connText.textContent = 'Live SSE';
      }});

      evtSource.addEventListener('state_update', (e) => {{
        try {{
          const state = JSON.parse(e.data);
          updateDashboard(state);
        }} catch (err) {{
          console.error("SSE parse error", err);
        }}
      }});

      evtSource.onerror = () => {{
        liveDot.style.background = '#f59e0b';
        connText.textContent = 'Reconnecting...';
        evtSource.close();
        setTimeout(connectSSE, 5000);
      }};
    }}

    async function pollFallback() {{
      try {{
        const res = await fetch('/api/state');
        if (res.ok) {{
          const state = await res.json();
          updateDashboard(state);
        }}
      }} catch (e) {{}}
    }}

    function updateDashboard(state) {{
      const now = new Date();
      document.getElementById('updated-time').textContent = now.toTimeString().split(' ')[0];
      if (!state.sites) return;
      
      let total = state.sites.length;
      let alive = state.sites.filter(s => s.alive).length;
      document.getElementById('stat-total').textContent = total;
      document.getElementById('stat-alive').textContent = alive;
    }}

    connectSSE();
    setInterval(pollFallback, 15000);
  </script>
</body></html>"""
    return HTMLResponse(html)


async def api_state():
    await refresh()
    return {
        "last_update": _state["last_update"],
        "sites": _state["sites"],
        "errors": _state["errors"],
    }
