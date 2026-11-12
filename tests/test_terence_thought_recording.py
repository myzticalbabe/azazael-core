# coding: utf-8
"""Terence's two thought-corpus methods must not silently rot.

TERENCE.py had no test file. Two of its methods were migrated onto the typed
`DirectMemory.record_thought()` / `fetch_thoughts()` API with nothing but
`py_compile` covering the result, and both are load-bearing for Terence's own
learning loop: `_record_outcome` is what teaches it which patterns verify, and
`pattern_scores` is what selects against the ones that do not.

Both are also fail-open by design (`except Exception: pass`), which is exactly
the shape that hides breakage: a wrong call signature, a renamed kwarg, or a
store that rejects the write all produce a quiet no-op rather than a failure.
These tests pin the observable contract — the row that lands, and the score
that comes back — instead of trusting the absence of a traceback.
"""
import os
import sys
import tempfile

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_PROJECT_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


@pytest.fixture(autouse=True)
def isolated_store():
    """Point BOTH memory paths at a throwaway DB for the whole module.

    The write path and the read path resolve storage differently, and
    redirecting only one of them makes writes land in the real corpus while
    reads come back empty — a test that passes for the wrong reason, or fails
    for a reason that has nothing to do with the code under test.

      * `_record_outcome`  -> DirectMemory.record_thought -> `_db_path`, which
        is read from the module constant `DB_PATH` at construction time.
      * `pattern_scores`   -> DirectMemory.fetch_thoughts -> `get_memory_store`.

    So both are patched here.
    """
    import CORE.direct_memory as dm
    import CORE.local_memory as lm
    from pathlib import Path

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    temp_store = lm.LocalMemoryStore(db_path=path)
    saved_path = dm.DB_PATH
    saved_getter = lm.get_memory_store
    dm.DB_PATH = Path(path)
    lm.get_memory_store = lambda *a, **k: temp_store
    try:
        yield temp_store
    finally:
        dm.DB_PATH = saved_path
        lm.get_memory_store = saved_getter
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(path + suffix)
            except OSError:
                pass


def _load_terence():
    """TERENCE lives outside the importable package root."""
    import importlib.util
    path = os.path.join(_PROJECT_ROOT, "src", "UTILITIES", "TERENCE",
                        "TERENCE.py")
    spec = importlib.util.spec_from_file_location("terence_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fix(error_type="undefined_name", file="a.py", line=42,
         original="x == None", fixed="x is None", description="d"):
    return _load_terence().Fix(file=file, line=line, error_type=error_type,
                               original=original, fixed=fixed,
                               description=description)


# ── _record_outcome ────────────────────────────────────────────────────────

def test_record_outcome_writes_a_readable_thought_row():
    """The row must be readable by the consumers, not merely written."""
    from CORE.direct_memory import THOUGHT_EVENT
    from CORE.local_memory import get_memory_store
    terence = _load_terence()
    t = terence.TERENCE()
    t._record_outcome(_fix(), ok=True, reason="py_compile clean")

    rows = get_memory_store().query_memories(event_type=THOUGHT_EVENT, limit=1)
    assert rows, "no thought row was written"
    r = rows[0]
    assert r["source"] == "Terence"
    assert r["severity"] == "info"
    d = r["data"]
    if isinstance(d, str):
        import json
        d = json.loads(d)
    # pattern_scores() matches this exact shape; if it drifts, Terence's own
    # self-evaluation silently returns {}.
    assert d["task"] == "auto-fix undefined_name at a.py:42"
    assert d["success"] is True
    assert "py_compile clean" in d["reasoning"]
    assert "rollback" not in d["actions"]
    assert "keep" in d["actions"]
    assert str(d["session_id"]).startswith("terence-")


def test_record_outcome_marks_failure_and_rollback():
    from CORE.direct_memory import THOUGHT_EVENT
    from CORE.local_memory import get_memory_store
    terence = _load_terence()
    terence.TERENCE()._record_outcome(_fix(), ok=False, reason="verify broke")
    r = get_memory_store().query_memories(event_type=THOUGHT_EVENT, limit=1)[0]
    assert r["severity"] == "warning"
    d = r["data"]
    if isinstance(d, str):
        import json
        d = json.loads(d)
    assert d["success"] is False
    assert "rollback" in d["actions"]


