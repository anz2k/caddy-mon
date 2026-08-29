"""Version helper — reads from installed package metadata or pyproject.toml."""

try:
    from importlib.metadata import version as _pkg_version

    try:
        __version__ = _pkg_version("caddy-mon")
    except Exception:
        __version__ = "1.0.0-rc1"
except ImportError:
    __version__ = "1.0.0-rc1"
