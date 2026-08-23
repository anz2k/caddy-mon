"""
caddy-mon — minimal Caddy reverse-proxy visibility.

Runs in the same Docker network as caddy-proxy (caddy_default), so the
DNS name `caddy-proxy` resolves and the admin API (port 2019) is reachable.

Data sources:
  - GET caddy-proxy:2019/config/apps/http/servers/srv0/routes
        -> each route: host matchers + reverse_proxy upstream dial
  - GET caddy-proxy:2019/metrics
        -> caddy_reverse_proxy_upstreams_healthy{upstream="IP:port"} 0/1
  - self-made HTTP GET probe to each upstream -> latency + status

No Prometheus, no Grafana.
"""

import json
import time
import httpx
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

TZ = ZoneInfo("Europe/Tallinn")

CADDY_API = "http://caddy-proxy:2019"
POLL_INTERVAL = 10  # seconds
PROBE_TIMEOUT = 3.0  # seconds, single probe

app = FastAPI()

# In-memory cache (no database needed)
_state = {
    "sites": [],
    "last_update": 0,
    "errors": [],
}


def _get_json(path: str):
    try:
        with httpx.Client(timeout=5.0) as c:
            r = c.get(f"{CADDY_API}{path}")
            r.raise_for_status()
            return r.json()
    except Exception as e:
        return {"_error": str(e)}


def _parse_routes(routes):
    """Return list of {hosts:[...], upstreams:[...]}."""
    out = []
    for r in routes or []:
        hosts = []
        ups = []
        for m in r.get("match", []) or []:
            hosts += m.get("host", [])
        # find reverse_proxy handler (in handle or terminal)
        def walk(node):
            if isinstance(node, dict):
                if node.get("handler") == "reverse_proxy":
                    for u in node.get("upstreams", []):
                        if u.get("dial"):
                            ups.append(u["dial"])
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for x in node:
                    walk(x)
        walk(r.get("handle"))
        walk(r.get("terminal"))
        if hosts and ups:
            out.append({"hosts": hosts, "upstreams": ups})
    return out


def _parse_healthy(metrics_text: str):
    """Caddy /metrics -> {upstream_dial: bool}."""
    result = {}
    for line in (metrics_text or "").splitlines():
        if line.startswith("caddy_reverse_proxy_upstreams_healthy"):
            # caddy_reverse_proxy_upstreams_healthy{upstream="<server-ip>:3000"} 1
            try:
                label_part = line.split("{", 1)[1].split("}", 1)[0]
                val = line.rsplit(" ", 1)[-1].strip()
                # label_part == upstream="<server-ip>:3000"
                key = label_part.split("=", 1)[1].strip('"')
                result[key] = (val == "1")
            except Exception:
                pass
    return result


def _probe(upstream: str):
    """Quick GET probe. Returns (ok, status, ms, error)."""
    # upstream is "IP:port"; assume http
    if upstream.startswith("https://") or upstream.startswith("http://"):
        return (False, 0, 0.0, "scheme_not_supported")
    url = f"http://{upstream}"
    try:
        start = time.monotonic()
        with httpx.Client(timeout=PROBE_TIMEOUT) as c:
            r = c.get(url, headers={"User-Agent": "caddy-mon-probe/0.1"})
        elapsed = (time.monotonic() - start) * 1000.0
        return (True, r.status_code, round(elapsed, 1), None)
    except Exception as e:
        return (False, 0, 0.0, str(e)[:80])


