"""Unit tests for caddy_mon.caddy_source parsing and grouping logic."""

import pytest
from caddy_mon.caddy_source import (
    _parse_routes,
    _parse_healthy,
    _tld_group,
    _group_hosts_by_tld,
)


# --------------------------------------------------------------------------
# _parse_routes
# --------------------------------------------------------------------------

def test_parse_simple_host_upstream():
    routes = [{
        "match": [{"host": ["example.ee"]}],
        "handle": [{
            "handler": "reverse_proxy",
            "upstreams": [{"dial": "192.168.1.10:8080"}],
        }],
    }]
    out = _parse_routes(routes)
    assert len(out) == 1
    assert out[0]["hosts"] == ["example.ee"]
    assert out[0]["paths"] == [{"paths": ["/"], "upstreams": ["192.168.1.10:8080"]}]
    assert out[0]["upstreams"] == ["192.168.1.10:8080"]


def test_parse_path_based_routing():
    routes = [{
        "match": [{"host": ["test.local"]}],
        "handle": [{
            "handler": "subroute",
            "routes": [
                {"match": [{"path": ["/api/*"]}], "handle": [{
                    "handler": "reverse_proxy",
                    "upstreams": [{"dial": "192.168.1.20:9000"}],
                }]},
                {"match": [{"path": ["/admin/*"]}], "handle": [{
                    "handler": "reverse_proxy",
                    "upstreams": [{"dial": "192.168.1.21:9001"}],
                }]},
                {"match": [{"path": ["/"]}], "handle": [{
                    "handler": "reverse_proxy",
                    "upstreams": [{"dial": "192.168.1.22:9002"}],
                }]},
            ],
        }],
    }]
    out = _parse_routes(routes)
    assert len(out) == 1
    s = out[0]
    assert s["hosts"] == ["test.local"]
    # 3 distinct paths
    paths = {tuple(b["paths"]) for b in s["paths"]}
    assert ("/api/*",) in paths
    assert ("/admin/*",) in paths
    assert ("/",) in paths
    # upstreams aggregated
    assert set(s["upstreams"]) == {
        "192.168.1.20:9000", "192.168.1.21:9001", "192.168.1.22:9002"
    }


def test_parse_nested_subroute():
    """A handle containing a subroute (nested) should still be walked."""
    routes = [{
        "match": [{"host": ["nested.local"]}],
        "handle": [{
            "handler": "subroute",
            "routes": [{
                "handle": [{
                    "handler": "reverse_proxy",
                    "upstreams": [{"dial": "192.168.1.30:8080"}],
                }],
            }],
        }],
    }]
    out = _parse_routes(routes)
    assert len(out) == 1
    assert out[0]["upstreams"] == ["192.168.1.30:8080"]


def test_parse_multiple_servers():
    """Routes from ALL servers (srv0, srv1) are merged."""
    servers_cfg = {
        "srv0": {"routes": [{
            "match": [{"host": ["a.ee"]}],
            "handle": [{"handler": "reverse_proxy",
                        "upstreams": [{"dial": "192.168.1.40:80"}]}],
        }]},
        "srv1": {"routes": [{
            "match": [{"host": ["b.ee"]}],
            "handle": [{"handler": "reverse_proxy",
                        "upstreams": [{"dial": "192.168.1.41:80"}]}],
        }]},
    }
    # _parse_routes takes a list of route dicts; simulate what refresh() does
    all_routes = []
    for srv in servers_cfg.values():
        all_routes.extend(srv.get("routes", []))
    out = _parse_routes(all_routes)
    hosts = {s["hosts"][0] for s in out}
    assert hosts == {"a.ee", "b.ee"}


def test_parse_deduplicates_identical_branches():
    routes = [{
        "match": [{"host": ["dup.local"]}],
        "handle": [{
            "handler": "subroute",
            "routes": [
                {"match": [{"path": ["/"]}], "handle": [{
                    "handler": "reverse_proxy",
                    "upstreams": [{"dial": "192.168.1.50:80"}],
                }]},
                {"match": [{"path": ["/"]}], "handle": [{
                    "handler": "reverse_proxy",
                    "upstreams": [{"dial": "192.168.1.50:80"}],
                }]},
            ],
        }],
    }]
    out = _parse_routes(routes)
    assert len(out) == 1
    # only one unique branch remains
    assert len(out[0]["paths"]) == 1
    assert out[0]["paths"][0]["upstreams"] == ["192.168.1.50:80"]


