"""Audit Log & Configuration Snapshots visual page."""

import json
from html import escape
from datetime import datetime
from typing import Dict, Any, List

try:
    from fastapi import Request
    from fastapi.responses import HTMLResponse
except ImportError:
    Request = object  # type: ignore
    HTMLResponse = object  # type: ignore

from .config import TZ
from .db import get_audit_logs, get_config_snapshots


def audit_page(request: Request) -> HTMLResponse:
    """Render modern Tailwind audit log and snapshot manager view."""
    logs = get_audit_logs(limit=50)
    snapshots = get_config_snapshots(limit=15)

    # Render Audit Rows
    audit_rows = ""
    for l in logs:
        ts_str = datetime.fromtimestamp(l["ts"], TZ).strftime("%Y-%m-%d %H:%M:%S")
        action = l["action"]
        if "CREATE" in action:
            badge_cls = "bg-status-alive/15 text-status-alive border-status-alive/30"
        elif "DELETE" in action:
            badge_cls = "bg-status-down/15 text-status-down border-status-down/30"
        elif "ROLLBACK" in action:
            badge_cls = "bg-primary/15 text-primary border-primary/30"
        else:
            badge_cls = "bg-surface-variant text-on-surface-variant border-white/10"

        diff_btn = ""
        if l.get("diff_json"):
            diff_escaped = escape(l["diff_json"])
            diff_btn = f"""
              <button onclick="showDiffModal('{escape(l['action'])}', '{diff_escaped}')" class="text-primary hover:underline text-[11px] font-mono cursor-pointer ml-2">
                view diff
              </button>"""

        audit_rows += f"""
          <tr class="border-b border-white/5 hover:bg-white/[0.02] transition-colors">
            <td class="p-3 text-outline whitespace-nowrap font-mono text-xs">{ts_str}</td>
            <td class="p-3 whitespace-nowrap font-mono text-xs font-semibold text-on-surface">{escape(l['user'])}</td>
            <td class="p-3 whitespace-nowrap">
              <span class="px-2 py-0.5 rounded text-[10px] font-bold uppercase font-mono border {badge_cls}">{escape(action)}</span>
            </td>
            <td class="p-3 whitespace-nowrap font-mono text-xs text-primary">{escape(l.get('host') or '-')}</td>
            <td class="p-3 text-on-surface-variant font-sans text-xs">{escape(l.get('details') or '-')}{diff_btn}</td>
          </tr>"""

    if not audit_rows:
        audit_rows = '<tr><td colspan="5" class="p-8 text-center text-outline font-mono">No administrative audit events recorded yet.</td></tr>'

    # Render Snapshots List
    snap_items = ""
    for s in snapshots:
        ts_str = datetime.fromtimestamp(s["ts"], TZ).strftime("%Y-%m-%d %H:%M:%S")
        snap_items += f"""
          <div class="p-3 rounded-lg bg-[#1e293b] border border-white/5 flex items-center justify-between gap-3">
            <div class="flex flex-col">
              <span class="font-mono text-xs text-on-surface font-semibold">Snapshot #{s['id']}</span>
              <span class="text-outline text-[11px] font-sans">{escape(s['description'])} ({escape(s['user'])})</span>
              <span class="text-outline-variant font-mono text-[10px]">{ts_str}</span>
            </div>
            <button onclick="rollbackSnapshot({s['id']})" class="bg-surface-variant hover:bg-slate-700 text-on-surface border border-white/10 text-xs px-3 py-1 rounded font-semibold transition-colors cursor-pointer" title="Rollback to this snapshot">
              Rollback
            </button>
          </div>"""

    if not snap_items:
        snap_items = '<div class="text-outline font-mono text-xs p-4 text-center">No snapshots saved yet.</div>'

    html = f"""<!doctype html>
<html class="dark" lang="en"><head>
<meta charset="utf-8"/>
<meta content="width=device-width, initial-scale=1.0" name="viewport"/>
<title>Caddy Mon - Audit Trail & Backups</title>
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
<body class="bg-background text-on-surface min-h-screen flex flex-col antialiased">
  <header class="bg-background top-0 flex flex-col gap-2 w-full pt-6 px-gutter max-w-container-max mx-auto border-b-0">
    <div class="flex justify-between items-center w-full">
      <div class="flex items-center gap-3">
        <span class="material-symbols-outlined text-primary text-3xl">history_edu</span>
        <h1 class="text-2xl font-bold text-on-surface tracking-tight font-sans">Audit Trail & Backups</h1>
      </div>
      <div class="flex gap-2">
        <a href="/" class="bg-surface-container hover:bg-slate-700 text-on-surface border border-white/10 text-xs px-3 py-1.5 rounded-lg flex items-center gap-1.5 transition-colors">
          <span class="material-symbols-outlined text-sm">dashboard</span> Dashboard
        </a>
      </div>
    </div>

    <!-- Navigation Bar -->
    <nav class="flex gap-6 mt-4 overflow-x-auto pb-1 border-b border-white/5 text-xs font-bold uppercase tracking-wider font-sans">
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/">Dashboard</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/topology">Topology</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/logs">Logs</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/security">Security</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/tls">TLS</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/caddy/config">Caddy Config</a>
      <a class="text-primary border-b-2 border-primary pb-2 whitespace-nowrap" href="/audit">Audit Trail</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/status">Status Page</a>
    </nav>
  </header>

  <main class="flex-1 w-full max-w-container-max mx-auto px-gutter py-8 flex flex-col lg:flex-row gap-8">
    <!-- Left: Audit Log Table -->
    <section class="flex-1 flex flex-col gap-4">
      <div class="flex justify-between items-center">
        <h2 class="text-lg font-semibold text-on-surface flex items-center gap-2">
          <span>Administrative Changes</span>
          <span class="text-xs font-mono text-outline">({len(logs)} entries)</span>
        </h2>
      </div>
      
      <div class="bg-[#131b2e] border border-white/10 rounded-xl overflow-hidden shadow-xl">
        <table class="w-full text-left border-collapse font-sans text-xs">
          <thead class="bg-[#0f172a] text-outline text-[11px] uppercase font-mono border-b border-white/10">
            <tr>
              <th class="p-3">Time</th>
              <th class="p-3">User</th>
              <th class="p-3">Action</th>
              <th class="p-3">Target Host</th>
              <th class="p-3">Details</th>
            </tr>
          </thead>
          <tbody>
            {audit_rows}
          </tbody>
        </table>
      </div>
    </section>

    <!-- Right: Configuration Snapshots & Rollback -->
    <aside class="w-full lg:w-96 flex flex-col gap-4">
      <h2 class="text-lg font-semibold text-on-surface flex items-center gap-2">
        <span class="material-symbols-outlined text-outline text-lg">restore</span>
        <span>Config Snapshots</span>
      </h2>
      <div class="bg-[#131b2e] border border-white/10 rounded-xl p-4 flex flex-col gap-3">
        <p class="text-xs text-outline font-sans">
          Automatic backups are taken before any route creation, update, or deletion.
        </p>
        <div class="flex flex-col gap-2 max-h-[500px] overflow-y-auto">
          {snap_items}
        </div>
      </div>
    </aside>
  </main>

  <!-- Diff Viewer Modal -->
  <div id="diff-modal" class="fixed inset-0 bg-black/75 backdrop-blur-xs flex items-center justify-center p-4 z-50 hidden">
    <div class="bg-[#131b2e] border border-white/15 rounded-xl w-full max-w-xl max-h-[85vh] flex flex-col overflow-hidden shadow-2xl">
      <div class="p-4 border-b border-white/10 flex justify-between items-center">
        <h3 id="diff-modal-title" class="text-base font-mono font-bold text-on-surface"></h3>
        <button onclick="closeDiffModal()" class="text-outline hover:text-on-surface p-1">✕</button>
      </div>
      <div class="p-4 overflow-y-auto bg-[#0b1326]">
        <pre id="diff-content" class="text-xs font-mono text-primary whitespace-pre-wrap overflow-x-auto"></pre>
      </div>
      <div class="p-3 border-t border-white/10 flex justify-end bg-[#0f172a]">
        <button onclick="closeDiffModal()" class="bg-[#1e293b] hover:bg-slate-700 text-on-surface px-4 py-1.5 rounded text-xs font-semibold">Close</button>
      </div>
    </div>
  </div>

  <script>
    function showDiffModal(title, jsonStr) {{
      try {{
        const parsed = JSON.parse(jsonStr);
        document.getElementById('diff-content').textContent = JSON.stringify(parsed, null, 2);
      }} catch (e) {{
        document.getElementById('diff-content').textContent = jsonStr;
      }}
      document.getElementById('diff-modal-title').textContent = title + ' Diff Payload';
      document.getElementById('diff-modal').classList.remove('hidden');
    }}

    function closeDiffModal() {{
      document.getElementById('diff-modal').classList.add('hidden');
    }}

    async function rollbackSnapshot(id) {{
      if (!confirm('Are you sure you want to rollback Caddy configuration to snapshot #' + id + '? This will reload Caddy in-memory configuration.')) return;
      try {{
        const r = await fetch('/api/caddy/rollback/' + id, {{ method: 'POST' }});
        const res = await r.json();
        if (res.ok) {{
          alert('Rollback successful! Caddy configuration restored.');
          location.reload();
        }} else {{
          alert('Rollback failed: ' + (res.error || 'Unknown error'));
        }}
      }} catch (e) {{
        alert('Error: ' + e);
      }}
    }}
  </script>
</body></html>"""
    return HTMLResponse(html)
