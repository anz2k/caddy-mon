"""Interactive Caddy Control Plane: active configuration viewer and zero-downtime reloader."""

import json
import httpx
from typing import Dict, Any

try:
    from fastapi import Request
    from fastapi.responses import HTMLResponse
except ImportError:
    Request = Any  # type: ignore
    HTMLResponse = Any  # type: ignore

from .config import CADDY_API


async def get_caddy_raw_config() -> Dict[str, Any]:
    """Fetch active configuration JSON directly from Caddy Admin API."""
    url = f"{CADDY_API}/config/"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.json()
            return {"error": f"Caddy returned HTTP {resp.status_code}", "detail": resp.text}
    except Exception as e:
        return {"error": "Failed to connect to Caddy Admin API", "detail": str(e)}


async def reload_caddy() -> Dict[str, Any]:
    """Trigger zero-downtime configuration reload on Caddy proxy."""
    current_config = await get_caddy_raw_config()
    if "error" in current_config:
        return {"ok": False, "error": f"Cannot read config: {current_config.get('detail')}"}

    url = f"{CADDY_API}/load"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url,
                headers={"Content-Type": "application/json"},
                json=current_config,
            )
            if resp.status_code in (200, 204):
                return {"ok": True, "message": "Caddy configuration reloaded successfully"}
            return {"ok": False, "error": f"Reload failed (HTTP {resp.status_code}): {resp.text}"}
    except Exception as e:
        return {"ok": False, "error": f"Connection error during reload: {str(e)}"}


async def caddy_config_page(request: Request) -> HTMLResponse:
    """Render HTML page with live syntax-formatted Caddy JSON configuration."""
    config_data = await get_caddy_raw_config()
    formatted_json = json.dumps(config_data, indent=2)

    html = f"""<!doctype html>
<html class="dark" lang="en"><head>
<meta charset="utf-8"/>
<meta content="width=device-width, initial-scale=1.0" name="viewport"/>
<title>Caddy Mon - Config Inspector</title>
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
  .no-scrollbar::-webkit-scrollbar {{ display: none; }}
  .no-scrollbar {{ -ms-overflow-style: none; scrollbar-width: none; }}
</style>
</head>
<body class="bg-background text-on-surface min-h-screen flex flex-col antialiased">
  <header class="bg-background docked full-width top-0 flex flex-col gap-2 w-full pt-6 px-gutter max-w-container-max mx-auto">
    <div class="flex justify-between items-center w-full flex-wrap gap-3">
      <h1 class="text-2xl font-bold text-on-surface tracking-tight font-sans">Caddy Mon</h1>
      <div class="flex items-center gap-3">
        <button onclick="triggerReload()" class="bg-primary hover:bg-sky-400 text-slate-950 font-bold px-3 py-1.5 rounded-lg text-xs flex items-center gap-1.5 transition-colors cursor-pointer font-sans shadow-lg shadow-primary/20">
          <span class="material-symbols-outlined text-[16px]">sync</span> Reload Caddy
        </button>
        <button onclick="downloadConfig()" class="bg-[#1e293b] hover:bg-slate-700 text-on-surface border border-white/10 font-semibold px-3 py-1.5 rounded-lg text-xs flex items-center gap-1.5 transition-colors cursor-pointer font-sans">
          <span class="material-symbols-outlined text-[16px]">download</span> Download JSON
        </button>
      </div>
    </div>

    <!-- Navigation Bar -->
    <nav class="flex gap-6 mt-2 overflow-x-auto pb-1 no-scrollbar border-b border-white/5 text-xs font-bold uppercase tracking-wider font-sans">
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/">Dashboard</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/topology">Topology</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/logs">Logs</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/security">Security</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/tls">TLS</a>
      <a class="text-primary border-b-2 border-primary pb-2 whitespace-nowrap" href="/caddy/config">Caddy Config</a>
      <a class="text-on-surface-variant hover:text-primary transition-colors pb-2 whitespace-nowrap" href="/status">Status Page</a>
    </nav>
  </header>

  <main class="flex-1 w-full max-w-container-max mx-auto px-gutter py-8 flex flex-col gap-4">
    <div class="flex items-center justify-between text-xs font-mono text-outline">
      <span>Active Caddy JSON Configuration: <code>{CADDY_API}/config/</code></span>
    </div>
    <div class="bg-[#1e293b] border border-white/10 rounded-lg p-5 overflow-x-auto max-h-[75vh]">
      <pre id="json-view" class="font-mono text-xs text-on-surface leading-relaxed">{formatted_json}</pre>
    </div>
  </main>

  <div id="toast" class="fixed bottom-5 right-5 bg-surface-container border border-primary/40 text-on-surface px-4 py-2.5 rounded-lg text-sm shadow-xl hidden z-50 font-sans flex items-center gap-2"></div>

  <script>
    function showToast(msg, duration = 3500) {{
      const t = document.getElementById('toast');
      t.innerHTML = msg;
      t.classList.remove('hidden');
      setTimeout(() => {{ t.classList.add('hidden'); }}, duration);
    }}

    function downloadConfig() {{
      const content = document.getElementById('json-view').textContent;
      const blob = new Blob([content], {{ type: 'application/json' }});
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'caddy-config-' + new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-') + '.json';
      a.click();
      URL.revokeObjectURL(url);
    }}

    async function triggerReload() {{
      if (!confirm("Are you sure you want to reload Caddy proxy configuration?")) return;
      showToast('<span class="material-symbols-outlined text-primary text-base">hourglass_top</span> Sending reload request to Caddy...');
      try {{
        const res = await fetch('/api/caddy/reload', {{ method: 'POST' }});
        const data = await res.json();
        if (data.ok) {{
          showToast('<span class="material-symbols-outlined text-status-alive text-base">check_circle</span> ' + data.message);
          setTimeout(() => location.reload(), 1500);
        }} else {{
          showToast('<span class="material-symbols-outlined text-status-down text-base">error</span> Reload failed: ' + (data.error || "Unknown error"));
        }}
      }} catch (e) {{
        showToast('<span class="material-symbols-outlined text-status-down text-base">error</span> Error connecting to server: ' + e);
      }}
    }}
  </script>
</body></html>"""
    return HTMLResponse(html)
