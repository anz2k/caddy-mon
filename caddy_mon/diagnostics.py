"""On-demand diagnostic probing for Caddy upstream endpoints."""

import time
import httpx
from typing import Dict, Any, List, Optional

from .caddy_source import _state, _probe_async
from .config import PROBE_TIMEOUT


async def probe_host_detailed(host: str) -> Dict[str, Any]:
    """Execute an immediate, detailed diagnostic probe against a host's upstreams."""
    # Find matching site from current state
    target_site = None
    for s in _state.get("sites", []):
        if host in s.get("hosts", []) or host == s.get("primary_host"):
            target_site = s
            break

    if not target_site:
        # If not found in cache, attempt direct upstream probe if host is formatted as dial
        upstreams_to_probe = [host]
    else:
        upstreams_to_probe = [
            u.get("upstream") if isinstance(u, dict) else u
            for u in target_site.get("upstreams", [])
        ]

    diagnostic_results = []
    for up in upstreams_to_probe:
        res = await _run_detailed_probe(up)
        diagnostic_results.append(res)

    overall_ok = any(r["ok"] for r in diagnostic_results) if diagnostic_results else False
    worst_ms = max((r["latency_ms"] for r in diagnostic_results if r["ok"]), default=0.0)

    return {
        "host": host,
        "timestamp": time.time(),
        "ok": overall_ok,
        "latency_ms": worst_ms,
        "upstreams": diagnostic_results,
    }


async def _run_detailed_probe(upstream: str) -> Dict[str, Any]:
    """Perform a deep HTTP GET probe capturing latency and response metadata."""
    if upstream.startswith("https://") or upstream.startswith("http://"):
        url = upstream
    else:
        url = f"http://{upstream}"

    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT, follow_redirects=False) as client:
            resp = await client.get(url, headers={"User-Agent": "caddy-mon-diag/1.0"})
            elapsed_ms = round((time.monotonic() - start) * 1000.0, 1)

            # Capture useful response headers
            headers = {
                "server": resp.headers.get("server", ""),
                "content-type": resp.headers.get("content-type", ""),
                "content-length": resp.headers.get("content-length", ""),
                "location": resp.headers.get("location", ""),
            }

            return {
                "upstream": upstream,
                "ok": resp.status_code < 500,
                "status_code": resp.status_code,
                "latency_ms": elapsed_ms,
                "headers": {k: v for k, v in headers.items() if v},
                "error": None,
            }
    except httpx.ConnectError as e:
        elapsed_ms = round((time.monotonic() - start) * 1000.0, 1)
        return {
            "upstream": upstream,
            "ok": False,
            "status_code": 0,
            "latency_ms": elapsed_ms,
            "headers": {},
            "error": "Connection refused / Host unreachable",
        }
    except httpx.TimeoutException:
        elapsed_ms = round((time.monotonic() - start) * 1000.0, 1)
        return {
            "upstream": upstream,
            "ok": False,
            "status_code": 0,
            "latency_ms": elapsed_ms,
            "headers": {},
            "error": f"Probe timed out after {PROBE_TIMEOUT}s",
        }
    except Exception as e:
        elapsed_ms = round((time.monotonic() - start) * 1000.0, 1)
        return {
            "upstream": upstream,
            "ok": False,
            "status_code": 0,
            "latency_ms": elapsed_ms,
            "headers": {},
            "error": str(e)[:100],
        }
