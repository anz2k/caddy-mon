"""Dashboard (home page) and /api/state with modern Tailwind CSS UI."""

from html import escape
from datetime import datetime
from typing import Dict, Any, List, Optional

try:
    from fastapi import Request
    from fastapi.responses import HTMLResponse
except ImportError:
    Request = object  # type: ignore
    HTMLResponse = object  # type: ignore

from .config import TZ
from .caddy_source import _state, refresh, _group_hosts_by_tld
from .db import get_all_maintenance


def _render_sparkline_svg(points: List[float], state_type: str = "alive") -> str:
    """Render a gradient-filled SVG sparkline (width 64, height 16)."""
    if not points:
        points = [0.0] * 12
    max_val = max(points) if points else 1.0
    if max_val <= 0.0:
        max_val = 1.0

    width = 64
    height = 16
    n = len(points)
    step = width / (n - 1) if n > 1 else width

    coords = []
    for i, val in enumerate(points):
        x = round(i * step, 1)
        y = round((height - 2) - ((val / max_val) * (height - 4)), 1)
        coords.append((x, y))

    line_d = f"M{coords[0][0]},{coords[0][1]}" + "".join(f" L{x},{y}" for x, y in coords[1:])
    fill_d = (
        f"M0,{height} L{coords[0][0]},{coords[0][1]}"
        + "".join(f" L{x},{y}" for x, y in coords[1:])
        + f" L{width},{height} Z"
    )

    cls_prefix = "live" if state_type == "alive" else ("maint" if state_type == "maint" else "down")

    return (
        f'<svg class="w-16 h-4" viewBox="0 0 {width} {height}">'
        f'<path class="sparkline-fill-{cls_prefix}" d="{fill_d}"></path>'
        f'<path class="sparkline-{cls_prefix}" d="{line_d}"></path>'
        f"</svg>"
    )


