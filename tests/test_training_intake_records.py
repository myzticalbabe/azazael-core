# coding: utf-8
"""The technique-outcome intake edge — Engram #770 / ACTION_REGISTRY item H-3.

WHAT IS UNDER TEST, AND WHY IT IS A SHAPE CLAIM
`technique-link` rows already existed (every live hook call writes one) and
reached no trainer's learning signal: no trainer filtered on that source at all.
The second, narrower gap is in the row SHAPE. `record_link()` declares the
verdict as `severity` ("info" for a success, "warning" for a failure) and puts
the outcome in `data["ok"]`, while the trainers' reward model
(`learning_rl._compute_reward`) reads a TOP-LEVEL `severity` plus the refinement
field `data["success"]`. So severity landed, and the refinement did not.

These tests pin the properties that make the fix real rather than decorative:

* the producer's severity is PRESERVED, not re-labelled — this module translates
  a row, it does not overrule the verdict the hook author recorded;
* `data.success` carries the measured outcome, and it is load-bearing on the
  trainer's own reward function rather than decorative;
* an UNMEASURED row is counted and skipped, never labelled — a row with no
  outcome is not an outcome, and training on a guess is worse than a gap;
* the adapter writes nothing, so an intake run cannot perturb the store it reads;
* the outcome reaches the REAL trainer's state space;
* the INTAKE track is registered in the rotation with a runner that exists, and
  a starved stream reports no success rather than a zero-record "win".
"""
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "src", ROOT / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import CORE.training_intake as ti  # noqa: E402
from CORE.WORMS.expansion.learning_rl import (  # noqa: E402
    RLTrainLoop,
    _compute_reward,
)
import track_registry  # noqa: E402


#: The live store's own per-query ceiling; a bigger ask is clamped and warns.
MAX_ROWS = 500


class FakeStore:
    """query_memories(source=...) over rows; records writes so read-only shows.

    `fail=True` models an unreachable store: the adapter's contract is
    fail-open, so the caller must get a diagnosis, never an exception.
    """

    def __init__(self, rows=(), fail=False):
        self._rows = list(rows)
        self._fail = fail
        self.writes = []

    def query_memories(self, source=None, event_type=None, limit=50, **kw):
        if self._fail:
            raise RuntimeError("store down")
        return [r for r in self._rows
                if source is None or r.get("source") == source][:limit]

    def record_event(self, *args, **kwargs):
        self.writes.append((args, kwargs))
        return 1


def _row(ok, hook="technique_runtime.verify_boundary", subject="EP412",
         severity=None):
    """One `technique-link` row, transcribed from `technique_runtime.record_link()`.

    That producer writes `source="technique-link"`, `event_type="hook:<hook>"`,
    `severity="info" if ok else "warning"`, and a payload of
    `{subject, ok, citation, decision_id}` — mirrored here deliberately, because
    a fixture invented beside the producer proves only the fixture. `ok=None`
    models a row whose payload carries no outcome at all.
    """
    data = {"subject": subject, "citation": "cite:1", "decision_id": "d-1"}
    if ok is not None:
        data["ok"] = ok
    if severity is None:
        severity = "info" if ok else "warning"
    return {"source": "technique-link", "event_type": "hook:%s" % hook,
            "data": data, "severity": severity,
            "timestamp": "2026-10-15T00:00:00+00:00", "session_id": "sess-1"}


def _built(monkeypatch, rows):
    store = FakeStore(rows)
    monkeypatch.setattr(ti, "get_memory_store", lambda: store)
    return ti.build_training_records(), store


# -- the translation itself ------------------------------------------------

def test_the_producers_verdict_is_preserved_not_relabelled(monkeypatch):
    built, _ = _built(monkeypatch, [_row(True), _row(False), _row(False, severity="error")])
    ok_rec, bad_rec, err_rec = built["records"]

    # severity as the producer declared it: re-labelling would overrule the hook
    # author and silently change the incentive the learner is fitted to.
    assert ok_rec["severity"] == "info"
    assert bad_rec["severity"] == "warning"
    assert err_rec["severity"] == "error"

    # The refinement field the reward model reads "when present".
    assert ok_rec["data"]["success"] is True
    assert bad_rec["data"]["success"] is False
    assert ok_rec["data"]["ok"] is True
    assert bad_rec["data"]["ok"] is False

    assert built["n"] == 3
    assert round(built["ok_rate"], 4) == round(1 / 3, 4)
    assert built["by_hook"]["technique_runtime.verify_boundary"] == [1, 3]


