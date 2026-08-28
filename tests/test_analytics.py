"""Unit tests for traffic and visitor analytics (log parsing, UA categorization, referrers, and aggregation)."""

import time
from caddy_mon.log_source import parse_user_agent, parse_referer, _LOG_CACHE
from caddy_mon.analytics_page import get_traffic_analytics, _fmt_bytes
from caddy_mon.db import init_db, upsert_hourly_traffic, get_traffic_history, prune_old_history


def test_parse_user_agent_bots():
    # Search engines
    g = parse_user_agent("Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)")
    assert g["category"] == "Bot"
    assert g["bot"] == "Googlebot"

    b = parse_user_agent("Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)")
    assert b["category"] == "Bot"
    assert b["bot"] == "Bingbot"

    # Scanners and tools
    s = parse_user_agent("sqlmap/1.6#stable (https://sqlmap.org)")
    assert s["category"] == "Bot"
    assert s["bot"] == "Scanner/Tool"

    c = parse_user_agent("curl/7.88.1")
    assert c["category"] == "Bot"
    assert c["bot"] == "Scanner/Tool"


def test_parse_user_agent_browsers_and_os():
    # Chrome on Windows
    u1 = parse_user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    assert u1["category"] == "Human"
    assert u1["browser"] == "Chrome"
    assert u1["os"] == "Windows"
    assert u1["device"] == "Desktop"

    # Safari on iPhone (Mobile)
    u2 = parse_user_agent("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
    assert u2["category"] == "Human"
    assert u2["browser"] == "Safari"
    assert u2["os"] == "iOS"
    assert u2["device"] == "Mobile"

    # Firefox on Linux
    u3 = parse_user_agent("Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/119.0")
    assert u3["category"] == "Human"
    assert u3["browser"] == "Firefox"
    assert u3["os"] == "Linux"


def test_parse_referer():
    assert parse_referer("https://www.google.com/search?q=caddy") == "google.com"
    assert parse_referer("https://t.co/xyz123") == "t.co"
    assert parse_referer("http://reddit.com/r/selfhosted") == "reddit.com"
    assert parse_referer("") == "Direct / None"
    assert parse_referer("-") == "Direct / None"


def test_fmt_bytes():
    assert _fmt_bytes(500) == "500 B"
    assert _fmt_bytes(2048) == "2.0 KB"
    assert _fmt_bytes(10 * 1024 * 1024) == "10.0 MB"
    assert _fmt_bytes(2 * 1024 * 1024 * 1024) == "2.00 GB"


def test_traffic_analytics_aggregation():
    now = time.time()
    # Mock log cache
    _LOG_CACHE.clear()
    _LOG_CACHE.extend([
        {
            "ts": now - 100,
            "host": "anne.kaaber.ee",
            "raw_host": "anne.kaaber.ee:443",
            "uri": "/blog/post-1",
            "method": "GET",
            "client_ip": "1.2.3.4",
            "status": 200,
            "duration": 0.025,
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0",
            "referer": "https://google.com/search",
            "bytes": 5000,
        },
        {
            "ts": now - 50,
            "host": "anne.kaaber.ee",
            "raw_host": "anne.kaaber.ee",
            "uri": "/blog/post-1",
            "method": "GET",
            "client_ip": "1.2.3.4",
            "status": 200,
            "duration": 0.020,
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0",
            "referer": "https://google.com/search",
            "bytes": 5000,
        },
        {
            "ts": now - 20,
            "host": "anne.kaaber.ee",
            "raw_host": "anne.kaaber.ee",
            "uri": "/api/v1/status",
            "method": "GET",
            "client_ip": "5.6.7.8",
            "status": 500,
            "duration": 0.150,
            "user_agent": "Mozilla/5.0 (compatible; Googlebot/2.1)",
            "referer": "-",
            "bytes": 200,
        },
        {
            "ts": now - 10,
            "host": "mail.lope.ee",
            "raw_host": "mail.lope.ee",
            "uri": "/login",
            "method": "GET",
            "client_ip": "9.9.9.9",
            "status": 200,
            "duration": 0.010,
            "user_agent": "Mozilla/5.0 (iPhone; CPU OS 17_0) Safari/604.1",
            "referer": "-",
            "bytes": 3000,
        }
    ])

    data = get_traffic_analytics(window=3600)
    s = data["summary"]
    assert s["total_requests"] == 4
    assert s["unique_visitors"] == 3  # 1.2.3.4, 5.6.7.8, 9.9.9.9
    assert s["human_requests"] == 3
    assert s["bot_requests"] == 1
    assert s["errors_5xx"] == 1
    # error_rate_pct now measures server errors (5xx) only — the 500 above = 25% of 4 requests
    assert s["error_rate_pct"] == 25.0
    # client error rate (4xx) is tracked separately
    assert s["client_error_rate_pct"] == 0.0

    # Add a 4xx entry to verify it counts toward client_error_rate_pct but not error_rate_pct
    _LOG_CACHE.append({
        "ts": time.time() - 5,
        "host": "anne.kaaber.ee",
        "raw_host": "anne.kaaber.ee",
        "uri": "/missing",
        "method": "GET",
        "client_ip": "1.2.3.4",
        "status": 404,
        "duration": 0.005,
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0",
        "referer": "-",
        "bytes": 150,
    })
    data2 = get_traffic_analytics(window=3600)
    assert data2["summary"]["errors_4xx"] == 1
    assert data2["summary"]["client_error_rate_pct"] == 20.0  # 1 of 5
    assert data2["summary"]["error_rate_pct"] == 20.0  # 1 of 5 (5xx still 1)

    # Top paths
    paths = {p["path"]: p["count"] for p in data["top_paths"]}
    assert paths["/blog/post-1"] == 2
    assert paths["/api/v1/status"] == 1

    # Referrers
    refs = {r["source"]: r["count"] for r in data["top_referrers"]}
    assert refs["google.com"] == 2
    assert refs["Direct / None"] == 2

    # Domains
    doms = {d["host"]: d["requests"] for d in data["domains"]}
    assert doms["anne.kaaber.ee"] == 3
    assert doms["mail.lope.ee"] == 1


def test_traffic_analytics_host_filter():
    now = time.time()
    _LOG_CACHE.clear()
    _LOG_CACHE.extend([
        {
            "ts": now - 50,
            "host": "anne.kaaber.ee",
            "uri": "/",
            "client_ip": "1.1.1.1",
            "status": 200,
            "duration": 0.01,
            "user_agent": "curl/7.88",
            "bytes": 100,
        },
        {
            "ts": now - 20,
            "host": "mail.lope.ee",
            "uri": "/",
            "client_ip": "2.2.2.2",
            "status": 200,
            "duration": 0.01,
            "user_agent": "curl/7.88",
            "bytes": 200,
        }
    ])

    data = get_traffic_analytics(window=3600, host_filter="anne.kaaber.ee")
    assert data["summary"]["total_requests"] == 1
    assert data["summary"]["unique_visitors"] == 1
    assert data["domains"][0]["host"] == "anne.kaaber.ee"


def test_db_hourly_traffic_upsert_and_retrieve():
    init_db()
    hour_ts = 1720000000.0
    upsert_hourly_traffic(
        ts_hour=hour_ts,
        host="test.lope.ee",
        requests=150,
        unique_ips=45,
        bytes_sent=1024000,
        errors_4xx=5,
        errors_5xx=1,
        avg_duration_ms=35.5,
    )

    rows = get_traffic_history(host="test.lope.ee", since_ts=hour_ts)
    assert len(rows) >= 1
    r = [row for row in rows if row["ts_hour"] == hour_ts][0]
    assert r["requests"] == 150
    assert r["unique_ips"] == 45
    assert r["bytes_sent"] == 1024000
    assert r["errors_4xx"] == 5
    assert r["errors_5xx"] == 1
    assert r["avg_duration_ms"] == 35.5
