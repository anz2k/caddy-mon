"""caddy-mon — FastAPI entry point.

Wires Caddy/Log sources, SQLite persistent history, SSE real-time streaming,
auth, public status page, security analytics, Caddy control plane, route CRUD, and audit logging.
"""

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, Request, Depends, Body
from fastapi.responses import HTMLResponse, StreamingResponse

from caddy_mon.config import AUTH_USER, AUTH_PASSWORD
from caddy_mon.auth import require_auth
from caddy_mon.db import (
    init_db,
    get_site_uptime_24h,
    get_site_sparkline,
    get_recent_incidents,
    set_maintenance,
    get_all_maintenance,
    get_host_extended_history,
    get_host_incidents,
    get_audit_logs,
    get_config_snapshots,
)
from caddy_mon.log_source import get_host_recent_logs
from caddy_mon.caddy_source import refresh, background_poll_loop, _state
from caddy_mon.sse import sse_event_stream
from caddy_mon.dashboard import dashboard, api_state
from caddy_mon.status_page import status_page, api_status, status_feed_xml
from caddy_mon.diagnostics import probe_host_detailed
from caddy_mon.security_page import security_page, security_analytics
from caddy_mon.caddy_control import caddy_config_page, get_caddy_raw_config, reload_caddy
from caddy_mon.caddy_crud import create_caddy_route, delete_caddy_route, rollback_caddy_config
from caddy_mon.audit_page import audit_page
from caddy_mon.topology import topology, api_topology
from caddy_mon.logs_page import logs_page, api_logs
from caddy_mon.tls_page import tls_page, api_tls
from caddy_mon.analytics_page import analytics, api_analytics, get_traffic_analytics


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start background 24/7 metrics poller on startup and cancel on shutdown."""
    try:
        init_db()
    except Exception as e:
        print(f"[caddy-mon] Warning: init_db in lifespan: {e}")
    bg_task = asyncio.create_task(background_poll_loop())
    yield
    bg_task.cancel()
    try:
        await bg_task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="caddy-mon", lifespan=lifespan)


# --------------------------------------------------------------------------
# Public Routes (No authentication required)
# --------------------------------------------------------------------------

@app.get("/status", response_class=HTMLResponse)
async def status_route(request: Request):
    """Public status page showing overall health, sanitized services, and incidents."""
    return await status_page(request)


@app.get("/api/status")
async def api_status_route():
    """Sanitized public status JSON API."""
    return api_status()


@app.get("/status/feed.xml")
async def status_feed_route():
    """Public RSS 2.0 XML incident feed for subscribers and monitoring tools."""
    return status_feed_xml()


# --------------------------------------------------------------------------
# Protected Administrative Routes (Auth required if configured)
# --------------------------------------------------------------------------

@app.get("/", dependencies=[Depends(require_auth)])
async def index(request: Request):
    return await dashboard(request)


@app.get("/api/state", dependencies=[Depends(require_auth)])
async def api_state_route():
    return await api_state()


@app.get("/api/events", dependencies=[Depends(require_auth)])
async def api_events_route(request: Request):
    """Server-Sent Events stream for zero-flicker real-time dashboard updates."""
    return StreamingResponse(
        sse_event_stream(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/history/{host}", dependencies=[Depends(require_auth)])
async def api_history_route(host: str, hours: int = 24):
    """Return 24h uptime percentage and latency sparkline points for a host."""
    return {
        "host": host,
        "uptime_24h": get_site_uptime_24h(host),
        "sparkline": get_site_sparkline(host, hours=hours),
    }


@app.get("/api/site/{host}/details", dependencies=[Depends(require_auth)])
async def api_site_details_route(host: str):
    """Return comprehensive deep-dive details, history, recent logs, and incidents for a host."""
    await refresh()
    site_obj = None
    all_hosts = [host]
    for s in _state.get("sites", []):
        if s.get("primary_host") == host or host in s.get("hosts", []):
            site_obj = s
            all_hosts = s.get("hosts", [host])
            break

    history_data = get_host_extended_history(host)
    recent_logs = get_host_recent_logs(all_hosts, limit=40)
    incidents = get_host_incidents(host, limit=15)
    maintenance_map = get_all_maintenance()
    traffic_summary = get_traffic_analytics(window=86400, host_filter=host)

    return {
        "host": host,
        "site": site_obj,
        "history": history_data,
        "recent_logs": recent_logs,
        "incidents": incidents,
        "maintenance": maintenance_map.get(host),
        "traffic": traffic_summary,
    }


@app.get("/api/export", dependencies=[Depends(require_auth)])
async def api_export_route():
    """Export complete infrastructure status report as a JSON payload."""
    await refresh()
    return {
        "exported_at": time.time(),
        "state": _state,
        "maintenance": get_all_maintenance(),
        "recent_incidents": get_recent_incidents(limit=50),
    }


# --------------------------------------------------------------------------
# Route CRUD & Dynamic Management Endpoints
# --------------------------------------------------------------------------

@app.post("/api/routes", dependencies=[Depends(require_auth)])
async def api_create_route(
    payload: Dict[str, Any] = Body(...),
):
    """Create a new reverse-proxy route in Caddy with automated backup and audit."""
    primary_host = payload.get("primary_host", "")
    aliases = payload.get("aliases", [])
    upstreams = payload.get("upstreams", [])
    path_prefix = payload.get("path_prefix", "")
    server = payload.get("server", "srv0")

    return await create_caddy_route(
        user=AUTH_USER or "admin",
        primary_host=primary_host,
        aliases=aliases,
        upstreams=upstreams,
        path_prefix=path_prefix,
        server=server,
    )


@app.delete("/api/routes/{host}", dependencies=[Depends(require_auth)])
async def api_delete_route(
    host: str,
    server: str = "srv0",
):
    """Delete a reverse-proxy route from Caddy with automated backup and audit."""
    return await delete_caddy_route(
        user=AUTH_USER or "admin",
        host=host,
        server=server,
    )


@app.get("/audit", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
async def audit_route(request: Request):
    """Visual administrative audit trail and snapshot manager."""
    return audit_page(request)


@app.get("/api/audit", dependencies=[Depends(require_auth)])
async def api_audit_route(limit: int = 50):
    """Retrieve administrative audit trail events."""
    return {
        "audit_logs": get_audit_logs(limit=limit),
        "snapshots": get_config_snapshots(limit=15),
    }


@app.post("/api/caddy/rollback/{snapshot_id}", dependencies=[Depends(require_auth)])
async def api_caddy_rollback_route(snapshot_id: int):
    """Rollback Caddy configuration to a previous saved snapshot."""
    return await rollback_caddy_config(
        user=AUTH_USER or "admin",
        snapshot_id=snapshot_id,
    )


# --------------------------------------------------------------------------
# Diagnostics, Maintenance & Security
# --------------------------------------------------------------------------

@app.get("/api/incidents", dependencies=[Depends(require_auth)])
async def api_incidents_route(limit: int = 20):
    """Return recent incident events (DOWN, RECOVERED, TLS warnings)."""
    return {"incidents": get_recent_incidents(limit=limit)}


@app.post("/api/probe/{host}", dependencies=[Depends(require_auth)])
async def api_probe_host_route(host: str):
    """Execute an immediate on-demand diagnostic probe against a host."""
    return await probe_host_detailed(host)


@app.post("/api/maintenance/{host}", dependencies=[Depends(require_auth)])
async def api_set_maintenance_route(
    host: str,
    payload: Dict[str, Any] = Body(default={"enabled": True, "reason": ""}),
):
    """Enable or disable maintenance mode for a host."""
    enabled = payload.get("enabled", True)
    reason = payload.get("reason", "")
    set_maintenance(host, enabled=enabled, reason=reason)
    return {"host": host, "enabled": enabled, "reason": reason}


@app.get("/api/maintenance", dependencies=[Depends(require_auth)])
async def api_get_maintenance_route():
    """Return all sites currently in maintenance mode."""
    return {"maintenance": get_all_maintenance()}


@app.get("/security", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
async def security_route(request: Request, window: int = 3600):
    """Security and client IP analytics dashboard view."""
    return security_page(request, window=window)


@app.get("/api/security", dependencies=[Depends(require_auth)])
async def api_security_route(window: int = 3600):
    """Security and client IP analytics JSON API."""
    return security_analytics(window=window)


@app.get("/caddy/config", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
async def caddy_config_route(request: Request):
    """Live Caddy active JSON configuration inspector."""
    return await caddy_config_page(request)


@app.get("/api/caddy/config", dependencies=[Depends(require_auth)])
async def api_caddy_config_route():
    """Export active Caddy JSON configuration."""
    return await get_caddy_raw_config()


@app.post("/api/caddy/reload", dependencies=[Depends(require_auth)])
async def api_caddy_reload_route():
    """Trigger zero-downtime configuration reload on Caddy proxy."""
    return await reload_caddy()


@app.get("/topology", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
async def topology_route(request: Request):
    return await topology(request)


@app.get("/api/topology", dependencies=[Depends(require_auth)])
async def api_topology_route():
    return api_topology()


@app.get("/analytics", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
async def analytics_route(request: Request):
    """Proxy-level traffic and visitor analytics dashboard view."""
    return await analytics(request)


@app.get("/api/analytics", dependencies=[Depends(require_auth)])
async def api_analytics_route(window: int = 86400, host: Optional[str] = None):
    """Traffic and visitor analytics JSON API."""
    return api_analytics(window=window, host=host)


@app.get("/logs", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
async def logs_route(request: Request, window: int = 3600):
    return logs_page(request, window=window)


@app.get("/api/logs", dependencies=[Depends(require_auth)])
async def api_logs_route(window: int = 3600):
    return api_logs(window=window)


@app.get("/tls", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
async def tls_route(request: Request):
    return tls_page(request)


@app.get("/api/tls", dependencies=[Depends(require_auth)])
async def api_tls_route():
    return api_tls()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