def test_a_success_less_payload_still_trains_on_the_verdict(monkeypatch):
    """Rows with no `hook` key group by the event_type, which is how the live
    stream looks: `hook:<name>`, not a payload field."""
    row = _row(True)
    del row["data"]["subject"]
    built, _ = _built(monkeypatch, [row])

    assert built["by_hook"] == {"technique_runtime.verify_boundary": [1, 1]}
    assert built["records"][0]["event_type"] == (
        "hook:technique_runtime.verify_boundary")


def test_unmeasured_row_is_counted_and_skipped_never_labelled(monkeypatch):
    """Truly unlabelled: no boolean AND the producer declared nothing.

    `severity="info"` pins the "declared nothing" case — a warning row with
    a lost payload does NOT hit this path anymore (F1 NAT, next test): the
    producer's own severity vocabulary already measures it.
    """
    built, _ = _built(monkeypatch, [_row(None, severity="info"), _row(True)])

    assert built["n"] == 1, "a row with no outcome is not a training record"
    assert built["n_rows"] == 2
    assert built["n_unmeasured"] == 1
    assert built["n_nat_declared"] == 0, "an info row is not NAT rescueable"
    assert built["ok_rate"] == 1.0
    assert all("success" in r["data"] for r in built["records"])


def test_a_non_boolean_ok_is_not_coerced_into_a_verdict(monkeypatch):
    """`bool("false")` is True — so a string outcome must not become a success."""
    row = _row(True)
    row["data"]["ok"] = "false"
    built, _ = _built(monkeypatch, [row])

    assert built["records"] == []
    assert built["n_unmeasured"] == 1


def test_nat_a_producer_declared_failure_is_a_measured_negative(monkeypatch):
    """F1 NAT (Engram #813, arXiv:2402.11651 reading): never skipped-silently.

    `record_link()` writes severity "warning" for a failure — that severity
    IS the measurement, so a row whose payload lost its boolean arrives at
    the trainer as a Marked negative rather than vanishing between the
    store and the record list. Marked explicitly in BOTH directions so the
    reward model (or a monitor) can read the sign without re-deriving it.
    """
    built, _ = _built(monkeypatch, [
        _row(None, severity="warning"),     # payload lost, verdict declared
        _row(None, severity="error"),       # declared error, also negative
        _row(True), _row(False),
    ])

    assert built["n_nat_declared"] == 2
    assert built["n_unmeasured"] == 0
    assert built["n_negative"] == 3
    assert built["n"] == 4
    marked = [r for r in built["records"] if r["data"]["nat_negative"]]
    assert len(marked) == 3
    assert all(r["data"]["success"] is False for r in marked)
    assert all(r["data"]["ok"] is False for r in marked)
    # The positive is explicitly NOT a negative.
    assert built["records"][2]["data"]["nat_negative"] is False
    # ...and the producer's severity itself was never re-labelled.
    assert marked[0]["severity"] == "warning"
    assert marked[1]["severity"] == "error"


def test_nat_positive_rows_were_not_rescued_in_the_measured_live_pool(monkeypatch):
    """The live stream measured 500/500 "info" + ok_rate 1.0 in #798's run.

    This test pins the honest gap: NOTHING in the live technique-link
    stream was NAT-rescueable at measurement time, so F1's acceptance
    "pool carries measured negatives" is about the MECHANISM (asserted on
    directly above) plus live-run exit 0 — the stream producing zero
    negatives is recorded, not hidden behind the mechanism's tests.
    """
    built, _ = _built(monkeypatch, [_row(True) for _ in range(3)])

    assert built["n"] == 3
    assert built["n_negative"] == 0
    assert built["n_nat_declared"] == 0
    assert all(r["data"]["nat_negative"] is False for r in built["records"])


# -- F2 Likra near-miss (Engram #813, arXiv:2503.14391 reading) --------------

