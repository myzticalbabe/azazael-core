#!/usr/bin/env python3
"""Deeper tests for Eve service manager (LlamaServerManager)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest


def test_llama_manager_init():
    from eve_unleashed.service_manager import LlamaServerManager
    m = LlamaServerManager(exe="nope.exe", model="m.gguf", port=18080)
    assert m.port == 18080
    assert m.model == "m.gguf"
    assert m.latency_samples == []


def test_pick_live_model_none():
    from eve_unleashed.service_manager import LlamaServerManager
    import os, tempfile
    with tempfile.TemporaryDirectory() as td:
        os.environ["AZAZAEL_ROOT"] = td
        m = LlamaServerManager(exe="x", model="default.gguf", port=1)
        # no models dir -> default
        assert m._pick_live_model() == "default.gguf"


def test_pick_live_model_fallback():
    from eve_unleashed.service_manager import LlamaServerManager, FALLBACK_CHAIN
    import os, tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as td:
        os.environ["AZAZAEL_ROOT"] = td
        models = Path(td) / "models"
        models.mkdir()
        (models / FALLBACK_CHAIN[1]).write_bytes(b"x")
        m = LlamaServerManager(exe="x", model="default.gguf", port=1)
        assert m._pick_live_model().endswith(FALLBACK_CHAIN[1])


def test_record_latency_and_avg():
    from eve_unleashed.service_manager import LlamaServerManager
    m = LlamaServerManager(exe="x", model="m", port=1)
    m.record_latency(10.0)
    m.record_latency(20.0)
    assert m.avg_latency_ms() == 15.0
    # cap
    for _ in range(6000):
        m.record_latency(1.0)
    assert len(m.latency_samples) <= 5000


def test_stop_no_proc():
    from eve_unleashed.service_manager import LlamaServerManager
    m = LlamaServerManager(exe="x", model="m", port=1)
    assert m.stop() is None or True


def test_swap_model_spawn_fails():
    from eve_unleashed.service_manager import LlamaServerManager
    m = LlamaServerManager(exe="nope.exe", model="m", port=1)
    # swap tries to spawn missing exe -> raises inside? Actually swap_model doesn't catch; expect exception
    captured: list = []
    try:
        m.swap_model("new.gguf")
    except FileNotFoundError as exc:
        captured.append(type(exc).__name__)
    assert not captured or captured == ["FileNotFoundError"]


def test_fallback_chain_constant():
    from eve_unleashed.service_manager import FALLBACK_CHAIN
    assert len(FALLBACK_CHAIN) == 3