def _render_card(s: Dict[str, Any], maintenance_map: Optional[Dict[str, Any]] = None) -> str:
    """Render one modern Tailwind site card."""
    primary = s.get("primary_host", "")
    is_maint = bool(maintenance_map and primary in maintenance_map)

    if is_maint:
        state_type = "maint"
        border_class = "bg-status-maint"
        glow_class = "card-glow-maint"
        status_text_color = "text-status-maint"
        status_label = "MAINTENANCE"
    elif s.get("alive"):
        state_type = "alive"
        border_class = "bg-status-alive"
        glow_class = "card-glow-alive"
        status_text_color = "text-status-alive"
        status_label = "ALIVE"
    else:
        state_type = "down"
        border_class = "bg-status-down"
        glow_class = "card-glow-down"
        status_text_color = "text-status-down"
        status_label = "DEAD"

    # Upstreams HTML
    up_html = ""
    for u in s.get("upstreams", []):
        if u.get("caddy_healthy") is True:
            badge_bg = "bg-status-alive text-[#022c22]"
            badge_icon = "check_circle"
            badge_text = "Caddy healthy"
        elif u.get("caddy_healthy") is False:
            badge_bg = "bg-status-down text-white"
            badge_icon = "cancel"
            badge_text = "Caddy unhealthy"
        else:
            badge_bg = "bg-surface-variant text-on-surface-variant"
            badge_icon = "help"
            badge_text = "Caddy ?"

        show_probe_err = (not u.get("probe_ok")) and (u.get("caddy_healthy") is None)
        err_msg = escape(str(u.get("error") or ""))
        probe = f"{u.get('status')} / {u.get('ms')}ms" if u.get("probe_ok") else (
            f"failed: {err_msg}" if show_probe_err else "alive")

        up_html += f"""
          <div class="flex items-center gap-2 text-xs font-mono flex-wrap">
            <span class="{badge_bg} px-1.5 py-0.5 rounded-sm text-[10px] font-bold uppercase tracking-wider flex items-center gap-1">
              <span class="material-symbols-outlined text-[12px]">{badge_icon}</span> {badge_text}
            </span>
            <span class="text-on-surface-variant flex items-center gap-1">
              <span class="material-symbols-outlined text-[14px] text-outline">dns</span> {escape(u.get('upstream', ''))}
            </span>
            <span class="text-on-surface-variant ml-auto font-mono text-[11px]">{probe}</span>
          </div>"""

    # Aliases
    aliases = [h for h in s.get("hosts", []) if h != s.get("primary_host")]
    aliases_html = ""
    if aliases:
        items = "".join(f"<li>↳ {escape(a)}</li>" for a in aliases)
        aliases_html = f"""
          <div class="text-xs font-mono text-outline mt-1 mb-1">also:</div>
          <ul class="text-xs font-mono text-on-surface-variant flex flex-col gap-0.5 pl-2 border-l border-white/10 ml-1 mb-2">
            {items}
          </ul>"""

    # Uptime Badge
    uptime = s.get("uptime_24h")
    if is_maint:
        uptime_html = '<span class="text-status-maint text-[11px] font-bold uppercase tracking-wider font-mono">maintenance</span>'
    elif uptime is not None:
        up_color = "text-status-alive" if uptime >= 99.0 else ("text-status-maint" if uptime >= 95.0 else "text-status-down")
        uptime_html = f'<span class="{up_color} text-[11px] font-bold tracking-wider font-mono">{uptime}% (24h)</span>'
    else:
        uptime_html = '<span class="text-outline text-[11px] font-bold font-mono">new</span>'

    spark_svg = _render_sparkline_svg(s.get("sparkline") or [], state_type)

    # Log statistics
    log = s.get("log")
    if log:
        log_color = "text-status-down" if log.get("errors_5xx", 0) > 0 else "text-on-surface-variant"
        log_text = f'{log.get("requests", 0)} req · {log.get("errors_5xx", 0)} 5xx ({log.get("error_pct", 0)}%)'
    else:
        log_color = "text-outline"
        log_text = "no traffic (1h)"
    log_html = f"""
      <div class="flex items-center gap-2 text-xs font-mono {log_color} mt-1">
        <span class="material-symbols-outlined text-[14px] text-outline">bar_chart</span>
        <span>{log_text}</span>
      </div>"""

    # TLS Status
    tls = s.get("tls")
    if tls:
        tls_color = "text-status-down" if tls.get("warn") else "text-on-surface-variant"
        tls_text = f'⚠ {tls.get("days_left")}d' if tls.get("warn") else f'{tls.get("days_left")}d remaining'
    else:
        tls_color = "text-outline"
        tls_text = "n/a (ACME managed)"
    tls_html = f"""
      <div class="flex items-center gap-2 text-xs font-mono {tls_color}">
        <span class="material-symbols-outlined text-[14px]">lock</span>
        <span>{tls_text}</span>
      </div>"""

    maint_btn_text = "End Maint" if is_maint else "Maint"

    return f"""
      <article class="bg-[#1e293b] rounded-lg border border-white/10 p-4 flex flex-col gap-3 relative overflow-hidden {glow_class}" id="card-{escape(primary)}" data-host="{escape(primary)}">
        <div class="absolute left-0 top-0 bottom-0 w-1 {border_class}"></div>
        <div class="flex justify-between items-start gap-2">
          <div class="flex flex-col min-w-0 flex-1">
            <h3 class="text-base font-mono font-semibold text-on-surface truncate pr-2" title="{escape(primary)}">{escape(primary)}</h3>
            {aliases_html}
            <div class="flex items-center gap-2 mt-1">
              <span class="{status_text_color} font-bold text-[11px] tracking-wider uppercase font-mono">{status_label}</span>
              <span class="text-outline-variant">•</span>
              <span class="{status_text_color} text-xs font-mono">{s.get('latency_ms', 0.0)}ms</span>
            </div>
          </div>
          <div class="flex flex-col items-end gap-2">
            <div class="flex items-center gap-2">
              {uptime_html}
              <div class="flex gap-1">
                <button onclick="runProbe('{escape(primary)}')" class="bg-transparent border border-white/20 hover:border-white/40 text-on-surface-variant hover:text-on-surface text-[10px] uppercase font-bold px-1.5 py-0.5 rounded flex items-center gap-1 transition-colors cursor-pointer" title="Test upstream now">
                  <span class="material-symbols-outlined text-[14px]">bolt</span> Test
                </button>
                <button onclick="toggleMaint('{escape(primary)}', {str(not is_maint).lower()})" class="bg-transparent border border-white/20 hover:border-white/40 text-on-surface-variant hover:text-on-surface text-[10px] uppercase font-bold px-1.5 py-0.5 rounded flex items-center gap-1 transition-colors cursor-pointer" title="Toggle maintenance mode">
                  <span class="material-symbols-outlined text-[14px]">build</span> {maint_btn_text}
                </button>
              </div>
            </div>
            {spark_svg}
          </div>
        </div>
        <div class="flex flex-col gap-1.5 mt-auto pt-2 border-t border-white/5">
          {up_html}
          {log_html}
          {tls_html}
        </div>
      </article>"""


