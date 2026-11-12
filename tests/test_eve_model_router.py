#!/usr/bin/env python3
"""Deeper tests for Eve ModelRouter."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest


def test_classify():
    from eve_unleashed.model_router import ModelRouter
    r = ModelRouter()
    assert r.classify("show me the code fix") == "code"
    assert r.classify("screenshot of the diagram") == "vision"
    assert r.classify("quick status summary") == "quick"
    assert r.classify("hello world") == "default"


def test_route_available_filter():
    from eve_unleashed.model_router import ModelRouter
    r = ModelRouter(available_models=["Qwen2.5-1.5B-instruct-Q8_0.gguf"])
    d = r.route("fix this bug")
    assert d.query_type == "code"
    assert d.model == "Qwen2.5-1.5B-instruct-Q8_0.gguf"
    assert d.fallback_chain == ["Qwen2.5-1.5B-instruct-Q8_0.gguf"]
    # no available -> chain preserved
    r2 = ModelRouter()
    d2 = r2.route("fix this bug")
    assert d2.model == "Qwen2.5-1.5B-instruct-Q8_0.gguf"


def test_route_for_type_and_stats():
    from eve_unleashed.model_router import ModelRouter
    r = ModelRouter()
    d = r.route_for_type("vision")
    assert d.query_type == "vision"
    st = r.stats()
    assert "route_counts" in st
    assert st["route_counts"].get("vision") == 1


def test_route_counts_increment():
    from eve_unleashed.model_router import ModelRouter
    r = ModelRouter()
    r.route("a")
    r.route("b")
    assert r._route_counts["default"] == 2


def test_default_routes_copy():
    from eve_unleashed.model_router import ModelRouter, DEFAULT_ROUTES
    r = ModelRouter(routes={"quick": ["m1"]})
    assert r.routes == {"quick": ["m1"]}
    # default not mutated
    assert "code" in DEFAULT_ROUTES


def test_set_available_models():
    from eve_unleashed.model_router import ModelRouter
    r = ModelRouter()
    r.set_available_models(["x.gguf"])
    assert r.available_models == {"x.gguf"}