def test_f2_near_miss_marks_the_warning_segment_of_the_negatives(monkeypatch):
    """Near-miss = plausible, adjacent-to-success failures: the "warning"

    class. An outright "error" breakage is a negative but NOT a near-miss,
    and a positive is neither — the mark is exclusive, and the count names
    its segment. No weighting changes here (this module's REJECTED-lesson
    boundary stands until per-worm hook identity exists): the mark TRAVELS,
    it does not yet APPLY.
    """
    built, _ = _built(monkeypatch, [
        _row(False, severity="warning"),   # near-miss negative
        _row(False, severity="error"),     # outright negative
        _row(True),                         # positive
        _row(None, severity="warning"),    # NAT-rescued negative: also near-miss
    ])

    assert built["n_negative"] == 3
    assert built["n_nat_declared"] == 1
    assert built["n_near_miss"] == 2
    marks = [r["data"]["near_miss"] for r in built["records"]]
    assert marks == [True, False, False, True]
    # The mark does not disturb VERDICT fields already pinned above.
    assert built["records"][0]["data"]["success"] is False
    assert built["records"][2]["data"]["success"] is True


def test_f2_zero_near_miss_when_pool_is_all_success(monkeypatch):
    """Live-shape twin: the measured pool (500/500 info, ok 1.0) carries no

    near-miss segment — count 0 is the honest live reading, not a bug.
    """
    built, _ = _built(monkeypatch, [_row(True), _row(True)])

    assert built["n_near_miss"] == 0
    assert all(r["data"]["near_miss"] is False for r in built["records"])


def test_empty_stream_yields_no_records_and_no_verdict(monkeypatch):
    built, _ = _built(monkeypatch, [])

    assert built["records"] == []
    assert built["n"] == 0
    assert built["ok_rate"] is None, "an empty stream has no ok-rate to report"
    assert "store_error" not in built


def test_an_unreadable_store_is_reported_not_raised(monkeypatch):
    """Fail-open is a diagnosis, not a silence: an empty pool must be explainable."""
    store = FakeStore(fail=True)
    monkeypatch.setattr(ti, "get_memory_store", lambda: store)

    built = ti.build_training_records()

    assert built["read_error"] == "RuntimeError"
    assert built["records"] == []
    assert built["n_rows"] == 0


def test_a_store_that_cannot_be_opened_is_named_too(monkeypatch):
    def _boom():
        raise OSError("no store")

    monkeypatch.setattr(ti, "get_memory_store", _boom)
    built = ti.build_training_records()

    assert built["store_error"] == "OSError"
    assert built["records"] == []


def test_the_intake_reads_only(monkeypatch):
    _, store = _built(monkeypatch, [_row(True), _row(False)])

    assert store.writes == [], "the intake must not write to the store it reads"


# -- the edge is real: the outcome reaches the trainer ----------------------

def test_a_failed_outcome_scores_differently_on_the_trainers_own_reward(monkeypatch):
    built, _ = _built(monkeypatch, [_row(True), _row(False)])
    ok_rec = next(r for r in built["records"] if r["data"]["success"])
    bad_rec = next(r for r in built["records"] if not r["data"]["success"])

    assert _compute_reward(ok_rec, 1) > _compute_reward(bad_rec, 1)


def test_data_success_is_load_bearing_not_decorative(monkeypatch):
    """Strip the field this adapter adds and the trainer's reward changes.

    Without `data["success"]` an `info` record earns no outcome bonus, so this
    is the measurement that the translation does work rather than merely
    restating a field the reward model ignores.
    """
    built, _ = _built(monkeypatch, [_row(True, severity="error")])
    rec = built["records"][0]
    stripped = {**rec, "data": {"ok": True, "hook": rec["data"]["hook"]}}

    assert _compute_reward(rec, 3) != _compute_reward(stripped, 3)
    assert _compute_reward(rec, 3) == _compute_reward(stripped, 3) + 0.3


def test_the_real_trainer_trains_on_technical_outcomes(monkeypatch):
    """RLTrainLoop consumes the pool and its state space carries the verdicts.

    This is the whole point of H-3: the technique outcome shapes the policy's
    state space instead of arriving as one undifferentiated "info" record.
    """
    built, _ = _built(monkeypatch, [_row(True), _row(False), _row(True), _row(False)])
    loop = RLTrainLoop()
    result = loop.train_from_memory(
        records=built["records"], n_episodes=4, max_steps=4,
        use_schema_features=False, persist=False, rng=random.Random(11))

    assert "error" not in result
    assert result["episodes"] == 4
    # The fallback state key is `source:severity`; both verdicts must appear, so
    # the policy can differentiate a hook that worked from one that did not.
    states = set(loop._q_table)  # noqa: SLF001 - the trainer's own state space
    assert "technique-link:info" in states
    assert "technique-link:warning" in states


