"""caddy-mon — FastAPI entry point.

Thin wrapper that wires the Caddy/Log sources and page modules into a single
FastAPI app. Real logic lives in the `caddy_mon` package.
"""

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from caddy_mon.caddy_source import refresh
from caddy_mon.dashboard import dashboard, api_state
from caddy_mon.topology import topology, api_topology
from caddy_mon.logs_page import logs_page, api_logs
from caddy_mon.tls_page import tls_page, api_tls

app = FastAPI()


@app.get("/")
def index(request: Request):
    return dashboard(request)


@app.get("/api/state")
def api_state_route():
    return api_state()


@app.get("/topology", response_class=HTMLResponse)
def topology_route(request: Request):
    return topology(request)


@app.get("/api/topology")
def api_topology_route():
    return api_topology()


@app.get("/logs", response_class=HTMLResponse)
def logs_route(request: Request, window: int = 3600):
    return logs_page(request, window=window)


@app.get("/api/logs")
def api_logs_route(window: int = 3600):
    return api_logs(window=window)


@app.get("/tls", response_class=HTMLResponse)
def tls_route(request: Request):
    return tls_page(request)


@app.get("/api/tls")
def api_tls_route():
    return api_tls()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
