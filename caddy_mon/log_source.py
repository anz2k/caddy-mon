"""Access-log ingestion and per-host analytics."""

import os
import json
import time
from .config import LOG_PATH

_LOG_OFFSET = {"pos": 0, "inode": None}
_LOG_CACHE = []  # list of parsed recent entries ({"ts","host","uri","status","duration"})


def ingest_logs():
    """Read new lines from the Caddy access log, keep last ~5000 entries.

    Tracks file position + inode so a log rotation (new file) restarts from 0.
    Filters out caddy-mon's own admin-API polling noise (logger=admin.api).
    """
    global _LOG_OFFSET, _LOG_CACHE
    try:
        st = os.stat(LOG_PATH)
    except OSError:
        return
    if _LOG_OFFSET["inode"] != st.st_ino:
        _LOG_OFFSET = {"pos": 0, "inode": st.st_ino}
    if st.st_size < _LOG_OFFSET["pos"]:
        _LOG_OFFSET["pos"] = 0
    try:
        with open(LOG_PATH, "r", errors="replace") as f:
            f.seek(_LOG_OFFSET["pos"])
            while True:
                line = f.readline()
                if not line:
                    break
                _LOG_OFFSET["pos"] = f.tell()
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if rec.get("logger") == "admin.api":
                    continue
                req = rec.get("request", {})
                _LOG_CACHE.append({
                    "ts": rec.get("ts"),
                    "host": req.get("host"),
                    "uri": req.get("uri"),
                    "status": rec.get("status"),
                    "duration": rec.get("duration"),
                })
    except OSError:
        return
    if len(_LOG_CACHE) > 5000:
        _LOG_CACHE = _LOG_CACHE[-5000:]


def host_log_stats(host, window=3600):
    """Return compact log stats for one host over `window` seconds.

    Returns {"requests": int, "errors_5xx": int, "error_pct": float} or None
    if the host has no log entries in the window.
    """
    ingest_logs()
    now = time.time()
    cutoff = now - window
    req = 0
    err = 0
    for e in _LOG_CACHE:
        ts = e["ts"]
        if ts is None or ts < cutoff:
            continue
        if e["host"] != host:
            continue
        req += 1
        st = e["status"]
        if isinstance(st, int) and st >= 500:
            err += 1
    if req == 0:
        return None
    return {"requests": req, "errors_5xx": err, "error_pct": round(err / req * 100, 1)}


def log_stats(window=3600):
    """Aggregate recent log entries over the last `window` seconds."""
    ingest_logs()
    now = time.time()
    cutoff = now - window
    per_host = {}
    recent_5xx = []
    for e in _LOG_CACHE:
        ts = e["ts"]
        if ts is None or ts < cutoff:
            continue
        h = e["host"]
        if not h:
            continue
        d = per_host.setdefault(h, {"requests": 0, "errors_5xx": 0, "durations": []})
        d["requests"] += 1
        st = e["status"]
        if isinstance(st, int) and st >= 500:
            d["errors_5xx"] += 1
            if len(recent_5xx) < 50:
                recent_5xx.append(e)
        if isinstance(e["duration"], (int, float)):
            d["durations"].append(e["duration"])
    rows = []
    for h, d in per_host.items():
        avg_ms = (sum(d["durations"]) / len(d["durations"]) * 1000) if d["durations"] else 0.0
        err_pct = (d["errors_5xx"] / d["requests"] * 100) if d["requests"] else 0.0
        rows.append({
            "host": h,
            "requests": d["requests"],
            "errors_5xx": d["errors_5xx"],
            "error_pct": round(err_pct, 1),
            "avg_ms": round(avg_ms, 1),
        })
    rows.sort(key=lambda r: (-r["error_pct"], -r["requests"]))
    return {"window_seconds": window, "rows": rows, "recent_5xx": recent_5xx}