def test_record_outcome_truncates_long_spans():
    """original/fixed are capped at 120 by the caller; verify the cap holds."""
    from CORE.direct_memory import THOUGHT_EVENT
    from CORE.local_memory import get_memory_store
    terence = _load_terence()
    terence.TERENCE()._record_outcome(
        _fix(original="A" * 500, fixed="B" * 500), ok=True, reason="r")
    d = get_memory_store().query_memories(event_type=THOUGHT_EVENT, limit=1)[0]["data"]
    if isinstance(d, str):
        import json
        d = json.loads(d)
    assert "A" * 120 in d["reasoning"]
    assert "A" * 121 not in d["reasoning"]


def test_record_outcome_is_fail_open_not_fail_loud():
    """A broken store must not raise — that is the module's documented rule.

    Asserted by breaking the exact call the method makes, so the except path
    is really executed rather than merely present in the source.
    """
    from CORE.direct_memory import DirectMemory
    terence = _load_terence()
    saved = DirectMemory.record_thought

    def boom(self, *a, **k):
        raise RuntimeError("store is down")

    DirectMemory.record_thought = boom
    try:
        terence.TERENCE()._record_outcome(_fix(), ok=True, reason="r")
    finally:
        DirectMemory.record_thought = saved


# ── pattern_scores ─────────────────────────────────────────────────────────

def test_pattern_scores_reads_back_what_record_outcome_wrote(isolated_store):
    """The write path and the read path must agree on the task format."""
    terence = _load_terence()
    t = terence.TERENCE()
    t._record_outcome(_fix(error_type="undefined_name"), ok=True, reason="r")
    t._record_outcome(_fix(error_type="undefined_name"), ok=False, reason="r")
    t._record_outcome(_fix(error_type="bare_except"), ok=True, reason="r")

    scores = t.pattern_scores(min_samples=1)
    assert "undefined_name" in scores, (
        "pattern_scores did not see rows this test just wrote: %r" % scores)
    assert scores["undefined_name"]["n"] == 2
    assert scores["bare_except"]["n"] == 1
    for stats in scores.values():
        assert 0.0 <= stats["rate"] <= 1.0
        assert stats["ok"] <= stats["n"]


def test_pattern_scores_fails_open_on_an_unreachable_store():
    """documented contract: empty dict, never an exception."""
    from CORE.direct_memory import DirectMemory
    terence = _load_terence()
    saved = DirectMemory.fetch_thoughts

    def boom(self, *a, **k):
        raise RuntimeError("store is down")

    DirectMemory.fetch_thoughts = boom
    try:
        assert terence.TERENCE().pattern_scores(min_samples=1) == {}
    finally:
        DirectMemory.fetch_thoughts = saved


def test_pattern_scores_honours_min_samples():
    """Below the floor a pattern is not reported.

    Found by this file, not by inspection: `min_samples` was accepted and
    never applied, so a pattern seen once and failed reported rate=0.0 and
    became a selection target on a single observation.
    """
    terence = _load_terence()
    t = terence.TERENCE()
    t._record_outcome(_fix(error_type="rare_pattern"), ok=False, reason="r")
    assert "rare_pattern" not in t.pattern_scores(min_samples=5), (
        "a one-observation pattern was reported as a 0.0 rate")
    assert "rare_pattern" in t.pattern_scores(min_samples=1), (
        "the floor must not become a blanket filter")


def test_pattern_scores_survives_a_zero_min_samples():
    """A caller passing 0 must not divide by zero or drop everything."""
    terence = _load_terence()
    t = terence.TERENCE()
    t._record_outcome(_fix(error_type="whatever"), ok=True, reason="r")
    scores = t.pattern_scores(min_samples=0)
    assert scores.get("whatever", {}).get("n") == 1


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
