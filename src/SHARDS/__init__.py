#!/usr/bin/env python3
"""Azazael SHARDS — polyglot skill/tool sharding.

A shard is one capability contract with implementations in one or more of
GO / RUST / PYTHON / JAVASCRIPT / POWERSHELL / SHELL behind a shared
JSON-serializable result shape. Python hosts the manifest, the router, and
the self-test; other languages execute through INTERPRETERS runners.

Layout per shard (only what pays — most shards stay Python-only):

    shards/<name>/{shard.py, shard.go, shard.rs, shard.js, shard.ps1, SHARD.md}

Usage:
    from SHARDS.polyglot import run_shard, self_test
    self_test()  # cold recompute across every available toolchain
"""

from SHARDS.registry import BUDGETS, SHARDS, route_lang, route_shard
from SHARDS.polyglot import run_shard, self_test, supported_shard_langs, sweep
from SHARDS.antibodies import (antibody_status, discover_checkouts,
                               promote_antibody, run_antibody, seek_all,
                               seek_box)

__all__ = ["SHARDS", "BUDGETS", "route_lang", "route_shard",
           "run_shard", "self_test", "supported_shard_langs", "sweep",
           "run_antibody", "seek_all", "seek_box", "antibody_status",
           "promote_antibody", "discover_checkouts"]
