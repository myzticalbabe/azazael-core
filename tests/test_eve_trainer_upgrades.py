"""Null control + pinned oracle for the trainer. No network in this file.

The null control is evolution's negative control (A-4's rule: a gate
nobody has seen fail is an assertion). The fixture is the VERIFY ORACLES
mandate applied to our own fitness: deterministic given the file.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eve_unleashed.self_modifying_worms import WormDNA
from eve_unleashed.trainer import (
    TrainConfig,
    null_control,
    run_url,
    score_genome,
)

PINNED = {
    "https://example.com": {"state": "ok", "rung": "commoncrawl", "kind": "x"},
    "https://www.python.org": {"state": "ok", "rung": "commoncrawl", "kind": "x"},
    "https://en.wikipedia.org/wiki/Python_(programming_language)":
        {"state": "ok", "rung": "archive", "kind": "x"},
    "https://httpbin.org/status/404":
        {"state": "blocked", "rung": "commoncrawl", "kind": "x"},
    "http://unseen-train.invalid/":
        {"state": "blocked", "rung": "commoncrawl", "kind": "x"},
    "https://www.ietf.org/rfc/rfc2616.txt":
        {"state": "ok", "rung": "commoncrawl", "kind": "x"},
}


def _cfg(tmp_path, seed=316, name="null"):
    log = str(tmp_path / (name + "-train.jsonl"))
    return TrainConfig(gens=1, pop=4, seed=seed, fresh=True,
                       log_path=log,
                       pop_path=str(tmp_path / (name + "-pop.json")),
                       cap_s=1.0, fixture_data=dict(PINNED))


def _dna(i=0):
    return WormDNA(worm_id="t-%d" % i, worm_type="fetcher",
                   aggressiveness=0.2, min_confidence=0.9,
                   cooldown_s=0.0, mutation_rate=0.15, hunches=[])


def test_fixture_replay_returns_pinned_outcome_no_network(tmp_path):
    import json
    cfg = _cfg(tmp_path)
    ok, _hit, _dt, _hunch = run_url(_dna(), "https://example.com",
                                    cfg.log_path, fixture=cfg.fixture_data)
    rec = json.loads(open(cfg.log_path, encoding="utf-8").read().strip().splitlines()[-1])
    assert ok is True and rec.get("replay") is True


def test_fixture_replay_is_deterministic(tmp_path):
    # Separate logs: the second scoring must not learn from the first.
    # Round to 3dp: wall-clock resource_usage carries timer noise (~1us)
    # under fixture replay; outcomes (hits/ok) are the pinned part.
    assert (round(score_genome(_dna(), _cfg(tmp_path, name="a")).score(), 3)
            == round(score_genome(_dna(), _cfg(tmp_path, name="b")).score(), 3))


def test_null_control_beats_nothing_and_reports_budget(tmp_path):
    res = null_control(_cfg(tmp_path, seed=99), 4)
    assert res["budget"] == 4 and len(res["scores"]) == 4
    assert res["best"] == max(res["scores"])
    assert res["best"] >= res["mean"]


def test_null_control_same_seed_same_ruler(tmp_path):
    # NOTE: separate logs per run -- a shared log would teach the second
    # run (dream-to-gene flow), and learning is the opposite of a control.
    a = null_control(_cfg(tmp_path, seed=7, name="a"), 4)["scores"]
    b = null_control(_cfg(tmp_path, seed=7, name="b"), 4)["scores"]
    # 3dp: wall-clock noise under replay, outcomes pinned.
    assert [round(s, 3) for s in a] == [round(s, 3) for s in b]


def test_fixture_writer_hashes_the_file_it_wrote(tmp_path):
    # The 2026-11-07 CRLF defect: doc sha (LF blob) never matched the file
    # (CRLF on Windows). The writer now hashes the file itself.
    from eve_unleashed.trainer import _write_fixture_doc
    from eve_unleashed.honey import sha_file
    p = str(tmp_path / "w.json")
    doc = _write_fixture_doc({"urls": ["u"], "outcomes": {"u": {"state": "ok"}}}, p)
    assert doc["sha256"] == sha_file(p)
    raw = open(p, "rb").read()
    assert b"\r" not in raw  # no platform translation in the pin
