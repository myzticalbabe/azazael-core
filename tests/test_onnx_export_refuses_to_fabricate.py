# coding: utf-8
"""The basic ONNX fallback must REFUSE, not export a model it made up.

Found while executing engram #511 deliverable 3 (the deploy-path attempt). The
route was: `ONNXConverter.convert(...)` -> `except ImportError` -> the fallback.
That fallback accepted `model_path` and **never read it**; it constructed a fresh
`SimpleDecoder` with random weights and exported THAT, returning

    (True, str(output_path), {"format": "onnx_basic", "path": str(onnx_path)})

So a caller that asked to convert its own model got a success whose artifact was
a model it had never seen. On this host the export happened to fail (torch's
dynamo exporter needs `onnxscript`), which is why the defect stayed latent -- it
would have produced a false green the moment the exporter was installed.

The test asserts the two things that matter and that a "returns False" claim alone
would not: **no artifact is emitted**, and the failure says why.
"""

import importlib.util
import logging
import sys
import tempfile

import pytest
from pathlib import Path

# Repo convention: tests insert src/ themselves (tests/conftest.py does not).
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))


def _onnxscript_available() -> bool:
    """True when the ONNX exporter's dependency is installed.

    A `find_spec` probe, not an import: the tests below pin the REFUSAL path,
    which only exists while the dependency is absent. Written this way so the
    probe cannot swallow an ImportError -- absence is the precondition, not an
    error to catch.
    """
    return importlib.util.find_spec("onnxscript") is not None


def _converter():
    from ORCHESTRATION.ml_platforms.universal_converter import ONNXConverter

    return ONNXConverter(logging.getLogger("test-onnx"))


def _metadata(model_path: str):
    from ORCHESTRATION.ml_platforms.universal_converter import ModelMetadata

    return ModelMetadata(
        name="cutting_edge_model",
        path=model_path,
        size_bytes=0,
        quantization="none",
        architecture="cutting_edge_moe_ssm",
        vocab_size=10,
        hidden_size=64,
    )


@pytest.mark.skipif(
    _onnxscript_available(),
    reason="onnxscript installed; refusal path not exercised",
)
def test_fallback_refuses_and_writes_no_artifact():
    """It must not return success, and must not leave a .onnx file behind."""
    with tempfile.TemporaryDirectory() as tmp:
        model_path = str(Path(tmp) / "cutting_edge")
        ok, path, info = _converter()._basic_onnx_export(
            model_path, tmp, _metadata(model_path))

        assert ok is False, (
            "the fallback reported success (`ok=%r`, path=%r) after removing the "
            "fabricated model; it has no exporter, so success can only be a lie"
            % (ok, path))
        assert path == "", "a refused export must not name an output path"

        produced = [p for p in Path(tmp).rglob("*") if p.is_file()]
        assert produced == [], (
            "the refusal still wrote %s; a fabricated artifact is the whole defect"
            % [str(p) for p in produced])


@pytest.mark.skipif(
    _onnxscript_available(),
    reason="onnxscript installed; refusal path not exercised",
)
def test_the_failure_names_the_model_and_the_reason():
    """A failure a caller cannot act on is only half a fix."""
    with tempfile.TemporaryDirectory() as tmp:
        model_path = str(Path(tmp) / "cutting_edge")
        ok, _path, info = _converter()._basic_onnx_export(
            model_path, tmp, _metadata(model_path))

        assert ok is False
        blob = " ".join(str(x) for x in info)
        assert model_path in blob, (
            "the reason must name the model it could not export; got %r" % (info,))
        assert "exporter" in blob.lower() or "optimum" in blob.lower(), (
            "the reason must say what is missing; got %r" % (info,))


@pytest.mark.skipif(
    _onnxscript_available(),
    reason="onnxscript installed; refusal path not exercised",
)
def test_convert_does_not_report_a_successful_onnx_for_this_model():
    """End to end: with no ONNX exporter installed, convert() must not lie."""
    with tempfile.TemporaryDirectory() as tmp:
        model_path = str(Path(tmp) / "cutting_edge")
        ok, path, info = _converter().convert(
            model_path, tmp, _metadata(model_path))

        if ok:  # only legitimate if a real exporter ran and produced an artifact
            onnx_files = [p for p in Path(path).rglob("*.onnx")]
            assert onnx_files, (
                "convert() returned success with no .onnx artifact at %r" % (path,))
        else:
            assert path == "", "a failed conversion must not name an output path"
            assert info, "a failed conversion must carry its reason"
