"""Tests for the Rust WASM tier (wasm_tutorial generate/build/run).

Needs the real Rust toolchain (rustc + wasm32 target + wasm-pack) and
node. Source generation and toolchain reporting always run; the
compile+execute test runs only when wasm-pack and node are both on
PATH, otherwise it skips cleanly with the missing tool named.
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


def test_generate_writes_crate_sources(tmp_path):
    out, total = _w.generate(STATE, tmp_path / "crate")
    assert total == 4  # overview + 2 providers + fallback
    src = (out / "src" / "lib.rs").read_text(encoding="utf-8")
    assert "render_frame" in src and "wasm_bindgen" in src
    assert (out / "Cargo.toml").is_file()
    assert (out / "index.html").is_file()
    assert (out / "state.json").is_file()


def test_build_rejects_unknown_target(tmp_path):
    out, _ = _w.generate(STATE, tmp_path / "crate")
    with pytest.raises(ValueError):
        _w.build_wasm(out, target="wasi")


def test_have_toolchain_covers_rust_tier():
    rep = _w.have_toolchain("rust")
    assert set(rep) == {"wasm-pack"}
    assert set(_w.have_toolchain("node")) == {"node"}


needs_rust_tier = pytest.mark.skipif(
    not (shutil.which("wasm-pack") and shutil.which("node")),
    reason="wasm-pack+node toolchains required for Rust compile+execute")


@needs_rust_tier
def test_rust_build_and_run_end_to_end(tmp_path):
    out, total = _w.generate(STATE, tmp_path / "crate")
    pkg = _w.build_wasm(out, target="nodejs")
    wasm = list(pkg.glob("*.wasm"))
    assert len(wasm) == 1 and wasm[0].stat().st_size > 10_000
    rep = _w.run_wasm(pkg)
    assert rep["ok"] is True
    assert rep["step_count"] == total == 4
    assert rep["frame_chars"] > 200
    assert rep["frame_head"].startswith("<svg")