async def dashboard(request: Request):
    """Render main modern dashboard."""
    await refresh()
    sites = _state.get("sites", [])
    errors = _state.get("errors", [])
    maintenance_map = get_all_maintenance()
    grouped = _group_hosts_by_tld(sites)
    total = sum(len(g["sites"]) for g in grouped)
    alive = sum(1 for g in grouped for s in g["sites"] if s["alive"])

    sections_html = ""
    for g in grouped:
        cards = "".join(_render_card(s, maintenance_map) for s in g["sites"])
        sections_html += f"""
          <section class="flex flex-col gap-4">
            <h2 class="text-lg font-semibold text-on-surface border-b border-white/5 pb-2">{escape(g['group'])}</h2>
            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
              {cards}
            </div>
          </section>"""

    err_html = ""
    if errors:
        err_items = "".join(f"<li class='text-sm text-status-down'>⚠ {escape(e)}</li>" for e in errors)
        err_html = f"""
          <div class="bg-status-down/10 border border-status-down/30 rounded-lg p-4 mb-6">
            <ul class="list-disc pl-4 flex flex-col gap-1">{err_items}</ul>
          </div>"""

    html = f"""<!doctype html>
<html class="dark" lang="en"><head>
<meta charset="utf-8"/>
<meta content="width=device-width, initial-scale=1.0" name="viewport"/>
<title>Caddy Mon - Dashboard</title>
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
          surface: "#0b1326",
          "surface-container": "#1e293b",
          "surface-container-high": "#1e293b",
          "surface-container-low": "#131b2e",
          "surface-container-highest": "#2d3449",
          primary: "#0ea5e9",
          "primary-container": "#0ea5e9",
          "on-primary": "#ffffff",
          "on-surface": "#f8fafc",
          "on-surface-variant": "#bec8d2",
          outline: "#88929b",
          "outline-variant": "#3e4850",
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
  .card-glow-alive {{ box-shadow: -4px 0 15px -5px rgba(16, 185, 129, 0.15); }}
  .card-glow-down {{ box-shadow: -4px 0 15px -5px rgba(225, 29, 72, 0.2); }}
  .card-glow-maint {{ box-shadow: -4px 0 15px -5px rgba(245, 158, 11, 0.2); }}
  .sparkline-live {{ stroke: #10b981; stroke-width: 1.5; fill: none; stroke-linecap: round; stroke-linejoin: round; }}
  .sparkline-down {{ stroke: #e11d48; stroke-width: 1.5; fill: none; stroke-linecap: round; stroke-linejoin: round; }}
  .sparkline-maint {{ stroke: #f59e0b; stroke-width: 1.5; fill: none; stroke-linecap: round; stroke-linejoin: round; }}
  .sparkline-fill-live {{ fill: url(#sparkline-gradient-live); opacity: 0.2; }}
  .sparkline-fill-down {{ fill: url(#sparkline-gradient-down); opacity: 0.2; }}
  .sparkline-fill-maint {{ fill: url(#sparkline-gradient-maint); opacity: 0.2; }}
  .material-symbols-outlined {{ font-variation-settings: 'FILL' 0, 'wght' 400, 'GRAD' 0, 'opsz' 24; vertical-align: middle; }}
  .no-scrollbar::-webkit-scrollbar {{ display: none; }}
  .no-scrollbar {{ -ms-overflow-style: none; scrollbar-width: none; }}
</style>
</head>
<body class="bg-background text-on-surface min-h-screen flex flex-col antialiased">
  <!-- Gradient definitions for SVG sparklines -->
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

  <!-- Top Header -->
  <header class="bg-background docked full-width top-0 flex flex-col gap-2 w-full pt-6 px-gutter max-w-container-max mx-auto border-b-0">
    <div class="flex justify-between items-center w-full">
      <h1 class="text-2xl font-bold text-on-surface tracking-tight font-sans">Caddy Mon</h1>
      <div class="flex items-center gap-4">
        <div class="flex items-center gap-2 px-3 py-1.5 rounded-full bg-status-alive/10 border border-status-alive/20">
          <span class="w-2 h-2 rounded-full bg-status-alive animate-pulse" id="live-dot"></span>
          <span class="text-status-alive text-[11px] font-bold uppercase tracking-wider font-mono" id="conn-text">Live SSE</span>
        </div>
        <button onclick="location.reload()" class="text-on-surface-variant hover:text-primary transition-colors cursor-pointer w-8 h-8 flex items-center justify-center rounded-lg hover:bg-surface-container" title="Refresh dashboard">
          <span class="material-symbols-outlined text-xl">sensors</span>
        </button>
      </div>
    </div>

    <!-- Summary Metrics Bar -->
    <div class="flex flex-col md:flex-row md:items-center gap-2 md:gap-4 mt-2 pb-4 text-sm text-on-surface-variant border-b border-white/5 font-sans">
      <div class="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span>Caddy reverse-proxy live status</span>
        <span class="text-outline-variant hidden md:inline">•</span>
        <span class="font-mono text-on-surface font-semibold" id="stat-total">{total}</span> <span>sites</span>
        <span class="text-outline-variant hidden md:inline">•</span>
        <span class="font-mono text-status-alive font-semibold" id="stat-alive">{alive}</span> <span class="text-status-alive">ALIVE</span>
        <span class="text-outline-variant hidden md:inline">•</span>
        <span>Updated <span class="font-mono text-on-surface" id="updated-time">{datetime.now(TZ).strftime('%H:%M:%S')}</span></span>
      </div>
    </div>

    <!-- Navigation Bar -->
    <nav class="flex gap-6 mt-2 overflow-x-auto pb-1 no-scrollbar border-b border-white/5 text-xs font-bold uppercase tracking-wider font-sans">
      <a class="text-primary border-b-2 border-primary pb-2 whitespace-nowrap" href="/">Dashboard</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/topology">Topology</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/logs">Logs</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/security">Security</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/tls">TLS</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/caddy/config">Caddy Config</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/status">Status Page</a>
    </nav>
  </header>

  <!-- Main Content -->
  <main class="flex-1 w-full max-w-container-max mx-auto px-gutter py-8 flex flex-col gap-10">
    {err_html}
    <div class="flex flex-col gap-10" id="groups-container">
      {sections_html}
    </div>
  </main>

  <!-- Footer -->
  <footer class="bg-background full-width py-8 border-t border-white/5 mt-auto">
    <div class="max-w-container-max mx-auto px-gutter flex flex-col md:flex-row justify-between items-center gap-4 text-xs font-sans text-on-surface-variant">
      <div>Caddy Mon • Live reverse-proxy visibility</div>
      <div class="flex gap-6">
        <a class="hover:text-primary transition-colors" href="/status">Public Status</a>
        <a class="hover:text-primary transition-colors" href="/status/feed.xml">RSS Feed</a>
        <a class="hover:text-primary transition-colors" href="/caddy/config">Config</a>
      </div>
    </div>
  </footer>

  <!-- Toast Notification -->
  <div id="toast" class="fixed bottom-5 right-5 bg-surface-container border border-primary/40 text-on-surface px-4 py-2.5 rounded-lg text-sm shadow-xl hidden z-50 font-sans flex items-center gap-2"></div>

  <script>
    const liveDot = document.getElementById('live-dot');
    const connText = document.getElementById('conn-text');
    let evtSource = null;

    function showToast(msg, duration = 3500) {{
      const t = document.getElementById('toast');
      t.innerHTML = msg;
      t.classList.remove('hidden');
      setTimeout(() => {{ t.classList.add('hidden'); }}, duration);
    }}

    async function runProbe(host) {{
      showToast('<span class="material-symbols-outlined text-primary text-base">hourglass_top</span> Testing ' + escapeHtml(host) + '...');
      try {{
        const r = await fetch('/api/probe/' + encodeURIComponent(host), {{ method: 'POST' }});
        const res = await r.json();
        if (res.ok) {{
          showToast('<span class="material-symbols-outlined text-status-alive text-base">check_circle</span> ' + escapeHtml(host) + ': ' + res.latency_ms + 'ms (' + (res.upstreams[0]?.status_code || 'OK') + ')');
        }} else {{
          showToast('<span class="material-symbols-outlined text-status-down text-base">error</span> ' + escapeHtml(host) + ' failed: ' + (res.upstreams[0]?.error || 'Unreachable'));
        }}
      }} catch (e) {{
        showToast('<span class="material-symbols-outlined text-status-down text-base">error</span> Error: ' + e);
      }}
    }}

    async function toggleMaint(host, enable) {{
      const reason = enable ? prompt('Reason for maintenance (optional):', 'Scheduled maintenance') : '';
      if (enable && reason === null) return;
      try {{
        const r = await fetch('/api/maintenance/' + encodeURIComponent(host), {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ enabled: enable, reason: reason || '' }})
        }});
        if (r.ok) {{
          showToast('<span class="material-symbols-outlined text-status-maint text-base">build</span> ' + escapeHtml(host) + (enable ? ' placed in maintenance' : ' resumed from maintenance'));
          setTimeout(() => location.reload(), 1000);
        }}
      }} catch (e) {{
        showToast('Failed to update maintenance: ' + e);
      }}
    }}

    function escapeHtml(str) {{
      return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }}

    function connectSSE() {{
      evtSource = new EventSource('/api/events');
      
      evtSource.addEventListener('connected', () => {{
        liveDot.className = 'w-2 h-2 rounded-full bg-status-alive animate-pulse';
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
        liveDot.className = 'w-2 h-2 rounded-full bg-status-maint';
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
