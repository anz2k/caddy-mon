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
    # First fetch current configuration to validate
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
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Caddy Config Inspector</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ font-family: system-ui, sans-serif; background:#0f1115; color:#e5e7eb; margin:0; padding:24px; }}
  .header {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:16px; flex-wrap:wrap; gap:12px; }}
  h1 {{ font-size:20px; margin:0; font-weight:700; }}
  .sub {{ color:#9ca3af; font-size:13px; margin-bottom:20px; }}
  a {{ color:#60a5fa; text-decoration:none; }}
  a:hover {{ text-decoration:underline; }}
  .actions {{ display:flex; gap:10px; }}
  .btn {{ background:#1e293b; color:#e2e8f0; border:1px solid #334155; padding:6px 14px; border-radius:6px; font-size:13px; font-weight:600; cursor:pointer; display:inline-flex; align-items:center; gap:6px; }}
  .btn:hover {{ background:#334155; }}
  .btn-primary {{ background:#2563eb; border-color:#1d4ed8; color:#fff; }}
  .btn-primary:hover {{ background:#1d4ed8; }}
  .config-box {{ background:#1a1d24; border:1px solid #2a2d35; border-radius:10px; padding:20px; overflow-x:auto; font-family:ui-monospace, monospace; font-size:12px; line-height:1.6; max-height:75vh; }}
  pre {{ margin:0; white-space:pre-wrap; word-break:break-all; }}
  #toast {{ position:fixed; bottom:20px; right:20px; background:#1e293b; border:1px solid #3b82f6; color:#e2e8f0; padding:10px 16px; border-radius:8px; font-size:13px; display:none; z-index:100; box-shadow:0 4px 12px rgba(0,0,0,0.5); }}
</style></head>
<body>
  <div class="header">
    <h1>Caddy Configuration Inspector</h1>
    <div class="actions">
      <button class="btn btn-primary" onclick="triggerReload()">🔄 Reload Caddy</button>
      <button class="btn" onclick="downloadConfig()">💾 Download JSON</button>
    </div>
  </div>
  <div class="sub">
    Active Caddy JSON configuration via <code>{CADDY_API}/config/</code> · <a href="/">← Back to Dashboard</a>
  </div>
  <div class="config-box">
    <pre id="json-view">{formatted_json}</pre>
  </div>
  <div id="toast"></div>

  <script>
    function showToast(msg, duration = 3500) {{
      const t = document.getElementById('toast');
      t.textContent = msg;
      t.style.display = 'block';
      setTimeout(() => {{ t.style.display = 'none'; }}, duration);
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
      showToast("Sending reload request to Caddy...");
      try {{
        const res = await fetch('/api/caddy/reload', {{ method: 'POST' }});
        const data = await res.json();
        if (data.ok) {{
          showToast("✓ " + data.message);
          setTimeout(() => location.reload(), 1500);
        }} else {{
          showToast("✗ Reload failed: " + (data.error || "Unknown error"));
        }}
      }} catch (e) {{
        showToast("Error connecting to server: " + e);
      }}
    }}
  </script>
</body></html>"""
    return HTMLResponse(html)
