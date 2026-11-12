"""Eve live log -- append/read roundtrip, tail order, bad-line counting."""

import json

from eve_unleashed import live_log as _l


def test_emit_read_roundtrip(tmp_path):
    p = str(tmp_path / "live.jsonl")
    _l.emit("testing", "probe", path=p, session="s1", ok=True, n=3)
    out = _l.read_events(p)
    assert out["bad"] == 0
    assert len(out["records"]) == 1
    rec = out["records"][0]
    assert rec["phase"] == "testing" and rec["event"] == "probe"
    assert rec["session"] == "s1" and rec["ok"] is True and rec["n"] == 3
    assert rec["ts"] > 0


def test_tail_order_and_bad_lines(tmp_path, capsys):
    p = str(tmp_path / "live.jsonl")
    for i in range(5):
        _l.emit("evolving", "step", path=p, i=i)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write("not json {\n")
    out = _l.read_events(p)
    assert out["bad"] == 1
    assert [r["i"] for r in out["records"]] == [0, 1, 2, 3, 4]
    assert [r["i"] for r in _l.tail(p, n=2)] == [3, 4]
    shown = capsys.readouterr().out
    assert shown.count("LIVE [evolving] step") == 5


def test_missing_file_reads_empty(tmp_path):
    out = _l.read_events(str(tmp_path / "absent.jsonl"))
    assert out == {"records": [], "bad": 0}
