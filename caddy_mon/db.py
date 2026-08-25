"""SQLite database layer for historical uptime, latency sparklines, and incident tracking."""

import os
import sqlite3
import time
from typing import Optional, List, Dict, Any

from .config import DB_PATH, HISTORY_RETENTION_DAYS


def get_connection(path: Optional[str] = None) -> sqlite3.Connection:
    """Return a configured SQLite connection with automatic fallback if unwritable."""
    target = path or DB_PATH

    try:
        parent = os.path.dirname(os.path.abspath(target))
        if parent and not os.path.exists(parent):
            os.makedirs(parent, exist_ok=True)
        conn = sqlite3.connect(target, timeout=10.0)
    except (sqlite3.OperationalError, PermissionError, OSError):
        # Fallback to /tmp which is always writable
        fallback = "/tmp/caddy_mon.db"
        if target != fallback:
            print(
                f"[caddy-mon] Warning: Database path '{target}' is not writable "
                f"(volume permission issue). Falling back to '{fallback}'."
            )
            try:
                conn = sqlite3.connect(fallback, timeout=10.0)
            except Exception:
                conn = sqlite3.connect(":memory:", timeout=10.0)
        else:
            conn = sqlite3.connect(":memory:", timeout=10.0)

    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
    except sqlite3.Error:
        pass
    return conn


def init_db():
    """Create tables and indices if they do not exist."""
    try:
        with get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS site_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    host TEXT NOT NULL,
                    alive INTEGER NOT NULL,
                    latency_ms REAL NOT NULL,
                    status INTEGER
                );
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_snapshots_host_ts
                ON site_snapshots(host, ts);
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_snapshots_ts
                ON site_snapshots(ts);
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS incident_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    host TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    details TEXT,
                    resolved_ts REAL
                );
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_incidents_ts
                ON incident_events(ts);
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS alert_state (
                    host TEXT PRIMARY KEY,
                    last_state INTEGER NOT NULL,
                    last_alert_ts REAL NOT NULL
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS site_maintenance (
                    host TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL,
                    reason TEXT,
                    started_at REAL NOT NULL
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    user TEXT NOT NULL,
                    action TEXT NOT NULL,
                    host TEXT,
                    details TEXT,
                    diff_json TEXT
                );
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_ts
                ON audit_log(ts);
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS config_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    user TEXT NOT NULL,
                    description TEXT,
                    config_json TEXT NOT NULL
                );
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_snapshots_cfg_ts
                ON config_snapshots(ts);
            """)
            conn.commit()
    except Exception as e:
        print(f"[caddy-mon] Warning: init_db encountered error: {e}")


def record_snapshot(sites: List[Dict[str, Any]], now: Optional[float] = None):
    """Insert current health and latency snapshots for all sites."""
    if not sites:
        return
    ts = now or time.time()
    rows = []
    for s in sites:
        primary = s.get("primary_host") or (s.get("hosts") or [""])[0]
        if not primary:
            continue
        alive = 1 if s.get("alive") else 0
        latency = float(s.get("latency_ms", 0.0))
        # Get status from worst or first upstream probe if available
        status = None
        upstreams = s.get("upstreams") or []
        for u in upstreams:
            if isinstance(u, dict) and u.get("status"):
                status = u.get("status")
                break
        rows.append((ts, primary, alive, latency, status))

    if not rows:
        return

    try:
        with get_connection() as conn:
            conn.executemany(
                "INSERT INTO site_snapshots (ts, host, alive, latency_ms, status) VALUES (?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()
    except sqlite3.Error:
        pass


def get_site_uptime_24h(host: str, now: Optional[float] = None) -> Optional[float]:
    """Calculate uptime percentage over the last 24 hours (0.0 to 100.0).

    Returns None if no snapshots exist in the 24h window.
    """
    ts_now = now or time.time()
    cutoff = ts_now - 86400.0
    try:
        with get_connection() as conn:
            cur = conn.execute(
                """
                SELECT COUNT(*) AS total, SUM(alive) AS alive_count
                FROM site_snapshots
                WHERE host = ? AND ts >= ?
                """,
                (host, cutoff),
            )
            row = cur.fetchone()
            if not row or row["total"] == 0:
                return None
            total = row["total"]
            alive_count = row["alive_count"] or 0
            return round((alive_count / total) * 100.0, 1)
    except sqlite3.Error:
        return None


def get_site_sparkline(
    host: str,
    hours: int = 24,
    points: int = 12,
    now: Optional[float] = None,
) -> List[float]:
    """Return an array of average latency data points for SVG sparkline visualization.

    Divides the time window (default 24h) into `points` equal buckets.
    Dead or missing buckets return 0.0.
    """
    ts_now = now or time.time()
    window_secs = hours * 3600.0
    cutoff = ts_now - window_secs
    bucket_size = window_secs / points

    try:
        with get_connection() as conn:
            cur = conn.execute(
                """
                SELECT ts, latency_ms, alive
                FROM site_snapshots
                WHERE host = ? AND ts >= ?
                ORDER BY ts ASC
                """,
                (host, cutoff),
            )
            records = cur.fetchall()
    except sqlite3.Error:
        return [0.0] * points

    if not records:
        return [0.0] * points

    # Aggregate into buckets
    buckets = [[] for _ in range(points)]
    for r in records:
        idx = int((r["ts"] - cutoff) / bucket_size)
        if 0 <= idx < points:
            # If alive, record latency; if dead, 0.0
            val = float(r["latency_ms"]) if r["alive"] else 0.0
            buckets[idx].append(val)

    sparkline = []
    for b in buckets:
        if b:
            sparkline.append(round(sum(b) / len(b), 1))
        else:
            sparkline.append(0.0)

    return sparkline


def record_incident(
    host: str,
    event_type: str,
    details: str,
    ts: Optional[float] = None,
) -> int:
    """Record an incident event (e.g. 'DOWN', 'RECOVERED', 'TLS_EXPIRING')."""
    now = ts or time.time()
    try:
        with get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO incident_events (ts, host, event_type, details)
                VALUES (?, ?, ?, ?)
                """,
                (now, host, event_type, details),
            )
            conn.commit()
            return cur.lastrowid or 0
    except sqlite3.Error:
        return 0


