"""Caddy Reverse-Proxy CRUD Engine.

Handles dynamic creation, modification, deletion, validation, snapshots,
and rollback of reverse-proxy routes via the Caddy Admin API.
"""

import re
import json
import httpx
from typing import List, Dict, Any, Optional, Tuple

from .config import CADDY_API
from .db import record_audit, save_config_snapshot, get_config_snapshot
from .caddy_control import get_caddy_raw_config


# Regex for domain name / FQDN validation
HOST_REGEX = re.compile(
    r"^(?=.{1,253}$)(?!-)[a-zA-Z0-9-]{1,63}(?<!-)(\.[a-zA-Z0-9-]{1,63})*$"
)

# Regex for upstream dial validation (e.g. '192.168.1.50:8080', 'backend:3000', 'localhost:8000')
DIAL_REGEX = re.compile(
    r"^([a-zA-Z0-9\.\-_]+):([0-9]{1,5})$"
)


def validate_route_input(
    primary_host: str,
    aliases: Optional[List[str]] = None,
    upstreams: Optional[List[str]] = None,
    path_prefix: str = "",
) -> Tuple[bool, str]:
    """Validate user-supplied domain names, aliases, upstreams, and path prefixes.

    Returns (is_valid, error_message).
    """
    primary = (primary_host or "").strip().lower()
    if not primary:
        return False, "Primary hostname is required."

    if not HOST_REGEX.match(primary):
        return False, f"Invalid primary hostname: '{primary}'. Must be a valid domain or hostname."

    clean_aliases = []
    if aliases:
        for a in aliases:
            a_clean = (a or "").strip().lower()
            if not a_clean:
                continue
            if not HOST_REGEX.match(a_clean):
                return False, f"Invalid alias hostname: '{a_clean}'."
            if a_clean == primary:
                continue
            clean_aliases.append(a_clean)

    if not upstreams:
        return False, "At least one upstream endpoint (e.g. '192.168.1.100:8080') is required."

    for u in upstreams:
        u_clean = (u or "").strip()
        if not u_clean:
            continue
        m = DIAL_REGEX.match(u_clean)
        if not m:
            return False, f"Invalid upstream endpoint '{u_clean}'. Expected 'host:port' or 'ip:port'."
        port = int(m.group(2))
        if port < 1 or port > 65535:
            return False, f"Invalid port number {port} in upstream '{u_clean}'."

    if path_prefix:
        p_clean = path_prefix.strip()
        if not p_clean.startswith("/"):
            return False, f"Path prefix '{p_clean}' must start with '/'."

    return True, ""


async def _fetch_current_config() -> Optional[Dict[str, Any]]:
    """Fetch current raw Caddy JSON configuration."""
    raw = await get_caddy_raw_config()
    if raw.get("ok"):
        return raw.get("config")
    return None


async def create_caddy_route(
    user: str,
    primary_host: str,
    aliases: Optional[List[str]] = None,
    upstreams: Optional[List[str]] = None,
    path_prefix: str = "",
    server: str = "srv0",
) -> Dict[str, Any]:
    """Create a new reverse-proxy route in Caddy.

    Validates inputs, takes an automatic snapshot, appends the route, and records an audit log.
    """
    valid, err_msg = validate_route_input(primary_host, aliases, upstreams, path_prefix)
    if not valid:
        return {"ok": False, "error": err_msg}

    primary = primary_host.strip().lower()
    clean_aliases = [a.strip().lower() for a in (aliases or []) if a.strip() and a.strip().lower() != primary]
    clean_upstreams = [u.strip() for u in (upstreams or []) if u.strip()]

    current_cfg = await _fetch_current_config()
    if not current_cfg:
        return {"ok": False, "error": "Could not connect to Caddy Admin API to read current configuration."}

    # Backup current config before modifying
    save_config_snapshot(
        user=user,
        description=f"Auto-backup before creating route for {primary}",
        config_json=json.dumps(current_cfg),
    )

    # Build match object
    match_hosts = [primary] + clean_aliases
    match_item: Dict[str, Any] = {"host": match_hosts}
    if path_prefix:
        p = path_prefix.strip()
        match_item["path"] = [p if p.endswith("*") else f"{p.rstrip('/')}/*", p.rstrip("/")]

    route_id = f"route_{primary.replace('.', '_').replace('-', '_')}"
    new_route = {
        "@id": route_id,
        "match": [match_item],
        "handle": [
            {
                "handler": "reverse_proxy",
                "upstreams": [{"dial": dial} for dial in clean_upstreams],
            }
        ],
        "terminal": True,
    }

    # Insert into Caddy configuration
    url = f"{CADDY_API}/config/apps/http/servers/{server}/routes"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(url, json=new_route)
            if resp.status_code not in (200, 201, 204):
                return {
                    "ok": False,
                    "error": f"Caddy Admin API returned {resp.status_code}: {resp.text}",
                }
    except Exception as e:
        return {"ok": False, "error": f"Failed to connect to Caddy Admin API: {e}"}

    record_audit(
        user=user,
        action="CREATE_ROUTE",
        host=primary,
        details=f"Created route for {primary} (aliases: {clean_aliases}) pointing to {clean_upstreams}",
        diff_json=json.dumps(new_route),
    )

    return {"ok": True, "route_id": route_id, "host": primary}


