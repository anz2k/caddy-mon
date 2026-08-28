"""Unit tests for caddy_mon.log_source module."""

import os
import json
import time
import tempfile
from unittest import mock
import pytest

from caddy_mon import log_source


def test_normalize_host():
    assert log_source._normalize_host("mail.lope.ee:443") == "mail.lope.ee"
    assert log_source._normalize_host("ha.lope.lan:8123") == "ha.lope.lan"
    assert log_source._normalize_host("WWW.LOPE.EE") == "www.lope.ee"
    assert log_source._normalize_host("") == ""
    assert log_source._normalize_host(None) == ""


def test_parse_ts():
    # Standard UNIX timestamp float
    assert log_source._parse_ts(1724584800.5) == 1724584800.5
    # Milliseconds integer
    assert log_source._parse_ts(1724584800500) == 1724584800.5
    # ISO string
    iso_ts = log_source._parse_ts("2026-08-25T14:20:00Z")
    assert iso_ts is not None
    assert iso_ts > 0


def test_host_log_stats_matches_with_port_and_aliases():
    now = time.time()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
        log_file = f.name
        # Line 1: primary host with :443
        f.write(json.dumps({
            "ts": now - 100,
            "logger": "http.log.access",
            "request": {"host": "mail.lope.ee:443", "uri": "/inbox"},
            "status": 200,
            "duration": 0.05,
        }) + "\n")
        # Line 2: alias host with :443 and 502 error
        f.write(json.dumps({
            "ts": now - 50,
            "logger": "http.log.access",
            "request": {"host": "autoconfig.lope.ee:443", "uri": "/mail/config-v1.1.xml"},
            "status": 502,
            "duration": 0.02,
        }) + "\n")
        # Line 3: other host
        f.write(json.dumps({
            "ts": now - 20,
            "logger": "http.log.access",
            "request": {"host": "pilv.lope.ee:443", "uri": "/login"},
            "status": 200,
            "duration": 0.08,
        }) + "\n")

    try:
        with mock.patch.object(log_source, "LOG_PATH", log_file):
            log_source._LOG_OFFSET = {"pos": 0, "inode": None}
            log_source._LOG_CACHE = []

            # Single host check with port stripping
            stats_single = log_source.host_log_stats("mail.lope.ee", window=3600)
            assert stats_single is not None
            assert stats_single["requests"] == 1
            assert stats_single["errors_5xx"] == 0

            # Multi-host alias check (mail.lope.ee + autoconfig.lope.ee)
            stats_aliases = log_source.host_log_stats(["mail.lope.ee", "autoconfig.lope.ee"], window=3600)
            assert stats_aliases is not None
            assert stats_aliases["requests"] == 2
            assert stats_aliases["errors_5xx"] == 1
            assert stats_aliases["error_pct"] == 50.0

            # Aggregated log_stats check
            summary = log_source.log_stats(window=3600)
            assert len(summary["rows"]) == 3  # mail.lope.ee, autoconfig.lope.ee, pilv.lope.ee
            assert len(summary["recent_5xx"]) == 1
    finally:
        if os.path.exists(log_file):
            os.unlink(log_file)


def test_log_stats_percentiles():
    """log_stats computes p50/p95/p99 and a latency histogram from durations."""
    import time as _time
    now = _time.time()
    fake = [
        {"ts": now - 10, "host": "d.lope.ee", "uri": "/", "status": 200, "duration": 0.005},
        {"ts": now - 9, "host": "d.lope.ee", "uri": "/", "status": 200, "duration": 0.010},
        {"ts": now - 8, "host": "d.lope.ee", "uri": "/", "status": 200, "duration": 0.020},
        {"ts": now - 7, "host": "d.lope.ee", "uri": "/", "status": 200, "duration": 0.050},
        {"ts": now - 6, "host": "d.lope.ee", "uri": "/", "status": 200, "duration": 0.200},
        {"ts": now - 5, "host": "d.lope.ee", "uri": "/", "status": 500, "duration": 1.500},
    ]
    with mock.patch.object(log_source, "_LOG_CACHE", fake), \
         mock.patch("caddy_mon.log_source.ingest_logs"):
        s = log_source.log_stats(window=3600)
    row = next(r for r in s["rows"] if r["host"] == "d.lope.ee")
    # durations in ms: 5, 10, 20, 50, 200, 1500
    assert row["avg_ms"] == 297.5  # (5+10+20+50+200+1500)/6
    assert row["p50_ms"] == 35.0   # median of sorted [5,10,20,50,200,1500] interpolated
    assert row["p95_ms"] >= 200.0
    assert row["p99_ms"] >= 200.0
    hist = {b["label"]: b["count"] for b in row["latency_histogram"]}
    assert hist["0-10ms"] == 1   # 5 ms
    assert hist["10-50ms"] == 2  # 10, 20 ms
    assert hist["50-100ms"] == 1 # 50 ms
    assert hist["1s+"] == 1      # 1500 ms


def test_percentile_empty():
    assert log_source.percentile([], 95) == 0.0
    assert log_source.percentile([1.0], 50) == 1.0