def test_parse_aliases_multiple_hosts():
    routes = [{
        "match": [{"host": ["mail.lope.ee", "autoconfig.lope.ee",
                            "autodiscover.lope.ee"]}],
        "handle": [{"handler": "reverse_proxy",
                    "upstreams": [{"dial": "192.168.1.60:80"}]}],
    }]
    out = _parse_routes(routes)
    assert len(out) == 1
    assert set(out[0]["hosts"]) == {
        "mail.lope.ee", "autoconfig.lope.ee", "autodiscover.lope.ee"
    }


# --------------------------------------------------------------------------
# _parse_healthy
# --------------------------------------------------------------------------

def test_parse_healthy_basic():
    text = (
        'caddy_reverse_proxy_upstreams_healthy{upstream="192.168.1.10:8080"} 1\n'
        'caddy_reverse_proxy_upstreams_healthy{upstream="192.168.1.11:8080"} 0\n'
    )
    result = _parse_healthy(text)
    assert result == {"192.168.1.10:8080": True, "192.168.1.11:8080": False}


def test_parse_healthy_ignores_garbage():
    text = (
        'some_other_metric 42\n'
        'caddy_reverse_proxy_upstreams_healthy{upstream="192.168.1.10:8080"} 1\n'
        'this line is broken{caddy_reverse_proxy_upstreams_healthy{upstream="x"} 1\n'
    )
    result = _parse_healthy(text)
    assert result == {"192.168.1.10:8080": True}


def test_parse_healthy_empty():
    assert _parse_healthy("") == {}
    assert _parse_healthy(None) == {}


# --------------------------------------------------------------------------
# _tld_group
# --------------------------------------------------------------------------

def test_tld_group_subdomain():
    assert _tld_group("sub.example.ee") == "example.ee"
    assert _tld_group("a.b.example.ee") == "example.ee"


def test_tld_group_ip():
    assert _tld_group("192.168.1.9") == "1.9"


def test_tld_group_single_label():
    assert _tld_group("localhost") == "localhost"


# --------------------------------------------------------------------------
# _group_hosts_by_tld
# --------------------------------------------------------------------------

def test_group_hosts_by_tld():
    sites = [
        {"group": "lope.ee", "hosts": ["mail.lope.ee"]},
        {"group": "kaaber.ee", "hosts": ["x.kaaber.ee"]},
        {"group": "lope.ee", "hosts": ["pildid.lope.ee"]},
        {"group": "lope.lan", "hosts": ["ha.lope.lan"]},
    ]
    groups = _group_hosts_by_tld(sites)
    by_group = {g["group"]: [s["hosts"][0] for s in g["sites"]] for g in groups}
    assert by_group["lope.ee"] == ["mail.lope.ee", "pildid.lope.ee"]
    assert by_group["kaaber.ee"] == ["x.kaaber.ee"]
    assert by_group["lope.lan"] == ["ha.lope.lan"]
    # sorted by group name
    assert [g["group"] for g in groups] == ["kaaber.ee", "lope.ee", "lope.lan"]


def test_parse_transport_timeouts():
    # Caddy JSON API expresses durations in nanoseconds (Go time.Duration).
    # 30s -> 30_000_000_000 ns, 3600s -> 3_600_000_000_000 ns, 15s -> 15_000_000_000 ns.
    routes = [{
        "match": [{"host": ["stream.lope.ee"]}],
        "handle": [{
            "handler": "reverse_proxy",
            "upstreams": [{"dial": "192.168.1.50:8080"}],
            "transport": {
                "protocol": "http",
                "dial_timeout": 30_000_000_000,
                "read_timeout": 3_600_000_000_000,
                "response_header_timeout": 15_000_000_000,
                "keepalive": {
                    "idle_timeout": 120_000_000_000
                }
            },
            "load_balancing": {
                "selection_policy": {"policy": "least_conn"},
                "retries": 3,
                "try_duration": 30_000_000_000,
                "try_interval": 250_000_000
            }
        }],
    }]
    out = _parse_routes(routes)
    assert len(out) == 1
    s = out[0]
    assert s["transport"] is not None
    assert s["transport"]["dial_timeout"] == "30s"
    assert s["transport"]["read_timeout"] == "1h"
    assert s["transport"]["response_header_timeout"] == "15s"
    assert s["transport"]["keepalive_idle"] == "2m"
    assert s["load_balancing"] is not None
    assert s["load_balancing"]["policy"] == "least_conn"
    assert s["load_balancing"]["retries"] == 3
    assert s["load_balancing"]["try_duration"] == "30s"
    assert s["load_balancing"]["try_interval"] == "250ms"


