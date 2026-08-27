"""Access-log ingestion and per-host analytics."""

import os
import json
import time
from datetime import datetime
from typing import Optional, Union, List, Set, Dict, Any
from .config import LOG_PATH

_LOG_OFFSET = {"pos": 0, "inode": None}
_LOG_CACHE = []  # list of parsed recent entries ({"ts","host","raw_host","uri","status","duration"})
_WARNED_PERMISSION = False


def _parse_ts(val) -> Optional[float]:
    """Parse various timestamp representations into a float UNIX timestamp."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        # Handle nanoseconds or milliseconds if formatted as large integer
        if val > 1e17:  # nanoseconds
            return float(val) / 1e9
        if val > 1e11:  # milliseconds
            return float(val) / 1e3
        return float(val)
    if isinstance(val, str):
        val = val.strip()
        try:
            f = float(val)
            if f > 1e17:
                return f / 1e9
            if f > 1e11:
                return f / 1e3
            return f
        except ValueError:
            pass
        try:
            return datetime.fromisoformat(val.replace("Z", "+00:00")).timestamp()
        except Exception:
            return None
    return None


def _normalize_host(host: Optional[str]) -> str:
    """Normalize hostname by stripping port and converting to lowercase."""
    if not host:
        return ""
    # Strip port if present (e.g. 'mail.lope.ee:443' -> 'mail.lope.ee')
    return host.split(":")[0].strip().lower()


def _get_active_log_path() -> Optional[str]:
    """Return an accessible log file path, checking LOG_PATH or discovering in /caddy-logs."""
    global _WARNED_PERMISSION
    if os.path.isfile(LOG_PATH):
        return LOG_PATH

    # If default log path is missing, search /caddy-logs directory
    log_dir = os.path.dirname(os.path.abspath(LOG_PATH))
    if os.path.isdir(log_dir):
        try:
            files = sorted(os.listdir(log_dir))
            for fn in files:
                if fn.endswith(".log") or fn.endswith(".json"):
                    candidate = os.path.join(log_dir, fn)
                    if os.path.isfile(candidate):
                        return candidate
        except PermissionError:
            if not _WARNED_PERMISSION:
                print(f"[caddy-mon] Warning: Permission denied reading log directory '{log_dir}'.")
                _WARNED_PERMISSION = True
        except OSError:
            pass
    return None


def parse_user_agent(ua: Optional[str]) -> Dict[str, str]:
    """Parse User-Agent string into category, browser, OS, device, and bot name."""
    if not ua:
        return {"category": "Unknown", "browser": "Unknown", "os": "Unknown", "device": "Unknown", "bot": None}

    ua_lower = ua.lower()

    # Bot detection
    bot_signatures = [
        ("Googlebot", ["googlebot", "google-inspectiontool"]),
        ("Bingbot", ["bingbot", "bingpreview"]),
        ("Yandex", ["yandexbot", "yandeximages"]),
        ("DuckDuckBot", ["duckduckbot", "duckduckgo"]),
        ("Baiduspider", ["baiduspider"]),
        ("AhrefsBot", ["ahrefsbot"]),
        ("SemrushBot", ["semrushbot"]),
        ("Applebot", ["applebot"]),
        ("PetalBot", ["petalbot"]),
        ("UptimeRobot", ["uptimerobot"]),
        ("Scanner/Tool", ["sqlmap", "nikto", "nmap", "masscan", "zgrab", "gobuster", "curl", "python-requests", "go-http-client", "wget", "postman"]),
        ("Bot/Crawler", ["bot", "spider", "crawl", "slurp", "mediapartners-google"]),
    ]

    for bot_name, tokens in bot_signatures:
        if any(tok in ua_lower for tok in tokens):
            return {
                "category": "Bot",
                "browser": bot_name,
                "os": "Bot",
                "device": "Bot",
                "bot": bot_name,
            }

    # Browser detection
    browser = "Other"
    if "edg/" in ua_lower or "edge/" in ua_lower:
        browser = "Edge"
    elif "opr/" in ua_lower or "opera" in ua_lower:
        browser = "Opera"
    elif "chrome/" in ua_lower or "crios/" in ua_lower:
        browser = "Chrome"
    elif "firefox/" in ua_lower or "fxios/" in ua_lower:
        browser = "Firefox"
    elif "safari/" in ua_lower and "chrome/" not in ua_lower:
        browser = "Safari"

    # OS / Platform detection
    os_name = "Other"
    device = "Desktop"
    if "iphone" in ua_lower or "ipad" in ua_lower or "ipod" in ua_lower:
        os_name = "iOS"
        device = "Mobile"
    elif "android" in ua_lower:
        os_name = "Android"
        device = "Mobile"
    elif "windows" in ua_lower:
        os_name = "Windows"
    elif "macintosh" in ua_lower or "mac os x" in ua_lower:
        os_name = "macOS"
    elif "linux" in ua_lower:
        os_name = "Linux"

    if "mobile" in ua_lower:
        device = "Mobile"

    return {
        "category": "Human",
        "browser": browser,
        "os": os_name,
        "device": device,
        "bot": None,
    }


def parse_referer(ref: Optional[str]) -> str:
    """Extract clean domain from Referer URL or return 'Direct / None'."""
    if not ref:
        return "Direct / None"
    ref = ref.strip()
    if not ref or ref in ("-", "null", "none"):
        return "Direct / None"
    try:
        domain = ref.split("://", 1)[-1].split("/", 1)[0].split("?", 1)[0].split(":", 1)[0].lower()
        if domain.startswith("www."):
            domain = domain[4:]
        return domain or "Direct / None"
    except Exception:
        return "Direct / None"


def ingest_logs():
    """Read new lines from the Caddy access log, keep last ~5000 entries.

    Tracks file position + inode so a log rotation restarts cleanly from 0.
    Filters out caddy-mon's own admin-API polling noise (logger=admin.api).
    """
    global _LOG_OFFSET, _LOG_CACHE, _WARNED_PERMISSION
    target_path = _get_active_log_path()
    if not target_path:
        return

    try:
        st = os.stat(target_path)
    except PermissionError:
        if not _WARNED_PERMISSION:
            print(f"[caddy-mon] Warning: Permission denied reading log file '{target_path}'.")
            _WARNED_PERMISSION = True
        return
    except OSError:
        return

    if _LOG_OFFSET["inode"] != st.st_ino:
        _LOG_OFFSET = {"pos": 0, "inode": st.st_ino}
    if st.st_size < _LOG_OFFSET["pos"]:
        _LOG_OFFSET["pos"] = 0

    try:
        with open(target_path, "r", errors="replace") as f:
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
                raw_host = req.get("host") or ""
                host = _normalize_host(raw_host)
                ts = _parse_ts(rec.get("ts"))

                client_ip = req.get("client_ip") or req.get("remote_ip") or ""
                if ":" in client_ip and client_ip.count(":") == 1:
                    client_ip = client_ip.split(":")[0]

                headers = req.get("headers") or {}
                ua_val = headers.get("User-Agent") or req.get("user_agent") or ""
                if isinstance(ua_val, list):
                    ua_str = ua_val[0] if ua_val else ""
                else:
                    ua_str = str(ua_val)

                ref_val = headers.get("Referer") or ""
                if isinstance(ref_val, list):
                    ref_str = ref_val[0] if ref_val else ""
                else:
                    ref_str = str(ref_val)

                size_bytes = rec.get("size") or rec.get("bytes_read") or 0
                try:
                    size_bytes = int(size_bytes)
                except (ValueError, TypeError):
                    size_bytes = 0

                _LOG_CACHE.append({
                    "ts": ts,
                    "host": host,
                    "raw_host": raw_host,
                    "uri": req.get("uri"),
                    "method": req.get("method", "GET"),
                    "client_ip": client_ip,
                    "status": rec.get("status"),
                    "duration": rec.get("duration"),
                    "user_agent": ua_str,
                    "referer": ref_str,
                    "bytes": size_bytes,
                })
    except PermissionError:
        if not _WARNED_PERMISSION:
            print(f"[caddy-mon] Warning: Permission denied accessing '{target_path}'.")
            _WARNED_PERMISSION = True
        return
    except OSError:
        return

    if len(_LOG_CACHE) > 5000:
        _LOG_CACHE = _LOG_CACHE[-5000:]


def host_log_stats(hosts: Union[str, List[str]], window: int = 3600):
    """Return compact log stats for one or more hostnames over `window` seconds.

    Accepts a single hostname or a list of host aliases.
    Returns {"requests": int, "errors_5xx": int, "error_pct": float} or None
    if no matching log entries exist in the window.
    """
    ingest_logs()
    now = time.time()
    cutoff = now - window

    if isinstance(hosts, str):
        target_hosts: Set[str] = {_normalize_host(hosts)}
    else:
        target_hosts = {_normalize_host(h) for h in hosts if h}

    req = 0
    err = 0
    for e in _LOG_CACHE:
        ts = e["ts"]
        if ts is None or ts < cutoff:
            continue
        if e["host"] not in target_hosts:
            continue
        req += 1
        st = e["status"]
        if isinstance(st, int) and st >= 500:
            err += 1

    if req == 0:
        return None
    return {"requests": req, "errors_5xx": err, "error_pct": round(err / req * 100, 1)}


def get_host_recent_logs(hosts: Union[str, List[str]], limit: int = 50) -> List[Dict[str, Any]]:
    """Return the most recent log entries matching a primary host or any of its aliases."""
    ingest_logs()
    if isinstance(hosts, str):
        target_hosts: Set[str] = {_normalize_host(hosts)}
    else:
        target_hosts = {_normalize_host(h) for h in hosts if h}

    matches = []
    for e in reversed(_LOG_CACHE):
        if e.get("host") in target_hosts:
            matches.append(e)
            if len(matches) >= limit:
                break
    return matches


def log_stats(window: int = 3600):
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
