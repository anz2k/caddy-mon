"""Traffic and visitor analytics page and API powered by Caddy proxy access logs."""

import time
from html import escape
from datetime import datetime
from typing import Optional, List, Dict, Any, Union

try:
    from fastapi import Request
    from fastapi.responses import HTMLResponse
except ImportError:
    Request = Any  # type: ignore
    HTMLResponse = Any  # type: ignore

from .config import TZ
from .caddy_source import _state
from .log_source import _LOG_CACHE, ingest_logs, parse_user_agent, parse_referer, _normalize_host
from .db import upsert_hourly_traffic, get_traffic_history


def _fmt_bytes(num_bytes: int) -> str:
    """Format bytes into human-readable string (KB, MB, GB)."""
    if num_bytes < 1024:
        return f"{num_bytes} B"
    kb = num_bytes / 1024
    if kb < 1024:
        return f"{kb:.1f} KB"
    mb = kb / 1024
    if mb < 1024:
        return f"{mb:.1f} MB"
    gb = mb / 1024
    return f"{gb:.2f} GB"


def get_traffic_analytics(window: int = 86400, host_filter: Optional[str] = None) -> Dict[str, Any]:
    """Compute comprehensive traffic, visitor, and device metrics over a time window."""
    ingest_logs()
    now = time.time()
    cutoff = now - window

    target_host = _normalize_host(host_filter) if host_filter else None

    # Filter log entries
    matched = []
    hourly_buckets: Dict[int, Dict[str, Any]] = {}
    path_counts: Dict[str, Dict[str, Any]] = {}
    referer_counts: Dict[str, int] = {}
    browser_counts: Dict[str, int] = {}
    os_counts: Dict[str, int] = {}
    device_counts: Dict[str, int] = {}
    bot_counts: Dict[str, int] = {}
    domain_stats: Dict[str, Dict[str, Any]] = {}
    unique_ips = set()
    total_bytes = 0
    errors_4xx = 0
    errors_5xx = 0
    durations = []
    human_reqs = 0
    bot_reqs = 0

    for e in _LOG_CACHE:
        ts = e.get("ts")
        if ts is None or ts < cutoff:
            continue
        h = e.get("host") or ""
        if target_host and h != target_host:
            continue

        matched.append(e)
        ip = e.get("client_ip")
        if ip:
            unique_ips.add(ip)

        b = e.get("bytes") or 0
        total_bytes += b

        status = e.get("status")
        if isinstance(status, int):
            if 400 <= status < 500:
                errors_4xx += 1
            elif status >= 500:
                errors_5xx += 1

        dur = e.get("duration")
        if isinstance(dur, (int, float)) and dur > 0:
            durations.append(dur)

        # Path tracking
        uri = e.get("uri") or "/"
        clean_path = uri.split("?")[0]
        if clean_path:
            p_entry = path_counts.setdefault(clean_path, {"count": 0, "durations": [], "status_2xx": 0, "status_4xx": 0, "status_5xx": 0})
            p_entry["count"] += 1
            if isinstance(dur, (int, float)):
                p_entry["durations"].append(dur)
            if isinstance(status, int):
                if status < 400:
                    p_entry["status_2xx"] += 1
                elif status < 500:
                    p_entry["status_4xx"] += 1
                else:
                    p_entry["status_5xx"] += 1

        # Referer
        ref_domain = parse_referer(e.get("referer"))
        referer_counts[ref_domain] = referer_counts.get(ref_domain, 0) + 1

        # User-Agent / Classification
        ua_info = parse_user_agent(e.get("user_agent"))
        if ua_info["category"] == "Bot":
            bot_reqs += 1
            bot_name = ua_info.get("bot") or "Other Bot"
            bot_counts[bot_name] = bot_counts.get(bot_name, 0) + 1
        else:
            human_reqs += 1
            br = ua_info.get("browser", "Other")
            browser_counts[br] = browser_counts.get(br, 0) + 1
            os_name = ua_info.get("os", "Other")
            os_counts[os_name] = os_counts.get(os_name, 0) + 1
            dev = ua_info.get("device", "Desktop")
            device_counts[dev] = device_counts.get(dev, 0) + 1

        # Domain breakdown
        if h:
            d_stat = domain_stats.setdefault(h, {"requests": 0, "unique_ips": set(), "bytes": 0, "errors": 0})
            d_stat["requests"] += 1
            if ip:
                d_stat["unique_ips"].add(ip)
            d_stat["bytes"] += b
            if isinstance(status, int) and status >= 400:
                d_stat["errors"] += 1

        # Hourly bucket
        hour_ts = int(ts // 3600) * 3600
        hb = hourly_buckets.setdefault(hour_ts, {"requests": 0, "ips": set(), "bytes": 0, "errors": 0, "durations": []})
        hb["requests"] += 1
        if ip:
            hb["ips"].add(ip)
        hb["bytes"] += b
        if isinstance(status, int) and status >= 400:
            hb["errors"] += 1
        if isinstance(dur, (int, float)):
            hb["durations"].append(dur)

    total_reqs = len(matched)
    avg_latency = (sum(durations) / len(durations) * 1000) if durations else 0.0
    err_rate = round(((errors_4xx + errors_5xx) / total_reqs * 100), 1) if total_reqs else 0.0
    human_pct = round((human_reqs / total_reqs * 100), 1) if total_reqs else 0.0

    # Build timeline (fill chronological buckets)
    timeline = []
    bucket_step = 3600 if window <= 86400 * 2 else 86400
    start_bucket = int(cutoff // bucket_step) * bucket_step
    end_bucket = int(now // bucket_step) * bucket_step
    curr_b = start_bucket

    while curr_b <= end_bucket:
        hb = hourly_buckets.get(curr_b)
        dt = datetime.fromtimestamp(curr_b, TZ)
        label = dt.strftime("%H:00") if bucket_step == 3600 else dt.strftime("%b %d")
        if hb:
            timeline.append({
                "ts": curr_b,
                "label": label,
                "requests": hb["requests"],
                "visitors": len(hb["ips"]),
                "bytes": hb["bytes"],
                "errors": hb["errors"],
            })
        else:
            timeline.append({
                "ts": curr_b,
                "label": label,
                "requests": 0,
                "visitors": 0,
                "bytes": 0,
                "errors": 0,
            })
        curr_b += bucket_step

    # Format top paths
    top_paths = []
    for p, pdata in sorted(path_counts.items(), key=lambda item: -item[1]["count"])[:10]:
        cnt = pdata["count"]
        pct = round(cnt / total_reqs * 100, 1) if total_reqs else 0.0
        avg_p_ms = (sum(pdata["durations"]) / len(pdata["durations"]) * 1000) if pdata["durations"] else 0.0
        top_paths.append({
            "path": p,
            "count": cnt,
            "pct": pct,
            "avg_ms": round(avg_p_ms, 1),
            "status_2xx": pdata["status_2xx"],
            "status_4xx": pdata["status_4xx"],
            "status_5xx": pdata["status_5xx"],
        })

    # Format referrers
    top_referrers = []
    for ref, count in sorted(referer_counts.items(), key=lambda x: -x[1])[:10]:
        top_referrers.append({
            "source": ref,
            "count": count,
            "pct": round(count / total_reqs * 100, 1) if total_reqs else 0.0,
        })

    # Format browsers
    browsers_list = []
    for br, count in sorted(browser_counts.items(), key=lambda x: -x[1]):
        browsers_list.append({
            "name": br,
            "count": count,
            "pct": round(count / (human_reqs or 1) * 100, 1),
        })

    # Format OS
    os_list = []
    for os_name, count in sorted(os_counts.items(), key=lambda x: -x[1]):
        os_list.append({
            "name": os_name,
            "count": count,
            "pct": round(count / (human_reqs or 1) * 100, 1),
        })

    # Format Devices
    device_list = []
    for dev, count in sorted(device_counts.items(), key=lambda x: -x[1]):
        device_list.append({
            "name": dev,
            "count": count,
            "pct": round(count / (human_reqs or 1) * 100, 1),
        })

    # Format Bots
    bot_list = []
    for bname, count in sorted(bot_counts.items(), key=lambda x: -x[1]):
        bot_list.append({
            "name": bname,
            "count": count,
            "pct": round(count / (bot_reqs or 1) * 100, 1),
        })

    # Format domain table
    domains = []
    for d, stat in sorted(domain_stats.items(), key=lambda x: -x[1]["requests"]):
        d_err_pct = round(stat["errors"] / stat["requests"] * 100, 1) if stat["requests"] else 0.0
        domains.append({
            "host": d,
            "requests": stat["requests"],
            "unique_visitors": len(stat["unique_ips"]),
            "bytes_formatted": _fmt_bytes(stat["bytes"]),
            "error_pct": d_err_pct,
        })

    return {
        "window_seconds": window,
        "host_filter": host_filter,
        "summary": {
            "total_requests": total_reqs,
            "unique_visitors": len(unique_ips),
            "total_bytes": total_bytes,
            "total_bytes_formatted": _fmt_bytes(total_bytes),
            "human_requests": human_reqs,
            "bot_requests": bot_reqs,
            "human_pct": human_pct,
            "avg_latency_ms": round(avg_latency, 1),
            "errors_4xx": errors_4xx,
            "errors_5xx": errors_5xx,
            "error_rate_pct": err_rate,
        },
        "timeline": timeline,
        "top_paths": top_paths,
        "top_referrers": top_referrers,
        "browsers": browsers_list,
        "os_list": os_list,
        "devices": device_list,
        "bots": bot_list,
        "domains": domains,
    }


def api_analytics(window: int = 86400, host: Optional[str] = None) -> Dict[str, Any]:
    """JSON API endpoint for traffic analytics."""
    return get_traffic_analytics(window=window, host_filter=host)


async def analytics(request: Request) -> HTMLResponse:
    """Render the full Tailwind Traffic and Visitor Analytics page."""
    from .caddy_source import refresh
    await refresh()

    sites = _state.get("sites", [])
    all_hosts = sorted({s.get("primary_host") for s in sites if s.get("primary_host")})

    # Default 24h window
    data = get_traffic_analytics(window=86400)
    summary = data["summary"]
    timeline = data["timeline"]

    # Generate SVG Chart for requests & unique visitors
    svg_chart = _generate_traffic_svg(timeline)

    # Domain Options HTML
    host_options = '<option value="">All Proxied Domains</option>'
    for h in all_hosts:
        host_options += f'<option value="{escape(h)}">{escape(h)}</option>'

    # Paths HTML rows
    path_rows = ""
    for p in data["top_paths"]:
        path_rows += f"""
        <tr class="border-b border-white/5 hover:bg-white/5 transition-colors">
          <td class="p-2.5 font-mono text-xs text-on-surface truncate max-w-xs" title="{escape(p['path'])}">{escape(p['path'])}</td>
          <td class="p-2.5 font-mono text-xs text-right font-bold text-primary">{p['count']}</td>
          <td class="p-2.5 font-mono text-xs text-right text-outline">{p['pct']}%</td>
          <td class="p-2.5 font-mono text-xs text-right text-on-surface-variant">{p['avg_ms']}ms</td>
          <td class="p-2.5 font-mono text-xs text-right">
            <span class="text-status-alive">{p['status_2xx']}</span> / 
            <span class="text-status-maint">{p['status_4xx']}</span> / 
            <span class="text-status-down">{p['status_5xx']}</span>
          </td>
        </tr>"""
    if not path_rows:
        path_rows = '<tr><td colspan="5" class="p-4 text-center text-outline text-xs">No traffic recorded in this time window</td></tr>'

    # Referrers HTML
    ref_rows = ""
    for r in data["top_referrers"]:
        ref_rows += f"""
        <div class="flex items-center justify-between py-1.5 border-b border-white/5 text-xs font-mono">
          <span class="text-on-surface truncate max-w-xs">{escape(r['source'])}</span>
          <div class="flex items-center gap-3">
            <span class="text-on-surface-variant">{r['count']}</span>
            <span class="text-outline text-[11px] w-12 text-right">{r['pct']}%</span>
          </div>
        </div>"""
    if not ref_rows:
        ref_rows = '<div class="text-xs text-outline py-2">No referrer data</div>'

    # Browsers HTML
    browser_bars = ""
    for b in data["browsers"][:5]:
        browser_bars += f"""
        <div class="flex flex-col gap-1 text-xs">
          <div class="flex justify-between font-mono">
            <span class="text-on-surface">{escape(b['name'])}</span>
            <span class="text-outline">{b['count']} ({b['pct']}%)</span>
          </div>
          <div class="w-full bg-white/5 rounded-full h-1.5 overflow-hidden">
            <div class="bg-primary h-full rounded-full" style="width: {b['pct']}%"></div>
          </div>
        </div>"""
    if not browser_bars:
        browser_bars = '<div class="text-xs text-outline py-2">No browser data</div>'

    # OS HTML
    os_bars = ""
    for o in data["os_list"][:5]:
        os_bars += f"""
        <div class="flex flex-col gap-1 text-xs">
          <div class="flex justify-between font-mono">
            <span class="text-on-surface">{escape(o['name'])}</span>
            <span class="text-outline">{o['count']} ({o['pct']}%)</span>
          </div>
          <div class="w-full bg-white/5 rounded-full h-1.5 overflow-hidden">
            <div class="bg-status-alive h-full rounded-full" style="width: {o['pct']}%"></div>
          </div>
        </div>"""
    if not os_bars:
        os_bars = '<div class="text-xs text-outline py-2">No OS data</div>'

    # Domain Leaderboard HTML
    domain_rows = ""
    for d in data["domains"]:
        domain_rows += f"""
        <tr class="border-b border-white/5 hover:bg-white/5 transition-colors">
          <td class="p-2.5 font-mono text-xs font-bold text-on-surface">{escape(d['host'])}</td>
          <td class="p-2.5 font-mono text-xs text-right text-primary font-semibold">{d['requests']}</td>
          <td class="p-2.5 font-mono text-xs text-right text-status-alive">{d['unique_visitors']}</td>
          <td class="p-2.5 font-mono text-xs text-right text-on-surface-variant">{d['bytes_formatted']}</td>
          <td class="p-2.5 font-mono text-xs text-right {'text-status-down' if d['error_pct'] > 5 else 'text-outline'}">{d['error_pct']}%</td>
        </tr>"""
    if not domain_rows:
        domain_rows = '<tr><td colspan="5" class="p-4 text-center text-outline text-xs">No per-domain traffic recorded</td></tr>'

    html = f"""<!doctype html>
<html class="dark" lang="en"><head>
<meta charset="utf-8"/>
<meta content="width=device-width, initial-scale=1.0" name="viewport"/>
<title>Caddy Mon - Traffic & Visitor Analytics</title>
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
</style>
</head>
<body class="min-h-screen flex flex-col justify-between">
<div>
  <!-- Header -->
  <header class="bg-background docked full-width top-0 flex flex-col gap-2 w-full pt-6 px-gutter max-w-container-max mx-auto">
    <div class="flex justify-between items-center w-full">
      <div class="flex items-center gap-3">
        <h1 class="text-2xl font-bold text-on-surface tracking-tight font-sans">Caddy Mon</h1>
        <span class="px-2.5 py-0.5 rounded-full text-[11px] font-bold uppercase tracking-wider bg-primary/10 border border-primary/30 text-primary font-mono">Analytics</span>
      </div>
      <div class="flex items-center gap-3">
        <button onclick="location.reload()" class="bg-[#1e293b] hover:bg-slate-700 text-on-surface px-3 py-1.5 rounded-lg text-xs font-mono flex items-center gap-1.5 border border-white/10 transition-colors cursor-pointer">
          <span class="material-symbols-outlined text-base">refresh</span> Refresh
        </button>
      </div>
    </div>

    <!-- Navigation -->
    <nav class="flex gap-6 mt-4 overflow-x-auto pb-1 no-scrollbar border-b border-white/5 text-xs font-bold uppercase tracking-wider font-sans">
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/">Dashboard</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/topology">Topology</a>
      <a class="text-primary border-b-2 border-primary pb-2 whitespace-nowrap" href="/analytics">Analytics</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/logs">Logs</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/security">Security</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/tls">TLS</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/caddy/config">Caddy Config</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/audit">Audit Trail</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/status">Status Page</a>
    </nav>
  </header>

  <!-- Filter Controls Bar -->
  <main class="w-full max-w-container-max mx-auto px-gutter py-6 flex flex-col gap-6">
    <div class="bg-[#1e293b] border border-white/10 rounded-xl p-4 flex flex-col md:flex-row justify-between items-stretch md:items-center gap-4">
      <div class="flex items-center gap-3">
        <span class="material-symbols-outlined text-primary text-xl">insights</span>
        <div>
          <h2 class="text-sm font-bold text-on-surface">Proxy-Level Traffic & Visitor Analytics</h2>
          <p class="text-xs text-outline">Real-time privacy-friendly analytics parsed directly from Caddy reverse-proxy logs</p>
        </div>
      </div>

      <div class="flex flex-wrap items-center gap-3">
        <!-- Domain Filter Selector -->
        <select id="domain-select" onchange="updateAnalytics()" class="bg-[#0f172a] border border-white/10 rounded-lg px-3 py-1.5 text-xs font-mono text-on-surface focus:outline-none focus:border-primary">
          {host_options}
        </select>

        <!-- Time Window Filter Buttons -->
        <div class="flex items-center bg-[#0f172a] border border-white/10 rounded-lg p-0.5 text-xs font-mono">
          <button onclick="setWindow(3600, this)" class="win-btn px-2.5 py-1 rounded transition-colors text-outline hover:text-on-surface cursor-pointer">1h</button>
          <button onclick="setWindow(86400, this)" class="win-btn active px-2.5 py-1 rounded transition-colors bg-primary text-slate-950 font-bold cursor-pointer">24h</button>
          <button onclick="setWindow(604800, this)" class="win-btn px-2.5 py-1 rounded transition-colors text-outline hover:text-on-surface cursor-pointer">7d</button>
          <button onclick="setWindow(2592000, this)" class="win-btn px-2.5 py-1 rounded transition-colors text-outline hover:text-on-surface cursor-pointer">30d</button>
        </div>
      </div>
    </div>

    <!-- KPI Summary Tiles -->
    <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4" id="kpi-container">
      <div class="bg-[#1e293b] border border-white/10 rounded-xl p-4 flex flex-col gap-1">
        <span class="text-outline text-[11px] font-mono uppercase tracking-wider">Total Requests</span>
        <span class="text-2xl font-bold font-mono text-primary" id="kpi-requests">{summary['total_requests']}</span>
        <span class="text-[10px] text-outline">HTTP transactions</span>
      </div>

      <div class="bg-[#1e293b] border border-white/10 rounded-xl p-4 flex flex-col gap-1">
        <span class="text-outline text-[11px] font-mono uppercase tracking-wider">Unique Visitors</span>
        <span class="text-2xl font-bold font-mono text-status-alive" id="kpi-visitors">{summary['unique_visitors']}</span>
        <span class="text-[10px] text-outline">Distinct client IPs</span>
      </div>

      <div class="bg-[#1e293b] border border-white/10 rounded-xl p-4 flex flex-col gap-1">
        <span class="text-outline text-[11px] font-mono uppercase tracking-wider">Bandwidth</span>
        <span class="text-2xl font-bold font-mono text-on-surface" id="kpi-bandwidth">{summary['total_bytes_formatted']}</span>
        <span class="text-[10px] text-outline">Outbound data sent</span>
      </div>

      <div class="bg-[#1e293b] border border-white/10 rounded-xl p-4 flex flex-col gap-1">
        <span class="text-outline text-[11px] font-mono uppercase tracking-wider">Human Traffic</span>
        <span class="text-2xl font-bold font-mono text-sky-400" id="kpi-human">{summary['human_pct']}%</span>
        <span class="text-[10px] text-outline">{summary['human_requests']} human vs {summary['bot_requests']} bot</span>
      </div>

      <div class="bg-[#1e293b] border border-white/10 rounded-xl p-4 flex flex-col gap-1">
        <span class="text-outline text-[11px] font-mono uppercase tracking-wider">Avg Latency</span>
        <span class="text-2xl font-bold font-mono text-on-surface-variant" id="kpi-latency">{summary['avg_latency_ms']}ms</span>
        <span class="text-[10px] text-outline">Upstream duration</span>
      </div>

      <div class="bg-[#1e293b] border border-white/10 rounded-xl p-4 flex flex-col gap-1">
        <span class="text-outline text-[11px] font-mono uppercase tracking-wider">Error Rate</span>
        <span class="text-2xl font-bold font-mono {'text-status-down' if summary['error_rate_pct'] > 5 else 'text-status-alive'}" id="kpi-error">{summary['error_rate_pct']}%</span>
        <span class="text-[10px] text-outline">{summary['errors_4xx']} 4xx / {summary['errors_5xx']} 5xx</span>
      </div>
    </div>

    <!-- Interactive Traffic Timeline Chart -->
    <div class="bg-[#1e293b] border border-white/10 rounded-xl p-5 flex flex-col gap-4">
      <div class="flex justify-between items-center">
        <div>
          <h3 class="text-sm font-bold text-on-surface">Traffic & Visitor Volume Over Time</h3>
          <p class="text-xs text-outline">Hourly request distribution and unique visitor trend</p>
        </div>
        <div class="flex items-center gap-4 text-xs font-mono">
          <div class="flex items-center gap-1.5">
            <span class="w-3 h-3 rounded bg-primary"></span>
            <span class="text-on-surface-variant">Requests</span>
          </div>
          <div class="flex items-center gap-1.5">
            <span class="w-3 h-3 rounded bg-status-alive"></span>
            <span class="text-on-surface-variant">Unique Visitors</span>
          </div>
        </div>
      </div>
      <div id="chart-container" class="w-full overflow-x-auto">
        {svg_chart}
      </div>
    </div>

    <!-- 2-Column Details: Top Paths & Traffic Sources -->
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
      <!-- Top Visited Paths (2 Cols) -->
      <div class="lg:col-span-2 bg-[#1e293b] border border-white/10 rounded-xl p-5 flex flex-col gap-4">
        <h3 class="text-sm font-bold text-on-surface flex items-center gap-2">
          <span class="material-symbols-outlined text-primary text-base">link</span> Top Visited Paths
        </h3>
        <div class="overflow-x-auto max-h-80 overflow-y-auto">
          <table class="w-full text-left border-collapse">
            <thead class="bg-[#0f172a] text-outline text-[10px] font-mono uppercase sticky top-0">
              <tr>
                <th class="p-2">Path / URI</th>
                <th class="p-2 text-right">Requests</th>
                <th class="p-2 text-right">Share</th>
                <th class="p-2 text-right">Avg Latency</th>
                <th class="p-2 text-right">2xx / 4xx / 5xx</th>
              </tr>
            </thead>
            <tbody id="top-paths-body">{path_rows}</tbody>
          </table>
        </div>
      </div>

      <!-- Traffic Sources & Referrers -->
      <div class="bg-[#1e293b] border border-white/10 rounded-xl p-5 flex flex-col gap-4">
        <h3 class="text-sm font-bold text-on-surface flex items-center gap-2">
          <span class="material-symbols-outlined text-sky-400 text-base">share</span> Traffic Sources (Referrers)
        </h3>
        <div class="flex flex-col gap-1 max-h-80 overflow-y-auto" id="referrers-container">
          {ref_rows}
        </div>
      </div>
    </div>

    <!-- 3-Column Technology Breakdown: Browsers, OS & Devices, Bots -->
    <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
      <!-- Browsers -->
      <div class="bg-[#1e293b] border border-white/10 rounded-xl p-5 flex flex-col gap-4">
        <h3 class="text-sm font-bold text-on-surface flex items-center gap-2">
          <span class="material-symbols-outlined text-primary text-base">web</span> Top Browsers
        </h3>
        <div class="flex flex-col gap-3" id="browsers-container">{browser_bars}</div>
      </div>

      <!-- Operating Systems & Devices -->
      <div class="bg-[#1e293b] border border-white/10 rounded-xl p-5 flex flex-col gap-4">
        <h3 class="text-sm font-bold text-on-surface flex items-center gap-2">
          <span class="material-symbols-outlined text-status-alive text-base">devices</span> Operating Systems
        </h3>
        <div class="flex flex-col gap-3" id="os-container">{os_bars}</div>
      </div>

      <!-- Bots & Crawlers -->
      <div class="bg-[#1e293b] border border-white/10 rounded-xl p-5 flex flex-col gap-4">
        <h3 class="text-sm font-bold text-on-surface flex items-center gap-2">
          <span class="material-symbols-outlined text-status-maint text-base">smart_toy</span> Bots & Crawlers
        </h3>
        <div class="flex flex-col gap-2 max-h-60 overflow-y-auto" id="bots-container">
          {"".join(f'<div class="flex justify-between items-center py-1 border-b border-white/5 text-xs font-mono"><span class="text-on-surface">{escape(b["name"])}</span><span class="text-outline">{b["count"]} ({b["pct"]}%)</span></div>' for b in data["bots"]) or '<div class="text-xs text-outline py-2">No bot traffic recorded</div>'}
        </div>
      </div>
    </div>

    <!-- Per-Domain Traffic Leaderboard -->
    <div class="bg-[#1e293b] border border-white/10 rounded-xl p-5 flex flex-col gap-4">
      <h3 class="text-sm font-bold text-on-surface flex items-center gap-2">
        <span class="material-symbols-outlined text-primary text-base">domain</span> Domain Traffic Breakdown
      </h3>
      <div class="overflow-x-auto">
        <table class="w-full text-left border-collapse">
          <thead class="bg-[#0f172a] text-outline text-[10px] font-mono uppercase">
            <tr>
              <th class="p-2.5">Domain / Host</th>
              <th class="p-2.5 text-right">Total Requests</th>
              <th class="p-2.5 text-right">Unique Visitors</th>
              <th class="p-2.5 text-right">Bandwidth</th>
              <th class="p-2.5 text-right">Error Rate</th>
            </tr>
          </thead>
          <tbody id="domains-body">{domain_rows}</tbody>
        </table>
      </div>
    </div>
  </main>
</div>

<footer class="w-full max-w-container-max mx-auto px-gutter py-6 text-center text-xs text-outline font-mono border-t border-white/5 mt-8">
  <span>Caddy Mon • Server-Side Traffic Analytics • Zero Tracking Cookies • Powered by Caddy Reverse-Proxy Logs</span>
</footer>

<script>
  let currentWindow = 86400;

  function setWindow(sec, btn) {{
    currentWindow = sec;
    document.querySelectorAll('.win-btn').forEach(b => {{
      b.classList.remove('bg-primary', 'text-slate-950', 'font-bold', 'active');
      b.classList.add('text-outline');
    }});
    btn.classList.add('bg-primary', 'text-slate-950', 'font-bold', 'active');
    btn.classList.remove('text-outline');
    updateAnalytics();
  }}

  async function updateAnalytics() {{
    const host = document.getElementById('domain-select').value;
    const url = `/api/analytics?window=${{currentWindow}}` + (host ? `&host=${{encodeURIComponent(host)}}` : '');
    try {{
      const res = await fetch(url);
      if (!res.ok) return;
      const data = await res.json();
      renderAnalyticsData(data);
    }} catch (e) {{
      console.error("Failed to fetch analytics:", e);
    }}
  }}

  function escapeHtml(s) {{
    if (!s) return '';
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }}

  function renderAnalyticsData(data) {{
    const s = data.summary;
    document.getElementById('kpi-requests').innerText = s.total_requests;
    document.getElementById('kpi-visitors').innerText = s.unique_visitors;
    document.getElementById('kpi-bandwidth').innerText = s.total_bytes_formatted;
    document.getElementById('kpi-human').innerText = s.human_pct + '%';
    document.getElementById('kpi-latency').innerText = s.avg_latency_ms + 'ms';
    document.getElementById('kpi-error').innerText = s.error_rate_pct + '%';

    // Top paths
    let pathHtml = '';
    (data.top_paths || []).forEach(p => {{
      pathHtml += `
        <tr class="border-b border-white/5 hover:bg-white/5 transition-colors">
          <td class="p-2.5 font-mono text-xs text-on-surface truncate max-w-xs" title="${{escapeHtml(p.path)}}">${{escapeHtml(p.path)}}</td>
          <td class="p-2.5 font-mono text-xs text-right font-bold text-primary">${{p.count}}</td>
          <td class="p-2.5 font-mono text-xs text-right text-outline">${{p.pct}}%</td>
          <td class="p-2.5 font-mono text-xs text-right text-on-surface-variant">${{p.avg_ms}}ms</td>
          <td class="p-2.5 font-mono text-xs text-right">
            <span class="text-status-alive">${{p.status_2xx}}</span> / 
            <span class="text-status-maint">${{p.status_4xx}}</span> / 
            <span class="text-status-down">${{p.status_5xx}}</span>
          </td>
        </tr>`;
    }});
    document.getElementById('top-paths-body').innerHTML = pathHtml || '<tr><td colspan="5" class="p-4 text-center text-outline text-xs">No traffic recorded</td></tr>';

    // Referrers
    let refHtml = '';
    (data.top_referrers || []).forEach(r => {{
      refHtml += `
        <div class="flex items-center justify-between py-1.5 border-b border-white/5 text-xs font-mono">
          <span class="text-on-surface truncate max-w-xs">${{escapeHtml(r.source)}}</span>
          <div class="flex items-center gap-3">
            <span class="text-on-surface-variant">${{r.count}}</span>
            <span class="text-outline text-[11px] w-12 text-right">${{r.pct}}%</span>
          </div>
        </div>`;
    }});
    document.getElementById('referrers-container').innerHTML = refHtml || '<div class="text-xs text-outline py-2">No referrer data</div>';

    // Browsers
    let brHtml = '';
    (data.browsers || []).slice(0, 5).forEach(b => {{
      brHtml += `
        <div class="flex flex-col gap-1 text-xs">
          <div class="flex justify-between font-mono">
            <span class="text-on-surface">${{escapeHtml(b.name)}}</span>
            <span class="text-outline">${{b.count}} (${{b.pct}}%)</span>
          </div>
          <div class="w-full bg-white/5 rounded-full h-1.5 overflow-hidden">
            <div class="bg-primary h-full rounded-full" style="width: ${{b.pct}}%"></div>
          </div>
        </div>`;
    }});
    document.getElementById('browsers-container').innerHTML = brHtml || '<div class="text-xs text-outline py-2">No browser data</div>';

    // Domains
    let domHtml = '';
    (data.domains || []).forEach(d => {{
      domHtml += `
        <tr class="border-b border-white/5 hover:bg-white/5 transition-colors">
          <td class="p-2.5 font-mono text-xs font-bold text-on-surface">${{escapeHtml(d.host)}}</td>
          <td class="p-2.5 font-mono text-xs text-right text-primary font-semibold">${{d.requests}}</td>
          <td class="p-2.5 font-mono text-xs text-right text-status-alive">${{d.unique_visitors}}</td>
          <td class="p-2.5 font-mono text-xs text-right text-on-surface-variant">${{d.bytes_formatted}}</td>
          <td class="p-2.5 font-mono text-xs text-right ${{d.error_pct > 5 ? 'text-status-down' : 'text-outline'}}">${{d.error_pct}}%</td>
        </tr>`;
    }});
    document.getElementById('domains-body').innerHTML = domHtml || '<tr><td colspan="5" class="p-4 text-center text-outline text-xs">No per-domain traffic recorded</td></tr>';
  }}
</script>
</body>
</html>"""
    return HTMLResponse(content=html)


def _generate_traffic_svg(timeline: List[Dict[str, Any]]) -> str:
    """Generate modern SVG bar chart of requests & unique visitors over time."""
    if not timeline:
        return '<div class="p-8 text-center text-outline text-xs font-mono">No timeline points</div>'

    width = 960
    height = 180
    pad_left = 40
    pad_right = 20
    pad_top = 20
    pad_bottom = 30

    chart_w = width - pad_left - pad_right
    chart_h = height - pad_top - pad_bottom

    max_req = max((t["requests"] for t in timeline), default=1)
    if max_req == 0:
        max_req = 10

    n_bars = len(timeline)
    bar_width = max(2, (chart_w / n_bars) - 4)

    bars_svg = []
    labels_svg = []

    # Grid lines
    grid_lines = []
    for step in [0.25, 0.5, 0.75, 1.0]:
        y_val = pad_top + chart_h - (chart_h * step)
        req_val = int(max_req * step)
        grid_lines.append(f'<line x1="{pad_left}" y1="{y_val}" x2="{width-pad_right}" y2="{y_val}" stroke="#334155" stroke-dasharray="3,3" stroke-width="1"/>')
        grid_lines.append(f'<text x="{pad_left-6}" y="{y_val+4}" fill="#64748b" font-size="9" text-anchor="end" font-family="JetBrains Mono, monospace">{req_val}</text>')

    for i, t in enumerate(timeline):
        x = pad_left + (i * (chart_w / n_bars)) + 2
        req_h = (t["requests"] / max_req) * chart_h
        vis_h = (t["visitors"] / max_req) * chart_h

        y_req = pad_top + chart_h - req_h
        y_vis = pad_top + chart_h - vis_h

        # Requests bar (blue)
        bars_svg.append(f'<rect x="{x}" y="{y_req}" width="{bar_width}" height="{max(1, req_h)}" fill="#0ea5e9" rx="2" opacity="0.85"><title>{t["label"]}: {t["requests"]} requests, {t["visitors"]} visitors</title></rect>')
        # Unique visitors overlay bar (green)
        if t["visitors"] > 0:
            bars_svg.append(f'<rect x="{x}" y="{y_vis}" width="{bar_width}" height="{max(1, vis_h)}" fill="#10b981" rx="2" opacity="0.95"><title>{t["label"]}: {t["visitors"]} unique visitors</title></rect>')

        # Labels every N steps
        step_mod = max(1, n_bars // 12)
        if i % step_mod == 0 or i == n_bars - 1:
            labels_svg.append(f'<text x="{x + bar_width/2}" y="{height - 8}" fill="#88929b" font-size="9" text-anchor="middle" font-family="JetBrains Mono, monospace">{escape(t["label"])}</text>')

    return f"""
    <svg viewBox="0 0 {width} {height}" class="w-full h-44 font-sans select-none">
      {''.join(grid_lines)}
      {''.join(bars_svg)}
      {''.join(labels_svg)}
      <line x1="{pad_left}" y1="{pad_top+chart_h}" x2="{width-pad_right}" y2="{pad_top+chart_h}" stroke="#475569" stroke-width="1.5"/>
    </svg>"""