def get_recent_incidents(limit: int = 20) -> List[Dict[str, Any]]:
    """Fetch recent incident events."""
    try:
        with get_connection() as conn:
            cur = conn.execute(
                """
                SELECT id, ts, host, event_type, details, resolved_ts
                FROM incident_events
                ORDER BY ts DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [
                {
                    "id": r["id"],
                    "ts": r["ts"],
                    "host": r["host"],
                    "event_type": r["event_type"],
                    "details": r["details"],
                    "resolved_ts": r["resolved_ts"],
                }
                for r in cur.fetchall()
            ]
    except sqlite3.Error:
        return []


def prune_old_history(days: int = HISTORY_RETENTION_DAYS, now: Optional[float] = None):
    """Delete snapshot records older than `days` to keep database size bounded."""
    ts_now = now or time.time()
    cutoff = ts_now - (days * 86400.0)
    try:
        with get_connection() as conn:
            conn.execute("DELETE FROM site_snapshots WHERE ts < ?", (cutoff,))
            conn.commit()
    except sqlite3.Error:
        pass


def set_maintenance(
    host: str,
    enabled: bool,
    reason: str = "",
    now: Optional[float] = None,
):
    """Set or toggle maintenance mode status for a host."""
    ts = now or time.time()
    try:
        with get_connection() as conn:
            if enabled:
                conn.execute(
                    """
                    INSERT INTO site_maintenance (host, enabled, reason, started_at)
                    VALUES (?, 1, ?, ?)
                    ON CONFLICT(host) DO UPDATE SET
                        enabled = 1,
                        reason = excluded.reason,
                        started_at = excluded.started_at
                    """,
                    (host, reason, ts),
                )
            else:
                conn.execute("DELETE FROM site_maintenance WHERE host = ?", (host,))
            conn.commit()
    except sqlite3.Error:
        pass


def get_maintenance_status(host: str) -> Optional[Dict[str, Any]]:
    """Return active maintenance info for a host, or None if not under maintenance."""
    try:
        with get_connection() as conn:
            cur = conn.execute(
                "SELECT host, enabled, reason, started_at FROM site_maintenance WHERE host = ? AND enabled = 1",
                (host,),
            )
            row = cur.fetchone()
            if row:
                return {
                    "host": row["host"],
                    "enabled": bool(row["enabled"]),
                    "reason": row["reason"],
                    "started_at": row["started_at"],
                }
    except sqlite3.Error:
        pass
    return None


def get_all_maintenance() -> Dict[str, Dict[str, Any]]:
    """Return a dictionary of all hosts currently under active maintenance."""
    try:
        with get_connection() as conn:
            cur = conn.execute(
                "SELECT host, enabled, reason, started_at FROM site_maintenance WHERE enabled = 1"
            )
            return {
                r["host"]: {
                    "enabled": True,
                    "reason": r["reason"],
                    "started_at": r["started_at"],
                }
                for r in cur.fetchall()
            }
    except sqlite3.Error:
        return {}


def get_host_incidents(host: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Return past incidents specific to a single host."""
    try:
        with get_connection() as conn:
            cur = conn.execute(
                """
                SELECT id, ts, host, event_type, details, resolved_ts
                FROM incident_events
                WHERE host = ?
                ORDER BY ts DESC
                LIMIT ?
                """,
                (host, limit),
            )
            return [
                {
                    "id": r["id"],
                    "ts": r["ts"],
                    "host": r["host"],
                    "event_type": r["event_type"],
                    "details": r["details"],
                    "resolved_ts": r["resolved_ts"],
                }
                for r in cur.fetchall()
            ]
    except sqlite3.Error:
        return []


def get_host_extended_history(host: str, now: Optional[float] = None) -> Dict[str, Any]:
    """Return detailed 24h & 7d latency statistics and min/avg/max metrics."""
    ts_now = now or time.time()
    cutoff_24h = ts_now - 86400.0

    try:
        with get_connection() as conn:
            cur = conn.execute(
                """
                SELECT latency_ms, alive
                FROM site_snapshots
                WHERE host = ? AND ts >= ? AND alive = 1
                ORDER BY ts ASC
                """,
                (host, cutoff_24h),
            )
            latencies = [float(r["latency_ms"]) for r in cur.fetchall() if r["latency_ms"] is not None]
    except sqlite3.Error:
        latencies = []

    min_latency = round(min(latencies), 1) if latencies else None
    max_latency = round(max(latencies), 1) if latencies else None
    avg_latency = round(sum(latencies) / len(latencies), 1) if latencies else None

    return {
        "host": host,
        "uptime_24h": get_site_uptime_24h(host, now=ts_now),
        "sparkline_24h": get_site_sparkline(host, hours=24, points=24, now=ts_now),
        "sparkline_7d": get_site_sparkline(host, hours=168, points=28, now=ts_now),
        "min_latency_ms": min_latency,
        "max_latency_ms": max_latency,
        "avg_latency_ms": avg_latency,
        "sample_count": len(latencies),
    }


def record_audit(
    user: str,
    action: str,
    host: str = "",
    details: str = "",
    diff_json: Optional[str] = None,
    ts: Optional[float] = None,
) -> int:
    """Record an administrative audit log event (e.g. 'CREATE_ROUTE', 'DELETE_ROUTE')."""
    now = ts or time.time()
    try:
        with get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO audit_log (ts, user, action, host, details, diff_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (now, user, action, host, details, diff_json),
            )
            conn.commit()
            return cur.lastrowid or 0
    except sqlite3.Error:
        return 0


def get_audit_logs(limit: int = 50) -> List[Dict[str, Any]]:
    """Fetch recent administrative audit log events."""
    try:
        with get_connection() as conn:
            cur = conn.execute(
                """
                SELECT id, ts, user, action, host, details, diff_json
                FROM audit_log
                ORDER BY ts DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [
                {
                    "id": r["id"],
                    "ts": r["ts"],
                    "user": r["user"],
                    "action": r["action"],
                    "host": r["host"],
                    "details": r["details"],
                    "diff_json": r["diff_json"],
                }
                for r in cur.fetchall()
            ]
    except sqlite3.Error:
        return []


def save_config_snapshot(
    user: str,
    description: str,
    config_json: str,
    ts: Optional[float] = None,
) -> int:
    """Save a snapshot of Caddy JSON configuration before making changes."""
    now = ts or time.time()
    try:
        with get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO config_snapshots (ts, user, description, config_json)
                VALUES (?, ?, ?, ?)
                """,
                (now, user, description, config_json),
            )
            conn.commit()
            return cur.lastrowid or 0
    except sqlite3.Error:
        return 0


def get_config_snapshots(limit: int = 20) -> List[Dict[str, Any]]:
    """List recent Caddy configuration snapshots."""
    try:
        with get_connection() as conn:
            cur = conn.execute(
                """
                SELECT id, ts, user, description
                FROM config_snapshots
                ORDER BY ts DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [
                {
                    "id": r["id"],
                    "ts": r["ts"],
                    "user": r["user"],
                    "description": r["description"],
                }
                for r in cur.fetchall()
            ]
    except sqlite3.Error:
        return []


def get_config_snapshot(snapshot_id: int) -> Optional[Dict[str, Any]]:
    """Retrieve full configuration JSON of a specific snapshot."""
    try:
        with get_connection() as conn:
            cur = conn.execute(
                "SELECT id, ts, user, description, config_json FROM config_snapshots WHERE id = ?",
                (snapshot_id,),
            )
            row = cur.fetchone()
            if row:
                return {
                    "id": row["id"],
                    "ts": row["ts"],
                    "user": row["user"],
                    "description": row["description"],
                    "config_json": row["config_json"],
                }
    except sqlite3.Error:
        pass
    return None
