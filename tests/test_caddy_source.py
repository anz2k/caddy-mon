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
