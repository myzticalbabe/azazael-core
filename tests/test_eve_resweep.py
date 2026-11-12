"""The sweep passes live -- and proves it can bite.

The tamper controls mutate a COPY of the real manifest in tmp (same
shape, hostile bytes): a flipped blob digest and a bumped diameter must
both FAIL with the liar named. A detector that cannot fail on demand is
a decoration.
"""
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eve_unleashed.resweep import sweep

MAN = "state/honey/honey.json"


def _mutated(tmp_path, fn):
    man = copy.deepcopy(json.load(open(MAN, encoding="utf-8")))
    fn(man)
    p = tmp_path / "honey.json"
    p.write_text(json.dumps(man), encoding="utf-8")
    return str(p)


def test_sweep_passes_live_five_for_five():
    s = sweep(MAN)
    assert s["verdict"] == "PASS", s
    assert len(s["chains"]) == 5
    assert all(c["pass"] for c in s["chains"].values())


def test_sweep_names_the_tampered_blob(tmp_path):
    def flip(m):
        m["artifacts"][next(iter(m["artifacts"]))]["sha"] = "0" * 64
    p = _mutated(tmp_path, flip)
    s = sweep(p)
    assert s["verdict"] == "FAIL", s
    assert not s["chains"]["funnel_seal"]["pass"]
    assert not s["chains"]["root_audit"]["pass"]


def test_sweep_catches_the_stale_number(tmp_path):
    def bump(m):
        m["resilience"]["reports"]["dodecahedron"]["diameter"] += 1
    s = sweep(_mutated(tmp_path, bump))
    assert s["verdict"] == "FAIL", s
    det = s["chains"]["resilience_numbers"]["detail"]
    assert "dodecahedron.diameter" in det