async def delete_caddy_route(
    user: str,
    host: str,
    server: str = "srv0",
) -> Dict[str, Any]:
    """Delete an existing reverse-proxy route matching a host from Caddy."""
    target_host = host.strip().lower()
    current_cfg = await _fetch_current_config()
    if not current_cfg:
        return {"ok": False, "error": "Could not connect to Caddy Admin API."}

    # Locate server routes
    servers = current_cfg.get("apps", {}).get("http", {}).get("servers", {})
    srv_obj = servers.get(server)
    if not srv_obj and servers:
        # Fallback to first available server if srv0 not present
        server = list(servers.keys())[0]
        srv_obj = servers.get(server)

    if not srv_obj or "routes" not in srv_obj:
        return {"ok": False, "error": f"No routes found in Caddy server '{server}'."}

    routes = srv_obj.get("routes", [])
    found_idx = None
    deleted_route = None

    for idx, r in enumerate(routes):
        for m in r.get("match", []):
            if target_host in [h.lower() for h in m.get("host", [])]:
                found_idx = idx
                deleted_route = r
                break
        if found_idx is not None:
            break

    if found_idx is None:
        return {"ok": False, "error": f"Route for host '{target_host}' not found in Caddy."}

    # Backup current config
    save_config_snapshot(
        user=user,
        description=f"Auto-backup before deleting route for {target_host}",
        config_json=json.dumps(current_cfg),
    )

    # Delete route by index
    url = f"{CADDY_API}/config/apps/http/servers/{server}/routes/{found_idx}"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.delete(url)
            if resp.status_code not in (200, 204):
                return {
                    "ok": False,
                    "error": f"Caddy Admin API returned {resp.status_code}: {resp.text}",
                }
    except Exception as e:
        return {"ok": False, "error": f"Failed to connect to Caddy Admin API: {e}"}

    record_audit(
        user=user,
        action="DELETE_ROUTE",
        host=target_host,
        details=f"Deleted route for {target_host}",
        diff_json=json.dumps(deleted_route) if deleted_route else None,
    )

    return {"ok": True, "host": target_host}


async def rollback_caddy_config(
    user: str,
    snapshot_id: int,
) -> Dict[str, Any]:
    """Restore a previous known-good Caddy configuration snapshot."""
    snap = get_config_snapshot(snapshot_id)
    if not snap:
        return {"ok": False, "error": f"Snapshot #{snapshot_id} not found."}

    current_cfg = await _fetch_current_config()
    if current_cfg:
        save_config_snapshot(
            user=user,
            description=f"Auto-backup before rollback to #{snapshot_id}",
            config_json=json.dumps(current_cfg),
        )

    try:
        config_data = json.loads(snap["config_json"])
    except Exception as e:
        return {"ok": False, "error": f"Invalid snapshot JSON data: {e}"}

    url = f"{CADDY_API}/load"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=config_data)
            if resp.status_code not in (200, 204):
                return {
                    "ok": False,
                    "error": f"Caddy Admin API load returned {resp.status_code}: {resp.text}",
                }
    except Exception as e:
        return {"ok": False, "error": f"Failed to connect to Caddy Admin API: {e}"}

    record_audit(
        user=user,
        action="ROLLBACK_CONFIG",
        host="",
        details=f"Rolled back Caddy configuration to snapshot #{snapshot_id} ({snap.get('description')})",
    )

    return {"ok": True, "snapshot_id": snapshot_id}
