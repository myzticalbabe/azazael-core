# coding: utf-8
"""
CORE/paths/state.py — Shared state for all domain path modules.

Contains the global cache dict, VERSION constant, and cache-clearing utility.
All domain modules import _cache from here to share memoized path results.
"""

VERSION = "1.0.0"

# -- Shared cache for all domain path modules --------------------------------
_cache: dict = {}


def clear_cache():
    """Clear the path resolution cache across ALL domain modules.

    Since all domain modules share this single _cache dict, clearing
    it once invalidates memoized results everywhere.
    """
    _cache.clear()
