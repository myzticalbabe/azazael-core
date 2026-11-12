# coding: utf-8
"""Tests for the GGUF quantize stage (Engram #523).

``GGUFAnalyzer.convert`` either produces a real artifact through the
llama-quantize backend (verified by ``servemesh.verify`` reporting bytes)
or returns a structured refusal naming the missing capability. Both paths
are pinned hermetically: the backend seam is stubbed, never the verdict.
"""

import logging
import sys
from pathlib import Path
from types import SimpleNamespace

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from ORCHESTRATION.ml_platforms.universal_converter import GGUFAnalyzer


def _analyzer():
    return GGUFAnalyzer(logging.getLogger("test-gguf-quantize"))


def _cfg():
    from CORE.WORMS.expansion.cutting_edge_model import CuttingEdgeConfig
    return CuttingEdgeConfig(d_model=16, n_layers=1, d_state=4, d_ff=8,
                             n_experts=8, top_k=2, seed=7)


def test_refuse_missing_source(tmp_path):
    ok, path, reasons = _analyzer().convert(
        str(tmp_path / "no-such-model.gguf"), str(tmp_path))
    assert ok is False
    assert path == ""
    assert any("not found" in r for r in reasons), reasons


def test_refuse_unknown_quant(tmp_path):
    src = tmp_path / "model-f16.gguf"
    src.write_bytes(b"GGUF" + b"\x00" * 64)
    ok, path, reasons = _analyzer().convert(
        str(src), str(tmp_path), quantization="Q9_XXX")
    assert ok is False
    assert path == ""
    assert any("unknown quant type" in r for r in reasons), reasons


def test_refuse_without_backend(tmp_path, monkeypatch):
    src = tmp_path / "model-f16.gguf"
    src.write_bytes(b"GGUF" + b"\x00" * 64)
    monkeypatch.setattr(GGUFAnalyzer, "find_quantizer",
                        staticmethod(lambda: None))
    ok, path, reasons = _analyzer().convert(str(src), str(tmp_path))
    assert ok is False
    assert path == ""
    assert any("llama-quantize" in r for r in reasons), reasons
    assert not list(tmp_path.glob("*.gguf"))[1:], \
        "refusal must not fabricate an artifact beside the source"


def test_success_plumbing_reports_bytes_through_verify(tmp_path, monkeypatch):
    """The convert->publish->verify chain with a stubbed backend.

    The stub stands in for the llama-quantize binary only: it proves OUR
    plumbing invokes the backend, checks the artifact, and reports bytes.
    It proves nothing about quantization quality -- that is the backend's
    job, not ours.
    """
    src = tmp_path / "model-f16.gguf"
    src.write_bytes(b"GGUF" + b"\x00" * 64)

    def _stub(argv):
        dst = Path(argv[2])
        dst.write_bytes(b"Q" * 4096)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(GGUFAnalyzer, "find_quantizer",
                        staticmethod(lambda: "stub-quantizer"))
    monkeypatch.setattr(GGUFAnalyzer, "_run_quantizer",
                        staticmethod(_stub))
    ok, path, info = _analyzer().convert(str(src), str(tmp_path))
    assert ok is True, info
    assert info["bytes"] == 4096

    from CORE.WORMS.expansion.servemesh import ModelShardServerWorm
    mesh = ModelShardServerWorm()
    pub = mesh.publish("quant-test", "s0", "test", "gguf", "Q4_K_M",
                       artifact_path=path)
    assert pub["success"] is True
    verdict = mesh.verify("quant-test", "s0")
    assert verdict["verified"] is True, verdict
    assert verdict["bytes"] == 4096, verdict


def test_plan_quantize_reflects_missing_backend(monkeypatch):
    from CORE.WORMS.expansion.cutting_edge_model import CuttingEdgeModel
    monkeypatch.setattr(GGUFAnalyzer, "find_quantizer",
                        staticmethod(lambda: None))
    plan = CuttingEdgeModel(_cfg()).deployment_plan()
    assert plan["quantize"]["available"] is False
    assert "llama-quantize" in plan["quantize"]["reason"], plan["quantize"]


def test_plan_quantize_available_with_backend(monkeypatch):
    from CORE.WORMS.expansion.cutting_edge_model import CuttingEdgeModel
    monkeypatch.setattr(GGUFAnalyzer, "find_quantizer",
                        staticmethod(lambda: "stub-quantizer"))
    plan = CuttingEdgeModel(_cfg()).deployment_plan()
    assert plan["quantize"]["available"] is True
    assert plan["quantize"]["quantizer_binary"] == "stub-quantizer"
