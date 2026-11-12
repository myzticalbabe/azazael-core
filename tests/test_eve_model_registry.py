#!/usr/bin/env python3
"""Deeper tests for Eve ModelRegistry."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest


def test_registry_register_new_and_dup(tmp_path):
    from eve_unleashed.model_registry import ModelRegistry
    reg = ModelRegistry(registry_path=tmp_path / "reg.json")
    # create a model file
    f = tmp_path / "m.pt"
    f.write_bytes(b"x" * 2000)
    cp = reg.register(str(f), framework="torch", project="p1")
    assert cp is not None
    assert reg.total_models() == 1
    # duplicate
    dup = reg.register(str(f), framework="torch", project="p2")
    assert dup is None
    assert reg.total_models() == 1
    rec = reg.get(str(f))
    assert rec is not None
    assert "p2" in rec.projects


def test_registry_record_inference():
    from eve_unleashed.model_registry import ModelRegistry
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        f = td / "m.pt"
        f.write_bytes(b"x" * 2000)
        reg = ModelRegistry(registry_path=td / "reg.json")
        reg.register(str(f), framework="torch")
        reg.record_inference(str(f), latency_ms=10.0)
        reg.record_inference(str(f), latency_ms=20.0)
        rec = reg.get(str(f))
        assert rec.inference_count == 2
        assert rec.avg_latency_ms == 15.0
        assert reg.total_inferences() == 2


def test_registry_list_and_stats():
    from eve_unleashed.model_registry import ModelRegistry
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        f1 = td / "a.pt"; f1.write_bytes(b"x" * 2000)
        f2 = td / "b.onnx"; f2.write_bytes(b"x" * 2000)
        reg = ModelRegistry(registry_path=td / "reg.json")
        reg.register(str(f1), framework="torch")
        reg.register(str(f2), framework="onnx")
        assert len(reg.list_all()) == 2
        assert len(reg.list_by_framework("torch")) == 1
        assert len(reg.list_by_project("none")) == 0
        st = reg.stats()
        assert st["model_count"] == 2
        assert "torch" in st["frameworks"]


def test_registry_select_fastest():
    from eve_unleashed.model_registry import ModelRegistry
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        f1 = td / "a.pt"; f1.write_bytes(b"x" * 2000)
        f2 = td / "b.pt"; f2.write_bytes(b"x" * 2000)
        reg = ModelRegistry(registry_path=td / "reg.json")
        reg.register(str(f1), framework="torch")
        reg.register(str(f2), framework="torch")
        reg.record_inference(str(f1), latency_ms=5.0)
        reg.record_inference(str(f2), latency_ms=20.0)
        assert reg.select_fastest(framework="torch") == str(f1.resolve())
        # cold fallback: smallest size
        reg2 = ModelRegistry(registry_path=td / "reg2.json")
        assert reg2.select_fastest() is None


def test_registry_persistence(tmp_path):
    from eve_unleashed.model_registry import ModelRegistry
    f = tmp_path / "m.pt"; f.write_bytes(b"x" * 2000)
    r1 = ModelRegistry(registry_path=tmp_path / "reg.json")
    r1.register(str(f), framework="torch")
    r2 = ModelRegistry(registry_path=tmp_path / "reg.json")
    assert r2.total_models() == 1


def test_registry_guess_framework_ext():
    from eve_unleashed.model_registry import _guess_framework_ext
    assert _guess_framework_ext("a.gguf") == "gguf"
    assert _guess_framework_ext("a.pt") == "torch"
    assert _guess_framework_ext("a.xyz") == "unknown"
