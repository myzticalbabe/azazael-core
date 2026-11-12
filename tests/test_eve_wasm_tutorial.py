"""G1 test for src/eve_unleashed/wasm_tutorial.py.

Generation only (no cargo in the gate): the crate must carry the real
verdicts inside lib.rs, the host page must reference the crate bundle,
and the log parser must count exactly what a real ablation log holds.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eve_unleashed.wasm_tutorial import generate, parse_ablation_log

FAKE_LOG = ("PROMPT: What is 17 multiplied by 24?\n"
            "  [ANSWERED] groq: 14 chars\n"
            "  [ERROR] gemini: \n"
            "  [n/a] swarm+NSP fallback: 1/2 providers answered\n"
            "ABLATION: 1/2 provider-answers; fallback serves\n")


def test_parse_counts_real_log_shape(tmp_path):
    log = tmp_path / "ablate.log"
    log.write_text(FAKE_LOG, encoding="utf-8")
    state = parse_ablation_log(log)
    assert (state["answered"], state["total"]) == (1, 2), state
    assert state["summary"].startswith("ABLATION:")


def test_generate_embeds_verdicts_and_host(tmp_path):
    state = {"rows": [{"provider": "groq", "verdict": "ANSWERED",
                       "detail": "14 chars"}],
             "answered": 1, "total": 1, "summary": "ABLATION: 1/1"}
    out, total = generate(state, tmp_path / "tut")
    assert total == 3, total  # overview + 1 provider + judgment
    lib = (out / "src" / "lib.rs").read_text(encoding="utf-8")
    assert "groq" in lib and "render_frame" in lib
    html = (out / "index.html").read_text(encoding="utf-8")
    assert "pkg/eve_inner_state.js" in html
    assert "decide('apply-best')" in html and "/decide" in html
    assert (out / "Cargo.toml").read_text(
        encoding="utf-8").count("wasm-bindgen") == 1