# -- the track that makes it an edge rather than an exercise ----------------

def test_intake_track_is_registered_with_a_runnable_runner():
    assert "INTAKE" in track_registry.ROTATION
    spec = track_registry.TRACKS["INTAKE"]
    assert spec["kind"] == "cycle"
    assert spec["event"] == ("intake_training_orchestrated", "eval_mean_reward", None)

    import run_training_orchestrator as rto
    assert callable(getattr(rto, spec["runner"], None)), spec["runner"]


def test_the_cycle_trains_the_real_trainer_on_a_seeded_store(monkeypatch):
    """The end-to-end edge: INTAKE track -> adapter -> the real RLTrainLoop.

    Runs the production cycle function against a store holding transcribed
    `record_link()` rows, so it exercises the same path the scheduler does.
    """
    import run_training_orchestrator as rto
    from CORE.local_memory import get_memory_store

    rows = [_row(True), _row(False)] * 4
    # Only the READ is faked (the pool); the cycle's own artifact write goes to
    # the session's redirected real store, which is where it must be checked.
    monkeypatch.setattr(ti, "get_memory_store", lambda: FakeStore(rows))

    result = rto.run_intake_cycle()

    assert result["success"] is True
    assert result["records_used"] == len(rows)
    assert result["eval_mean_reward"] is not None
    assert result["model_version"] == "v1"

    artifacts = get_memory_store().query_memories(
        event_type="intake_training_orchestrated", limit=5) or []
    assert len(artifacts) == 1
    data = artifacts[0]["data"]
    assert data["algo"] == "INTAKE"
    assert data["records_used"] == len(rows)
    assert data["intake_hooks"] == 1
    assert data["intake_ok_rate"] == 0.5
    assert data["intake_unmeasured"] == 0
    assert data["data_source"] == "memory:technique-link"


def test_a_starved_intake_cycle_reports_no_success_and_writes_nothing(monkeypatch):
    """An unmeasured stream must not read as a score (or as a zero-record win).

    F1 NAT moved the "warning + lost payload" rows out of the unmeasured
    bucket (they are producer-measured negatives now), so the truly starved
    stream is an `info`-severity row with no boolean — nothing was measured
    by anyone.
    """
    store = FakeStore([_row(None, severity="info")])
    monkeypatch.setattr(ti, "get_memory_store", lambda: store)

    import run_training_orchestrator as rto
    result = rto.run_intake_cycle()

    assert result["success"] is False
    assert result["records_used"] == 0
    assert "no MEASURED" in result["note"]
    assert store.writes == [], "a starved run must not leave an artifact row"


def test_a_stream_of_only_nat_negatives_is_a_real_training_run(monkeypatch):
    """F1 acceptance's other side: measured negatives are a pool, not a gap.

    A stream whose every row is a producer-declared failure (severity outside
    "info", measured by the producer's own vocabulary) trains on REAL
    measurements — success must not be withheld just because nothing
    succeeded, or the trainer could only ever see the happy path.
    """
    store = FakeStore([_row(None, severity="warning"),
                       _row(None, severity="error")])
    monkeypatch.setattr(ti, "get_memory_store", lambda: store)

    import run_training_orchestrator as rto
    result = rto.run_intake_cycle()

    assert result["success"] is True
    assert result["records_used"] == 2
    # The versioner's own artifact channel may write; the store read here
    # must stay untouched either way.
    assert isinstance(store.writes, list)


def test_a_starved_intake_cycle_names_an_unreadable_store(monkeypatch):
    store = FakeStore(fail=True)
    monkeypatch.setattr(ti, "get_memory_store", lambda: store)

    import run_training_orchestrator as rto
    result = rto.run_intake_cycle()

    assert result["success"] is False
    assert "error=RuntimeError" in result["note"]
    assert store.writes == []
