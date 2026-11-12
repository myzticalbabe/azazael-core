"""Tests for the Go WASM tier (wasm_tutorial generate_go/build/run).

Source generation and toolchain reporting are stdlib-only and always
run. The compile+execute test runs only when go and node are both on
PATH; otherwise it skips cleanly with the missing tool named.
"""

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eve_unleashed import wasm_tutorial as _w

STATE = {"total": 2, "answered": 1, "summary": "ABLATION: probe",
         "rows": [{"provider": "a", "verdict": "ANSWERED", "detail": "d1"},
                  {"provider": "b", "verdict": "REFUSED", "detail": "d2"}],
         "nsp": "judged"}


def test_generate_go_writes_module_sources(tmp_path):
    out, total = _w.generate_go(STATE, tmp_path / "gomod")
    assert total == 4  # overview + 2 providers + fallback
    src = (out / "main.go").read_text(encoding="utf-8")
    assert "syscall/js" in src and "renderFrame" in src
    assert (out / "go.mod").is_file()
    assert (out / "run_node.js").is_file()
    assert (out / "state.json").is_file()


def test_have_toolchain_reports_shape():
    for kind in ("rust", "go", "node"):
        rep = _w.have_toolchain(kind)
        assert set(rep) == {kind == "rust" and "wasm-pack" or kind}
        assert all(v is None or isinstance(v, str) for v in rep.values())
    with pytest.raises(ValueError):
        _w.have_toolchain("cobol")


needs_go_node = pytest.mark.skipif(
    not (shutil.which("go") and shutil.which("node")),
    reason="go+node toolchains required for compile+execute")


@needs_go_node
def test_go_build_and_run_end_to_end(tmp_path):
    out, total = _w.generate_go(STATE, tmp_path / "gomod")
    pkg = _w.build_wasm_go(out)
    wasm = list(pkg.glob("*.wasm"))
    assert len(wasm) == 1 and wasm[0].stat().st_size > 1000
    assert (pkg / "wasm_exec.js").is_file()
    rep = _w.run_wasm_go(pkg)
    assert rep["ok"] is True
    assert rep["step_count"] == total == 4
    assert rep["frame_chars"] > 200
    assert rep["frame_head"].startswith("<svg")
