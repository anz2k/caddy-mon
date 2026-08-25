"""Pytest configuration for caddy-mon tests.

Adds the repo root to sys.path so the `caddy_mon` package is importable
when running `pytest` from the repo root (no installation needed).
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