def refresh():
    now = time.time()
    if now - _state["last_update"] < POLL_INTERVAL and _state["sites"]:
        return

    errors = []
    routes = _get_json("/config/apps/http/servers/srv0/routes")
    if "_error" in routes:
        errors.append(f"Caddy admin API unreachable: {routes['_error']}")
        _state["errors"] = errors
        return

    # admin API returns the /routes endpoint as a bare list (not {"routes": [...]})
    if isinstance(routes, list):
        parsed = _parse_routes(routes)
    elif isinstance(routes, dict) and "routes" in routes:
        parsed = _parse_routes(routes["routes"])
    else:
        parsed = []

    # /metrics is Prometheus text (not JSON), fetch it directly
    metrics_text = ""
    try:
        with httpx.Client(timeout=5.0) as c:
            metrics_text = c.get(f"{CADDY_API}/metrics").text
    except Exception as e:
        errors.append(f"metrics: {e}")
    healthy = _parse_healthy(metrics_text)

    sites = []
    for s in parsed:
        up_probes = []
        for up in s["upstreams"]:
            ok, status, ms, err = _probe(up)
            up_probes.append({
                "upstream": up,
                "caddy_healthy": healthy.get(up),  # None = unknown
                "probe_ok": ok,
                "status": status,
                "ms": ms,
                "error": err,
            })
        # Site is alive if every upstream is OK (Caddy healthy=1 or probe succeeds)
        alive = all(
            (u["caddy_healthy"] is True) or (u["probe_ok"] and u["status"] < 500)
            for u in up_probes
        )
        worst_ms = max((u["ms"] for u in up_probes if u["probe_ok"]), default=0.0)
        sites.append({
            "hosts": s["hosts"],
            "primary_host": s["hosts"][0],
            "upstreams": up_probes,
            "alive": alive,
            "latency_ms": worst_ms,
        })

    # ORDERING: fixed = Caddy route order (Caddyfile order).
    # Do not sort by health, otherwise cards jump up/down on every refresh.
    # (if you want dead-first, remove this comment and enable sorting below)
    # sites.sort(key=lambda x: (x["alive"], -x["latency_ms"]))

    _state["sites"] = sites
    _state["last_update"] = now
    _state["errors"] = errors


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    refresh()
    sites = _state["sites"]
    errors = _state["errors"]
    total = len(sites)
    alive = sum(1 for s in sites if s["alive"])

    cards = ""
    for s in sites:
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
            # If Caddy reports healthy=1, a probe failure is a false negative
            # (e.g. Immich doesn't answer plain HTTP) -> don't show it as a failure.
            # If Caddy reports no health (None), the probe is our only signal.
            show_probe_err = (not u["probe_ok"]) and (u["caddy_healthy"] is None)
            probe = f"{u['status']} / {u['ms']}ms" if u["probe_ok"] else (f"probe failed: {u['error']}" if show_probe_err else "Caddy: alive")
            up_html += f"""
              <div class="up">
                <span class="badge" style="background:{bcolor}">{badge}</span>
                <code>{u['upstream']}</code>
                <span class="probe">{probe}</span>
              </div>"""
        hosts = " · ".join(s["hosts"])
        cards += f"""
          <div class="card" style="border-left:6px solid {color}">
            <div class="host">{s['primary_host']}</div>
            <div class="hosts-all">{hosts}</div>
            <div class="status" style="color:{color}">{('ALIVE' if s['alive'] else 'DEAD')} · {s['latency_ms']}ms</div>
            {up_html}
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
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(300px,1fr)); gap:14px; }}
  .card {{ background:#1a1d24; border-radius:10px; padding:14px 16px; }}
  .host {{ font-weight:600; font-size:16px; }}
  .hosts-all {{ color:#9ca3af; font-size:11px; margin-bottom:6px; word-break:break-all; }}
  .status {{ font-weight:700; font-size:14px; margin-bottom:8px; }}
  .up {{ display:flex; align-items:center; gap:8px; font-size:12px; margin-top:6px; flex-wrap:wrap; }}
  .badge {{ color:#fff; padding:2px 6px; border-radius:5px; font-size:11px; white-space:nowrap; }}
  .probe {{ color:#9ca3af; }}
  code {{ color:#cbd5e1; }}
  .err {{ color:#fbbf24; }}
  .count {{ color:#9ca3af; font-size:13px; }}
</style></head>
<body>
  <h1>Caddy Mon</h1>
  <div class="sub">Caddy reverse-proxy live status · {total} sites · {alive} alive · updated {datetime.now(TZ).strftime('%H:%M:%S')}</div>
  {err_html}
  <div class="grid">{cards}</div>
  <script>
    // Auto-refresh every 12s
    setTimeout(() => location.reload(), 12000);
  </script>
</body></html>"""
    return HTMLResponse(html)


@app.get("/api/state")
def api_state():
    refresh()
    return {
        "last_update": _state["last_update"],
        "sites": _state["sites"],
        "errors": _state["errors"],
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
