# coding: utf-8
"""Tests for the graph_semantic pipeline runner.

The defect these lock in: `run_extraction.py` re-ran end-to-end aborted at
`doc_digest.py` with `UnicodeEncodeError` because doc titles contain non-ASCII
(and leading BOMs) and a cp1252 Windows console cannot encode them. The runner now
forces `PYTHONIOENCODING=utf-8` on every child, and `doc_digest.py` reconfigures
its own stdout so it is also safe to run standalone.
"""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GS_DIR = REPO_ROOT / "tools" / "graph_semantic"


def _load(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        pytest.skip(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_child_env_forces_utf8():
    """The runner must set PYTHONIOENCODING=utf-8 for every child step."""
    runner = _load("gs_run_extraction", GS_DIR / "run_extraction.py")
    env = runner.child_env()
    assert env["PYTHONIOENCODING"] == "utf-8"


def test_pipeline_runs_enhanced_and_bridge_layers():
    """The step list must include the enhanced-semantic and bridge layers."""
    runner = _load("gs_run_extraction_steps", GS_DIR / "run_extraction.py")
    scripts = [s for s, _ in runner.STEPS]
    assert "enhanced_semantic.py" in scripts
    assert "bridge_layer.py" in scripts
    assert scripts.index("enhanced_semantic.py") < scripts.index("bridge_layer.py")


def test_doc_digest_survives_cp1252_console(tmp_path):
    """Standalone doc_digest must not die printing a non-ASCII title on cp1252."""
    (tmp_path / "graphify-out").mkdir()
    # A title with a BOM and a non-Latin-1 character, as the real corpus has.
    (tmp_path / "TITLE.md").write_text(
        "# \ufeffAzazael \u2014 \u03b1\u03b2\u03b3 title\n\nbody text here\n", encoding="utf-8"
    )
    env = dict(os.environ, PYTHONIOENCODING="cp1252")
    proc = subprocess.run(
        [sys.executable, str(GS_DIR / "doc_digest.py")],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert (tmp_path / "graphify-out" / "_digest.json").exists()
