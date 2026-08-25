"""caddy-mon — FastAPI entry point.

Wires the Caddy/Log sources, SQLite history, SSE streaming, and page modules
into a single FastAPI application with background polling.
"""

import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from caddy_mon.db import (
    init_db,
    get_site_uptime_24h,
    get_site_sparkline,
    get_recent_incidents,
)
from caddy_mon.caddy_source import refresh, background_poll_loop
from caddy_mon.sse import sse_event_stream
from caddy_mon.dashboard import dashboard, api_state
from caddy_mon.topology import topology, api_topology
from caddy_mon.logs_page import logs_page, api_logs
from caddy_mon.tls_page import tls_page, api_tls


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start background 24/7 metrics poller on startup and cancel on shutdown."""
    init_db()
    bg_task = asyncio.create_task(background_poll_loop())
    yield
    bg_task.cancel()
    try:
        await bg_task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="caddy-mon", lifespan=lifespan)


@app.get("/")
async def index(request: Request):
    return await dashboard(request)


@app.get("/api/state")
async def api_state_route():
    return await api_state()


@app.get("/api/events")
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


@app.get("/api/history/{host}")
async def api_history_route(host: str, hours: int = 24):
    """Return 24h uptime percentage and latency sparkline points for a host."""
    return {
        "host": host,
        "uptime_24h": get_site_uptime_24h(host),
        "sparkline": get_site_sparkline(host, hours=hours),
    }


@app.get("/api/incidents")
async def api_incidents_route(limit: int = 20):
    """Return recent incident events (DOWN, RECOVERED, TLS warnings)."""
    return {"incidents": get_recent_incidents(limit=limit)}


@app.get("/topology", response_class=HTMLResponse)
async def topology_route(request: Request):
    return await topology(request)


@app.get("/api/topology")
async def api_topology_route():
    return api_topology()


@app.get("/logs", response_class=HTMLResponse)
async def logs_route(request: Request, window: int = 3600):
    return logs_page(request, window=window)


@app.get("/api/logs")
async def api_logs_route(window: int = 3600):
    return api_logs(window=window)


@app.get("/tls", response_class=HTMLResponse)
async def tls_route(request: Request):
    return tls_page(request)


@app.get("/api/tls")
async def api_tls_route():
    return api_tls()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
