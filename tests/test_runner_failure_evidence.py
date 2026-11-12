# coding: utf-8
"""Failure-evidence invariants for the chain/runner/instrument lane (2026-10-14).

A runner that FAILS must leave evidence a human can read afterwards. The three
rules pinned here were each violated before, and each violation made one real
failure indistinguishable from a different one:

  * a timeout must produce an authoritative rc, not None -- every consumer of
    the mutation ledger reads rc, and None reads as "unknown", which is the one
    thing a failure record must not be;
  * the partial stdout/stderr a timed-out command already produced must be
    persisted (TimeoutExpired carries it; it is the only evidence a timeout
    leaves), instead of being replaced by the word "TIMEOUT";
  * a ledger row must always carry a `verdict` (readers group by it, and a row
    without one is invisible) and a torn line must not make the whole attempt
    history unreadable.

These drive the real functions with real subprocesses and real temp files.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tools"))

import chain20  # noqa: E402
import run_azazael_chain  # noqa: E402
import run_full_suite  # noqa: E402
import self_modify  # noqa: E402

# A child that emits evidence on BOTH streams, then hangs. The sleep is what
# the timeout interrupts; the prints are what must survive it.
_CHATTY_HANG = ("import sys, time; "
                "print('PARTIAL-OUT', flush=True); "
                "sys.stderr.write('PARTIAL-ERR\\n'); sys.stderr.flush(); "
                "time.sleep(30)")


def test_self_modify_timeout_rc_is_authoritative_and_keeps_evidence():
    res = self_modify._run([sys.executable, "-c", _CHATTY_HANG], timeout=2)
    assert res["timeout"] is True
    assert res["rc"] == self_modify.RC_TIMEOUT, "rc must be a code, not None"
    assert "PARTIAL-OUT" in res["out"], res
    assert "PARTIAL-ERR" in res["err"], res


def test_self_modify_non_timeout_keeps_the_real_returncode():
    res = self_modify._run([sys.executable, "-c", "raise SystemExit(3)"], timeout=30)
    assert res["rc"] == 3
    assert res["timeout"] is False


def test_ledger_row_always_has_verdict_and_a_torn_line_does_not_break_readers(
        tmp_path, monkeypatch):
    ledger = tmp_path / "mutation_ledger.jsonl"
    monkeypatch.setattr(self_modify, "LEDGER", ledger)
    # Force the DirectMemory mirror to fail so discipline rule 3 is exercised
    # too -- WITHOUT writing an event into the real store.
    monkeypatch.setitem(sys.modules, "CORE.direct_memory", None)

    self_modify.record({"verdict": "kept"})
    self_modify.record({})  # no verdict supplied, on purpose

    rows = [json.loads(x) for x in
            ledger.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert rows[0]["verdict"] == "kept"
    assert rows[1]["verdict"] == "unknown", \
        "a row without a verdict is invisible to the reader"
    assert "direct_memory_error" in rows[0], \
        "a mirror failure must be recorded into the row, not swallowed"

    # A torn append (a crash mid-write) must not make the history unreadable:
    # the reader has to skip the damaged line and keep every good one.
    with ledger.open("a", encoding="utf-8") as fh:
        fh.write('{"verdict": "kept", "trunc"')
    good, torn = [], []
    for line in ledger.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            good.append(json.loads(line))
        except ValueError as exc:
            torn.append(str(exc))  # recorded as damaged; never fatal
    assert [r["verdict"] for r in good] == ["kept", "unknown"]
    assert len(torn) == 1, "the torn line must be detected, not silently ignored"


def test_chain20_step_records_a_numeric_returncode(tmp_path, monkeypatch):
    log = tmp_path / "chain20.log"
    monkeypatch.setattr(chain20, "LOG", str(log))
    ok = chain20.step("t-fail", ["-c", "raise SystemExit(3)"], binary=sys.executable)
    assert ok is False
    line = log.read_text(encoding="utf-8")
    assert "rc=3" in line, line
    assert "rc=FAIL" not in line, "the rc field must hold a code, not a verdict"


def test_chain20_step_timeout_records_124_with_partial_evidence(tmp_path, monkeypatch):
    log = tmp_path / "chain20.log"
    monkeypatch.setattr(chain20, "LOG", str(log))
    monkeypatch.setattr(chain20, "STEP_TIMEOUT", 2)
    ok = chain20.step("t-hang", ["-c", _CHATTY_HANG], binary=sys.executable)
    assert ok is False
    line = log.read_text(encoding="utf-8")
    assert "rc=124" in line, line
    assert "PARTIAL-OUT" in line, line


def test_run_full_suite_timeout_text_normalises_bytes():
    assert run_full_suite._timeout_text(b"PARTIAL-OUT") == "PARTIAL-OUT"
    assert run_full_suite._timeout_text(None) == ""
    assert run_full_suite._timeout_text("already-text") == "already-text"


def test_start_slow_failure_logs_the_reason(monkeypatch):
    seen = []
    monkeypatch.setattr(run_azazael_chain, "_log", lambda line: seen.append(line))
    # An unknown job kind cannot be launched; the REASON must be recorded --
    # the caller only logs a bare "START-FAIL <job>", which says nothing about
    # why.
    pid = run_azazael_chain._start_slow("bogus", ("x", 1, "nope", "target", ()))
    assert pid is None
    assert any("START-FAIL-EVIDENCE" in line for line in seen), seen


def test_ledger_rotation_bounds_growth_without_hiding_history(tmp_path, monkeypatch):
    """Rotation must bound the file WITHOUT hiding rows from the readers.

    refresh_all.attempts() reads this ledger so a reverted attempt is not
    re-derived, local_inference.scoreboard() counts verdicts across it, and
    meta_evolution_cycle._attempted_ids() reads it so a consumed candidate is
    never re-selected. A rotation those readers cannot see would silently erase
    the memory the ledger exists to hold -- which is why the readers were
    changed in the same commit as the rotation.
    """
    monkeypatch.setattr(self_modify, "LEDGER", tmp_path / "mutation_ledger.jsonl")
    monkeypatch.setattr(self_modify, "LEDGER_GENERATIONS", 3)
    monkeypatch.setattr(self_modify, "LEDGER_MAX_BYTES", 300)
    monkeypatch.setitem(sys.modules, "CORE.direct_memory", None)

    for i in range(40):
        self_modify.record({"verdict": "kept" if i % 2 else "reverted", "seq": i})

    gens = list(tmp_path.glob("mutation_ledger.jsonl*"))
    assert len(gens) <= self_modify.LEDGER_GENERATIONS + 1, \
        "generation count must be bounded, or rotation bounds nothing"
    assert all(p.stat().st_size <= self_modify.LEDGER_MAX_BYTES + 2000
               for p in gens), "a generation must not run away"

    rows, torn = self_modify.read_rows()
    assert torn == 0
    seqs = [r["seq"] for r in rows]
    assert max(seqs) == 39, "the live rows must still be readable"
    assert seqs == sorted(seqs), "rows must read oldest-generation-first"
    assert len(rows) >= 10, "rotation must keep a usable window, not one row"
    # Dropping the OLDEST generation is the only, bounded loss -- so the newest
    # rows are never the ones discarded.
    assert min(seqs) > 0, "the LIVE data must survive a rotation"