def test_parse_health_checks():
    """_parse_routes exposes health-check config (active vs passive)."""
    routes = [{
        "match": [{"host": ["hc.lope.ee"]}],
        "handle": [{
            "handler": "reverse_proxy",
            "upstreams": [{"dial": "192.168.1.60:8080"}],
            "health_checks": {
                "active": {
                    "uri": "/healthz",
                    "interval": 5_000_000_000,
                    "timeout": 2_000_000_000,
                },
                "passive": {
                    "max_fails": 3,
                    "fail_duration": 10_000_000_000,
                    "unhealthy_latency": 500_000_000,
                },
            },
        }],
    }]
    out = _parse_routes(routes)
    assert len(out) == 1
    s = out[0]
    assert s["health_checks"] is not None
    hc = s["health_checks"]
    assert hc["active_uri"] == "/healthz"
    assert hc["active_interval"] == "5s"
    assert hc["active_timeout"] == "2s"
    assert hc["max_fails"] == 3
    assert hc["fail_duration"] == "10s"
    assert hc["unhealthy_latency"] == "500ms"


def test_parse_health_checks_absent():
    """No health_checks block -> site health_checks is None."""
    routes = [{
        "match": [{"host": ["plain.lope.ee"]}],
        "handle": [{
            "handler": "reverse_proxy",
            "upstreams": [{"dial": "192.168.1.70:8080"}],
        }],
    }]
    out = _parse_routes(routes)
    assert out[0]["health_checks"] is None


def test_parse_transforms_rewrite_and_headers():
    routes = [{
        "match": [{"host": ["api.lope.ee"]}],
        "handle": [
            {
                "handler": "rewrite",
                "strip_path_prefix": "/api",
                "uri": "/v1{http.request.uri.path}",
            },
            {
                "handler": "headers",
                "request": {
                    "set": {"Host": ["internal-api:8080"]},
                    "add": {"X-Custom": ["true"]}
                },
                "response": {
                    "set": {"Strict-Transport-Security": ["max-age=31536000"]}
                }
            },
            {
                "handler": "reverse_proxy",
                "upstreams": [{"dial": "192.168.1.60:8080"}],
                "handle_response": [
                    {"match": {"status_code": [404, 500]}}
                ]
            }
        ]
    }]
    out = _parse_routes(routes)
    assert len(out) == 1
    s = out[0]
    tr = s.get("transforms")
    assert tr is not None
    assert "strip /api" in tr["rewrites"]
    assert "rewrite -> /v1{http.request.uri.path}" in tr["rewrites"]
    assert any("Host: internal-api:8080" in h for h in tr["headers_up"])
    assert any("X-Custom: true" in h for h in tr["headers_up"])
    assert any("Strict-Transport-Security: max-age=31536000" in h for h in tr["headers_down"])
    assert any("catch status [404, 500]" in hr for hr in tr["handle_response"])


def test_parse_subroute_sequential_middleware():
    """Caddyfile with uri strip_prefix + headers + reverse_proxy generates nested subroutes with sequential middleware."""
    routes = [{
        "match": [{"host": ["anne.kaaber.ee"]}],
        "handle": [
            {
                "handler": "subroute",
                "routes": [
                    {
                        "handle": [
                            {
                                "handler": "rewrite",
                                "strip_path_prefix": "/test"
                            }
                        ]
                    },
                    {
                        "handle": [
                            {
                                "handler": "headers",
                                "response": {
                                    "set": {
                                        "Strict-Transport-Security": ["max-age=31536000"]
                                    }
                                }
                            }
                        ]
                    },
                    {
                        "handle": [
                            {
                                "handler": "reverse_proxy",
                                "headers": {
                                    "request": {
                                        "set": {
                                            "X-Custom-Header": ["test-caddy-mon"]
                                        }
                                    }
                                },
                                "transport": {
                                    "protocol": "http",
                                    "dial_timeout": 5000000000,
                                    "read_timeout": 3600000000000
                                },
                                "upstreams": [
                                    {"dial": "192.168.1.7:3001"}
                                ]
                            }
                        ]
                    }
                ]
            }
        ]
    }]
    out = _parse_routes(routes)
    assert len(out) == 1
    s = out[0]
    assert s["hosts"] == ["anne.kaaber.ee"]
    assert s["upstreams"] == ["192.168.1.7:3001"]
    assert s["transport"] is not None
    assert s["transport"]["dial_timeout"] == "5s"
    assert s["transport"]["read_timeout"] == "1h"
    tr = s.get("transforms")
    assert tr is not None
    assert "strip /test" in tr["rewrites"]
    assert any("X-Custom-Header: test-caddy-mon" in h for h in tr["headers_up"])
    assert any("Strict-Transport-Security: max-age=31536000" in h for h in tr["headers_down"])
