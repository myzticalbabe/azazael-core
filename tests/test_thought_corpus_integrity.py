# coding: utf-8
"""Thought-corpus integrity: a trace that looks recorded must BE in the loop.

Two silent failures motivated this file. Neither raised an error; both made
the learning loop train on less data than it believed it had.

  1. The clamp. `query_memories` hard-clamps every call to
     MAX_QUERY_LIMIT (500) and only logs a warning the caller never sees.
     Result is newest-first, so the clamp evicts the OLDEST rows -- which are
     the minority families carrying the only success/fail contrast. The DPO
     pairing window had been widened 120 -> 600 precisely to stop one loud
     family evicting the contrast, and the clamp silently capped it back to
     500, reopening the same defect through a different door.

  2. The stringly-typed writer. `thought_process` was a bare literal at five
     call sites, selected by equality by every consumer. A misspelling wrote a
     row that read back fine, looked recorded, and was ignored by the reward
     trainer, the DPO pairing and the monitor. `record_thought()` makes the
     event type and the payload shape a single typed contract.
"""
import json
import os
import sys
import tempfile

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_PROJECT_ROOT, "src"),):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _temp_store():
    """A LocalMemoryStore on a throwaway DB, isolated from the real corpus."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    from CORE.local_memory import LocalMemoryStore
    return LocalMemoryStore(db_path=path), path


@pytest.fixture(autouse=True)
# start of the recorder guard release
#
# The recorder guard is a production flood policy: 8 rows/s, burst 200, applied
# to every event_type it does not name. It is process-wide, so once one test
# writes 600 rows the bucket is empty for every test after it and their writes
# are dropped silently -- measured 2026-11-02: 600 writes landed 205 rows, and a
# later test's 50 writes landed 0. These tests measure the CLAMP and the typed
# WRITER, not the throttle, so the fixture has to be able to write. Disabling it
# here does not weaken a single assertion: every assertion about paging,
# truncation and payload shape is unchanged.
#
# The policy question the guard raises is recorded separately (it silently drops
# thought_process rows in production too, which is the defect this file exists
# to catch) and is deliberately NOT decided inside a fixture.
def _recorder_guard_released(monkeypatch):
    from CORE import recorder_guard
    monkeypatch.setattr(recorder_guard, "_DISABLED", True, raising=False)
    recorder_guard.guard().reset()
# end of the recorder guard release


# ── the clamp ──────────────────────────────────────────────────────────────

def test_fetch_all_pages_past_the_clamp():
    store, path = _temp_store()
    try:
        for i in range(600):
            store.record_event(source="T", event_type="thought_process",
                               data={"task": "t%d" % i, "success": True,
                                     "reasoning": "r"})
        clamped = store.query_memories(event_type="thought_process", limit=2000)
        assert len(clamped) == store.MAX_QUERY_LIMIT, (
            "fixture assumption: the clamp should bind at %d, got %d"
            % (store.MAX_QUERY_LIMIT, len(clamped)))
        paged = store.fetch_all_memories(limit=2000,
                                         event_type="thought_process")
        assert len(paged) == 600, "paged read lost rows: %d" % len(paged)
    finally:
        os.unlink(path)


def test_the_clamp_evicts_exactly_the_rows_that_carry_signal():
    """A 500-row cap on newest-first drops the oldest minority family."""
    store, path = _temp_store()
    try:
        # The rare contrast is written FIRST so it is the OLDEST, and
        # query_memories returns newest-first -- so the 500-row clamp keeps the
        # 600 loud rows written after it and evicts the contrast. That is the
        # exact shape of the 2026-09-28 defect: one high-volume family pushes
        # the only success/fail pair out of a newest-first window.
        store.record_event(source="T", event_type="thought_process",
                           data={"task": "fix flat-fitness false positive",
                                 "success": True, "reasoning": "r"})
        store.record_event(source="T", event_type="thought_process",
                           data={"task": "fix flat-fitness false negative",
                                 "success": False, "reasoning": "r"})
        for _i in range(600):
            store.record_event(source="T", event_type="thought_process",
                               data={"task": "evolution promotion verdict",
                                     "success": True, "reasoning": "r"})

        clamped = store.query_memories(event_type="thought_process", limit=2000)
        clamped_tasks = {r["data"]["task"] for r in clamped}
        assert "fix flat-fitness false negative" not in clamped_tasks, (
            "fixture wrong: the clamp should have dropped the rare failure")

        paged = store.fetch_all_memories(limit=2000,
                                         event_type="thought_process")
        paged_tasks = {r["data"]["task"] for r in paged}
        assert "fix flat-fitness false negative" in paged_tasks, (
            "the paged read must recover the contrast the clamp dropped")
        assert "fix flat-fitness false positive" in paged_tasks
    finally:
        os.unlink(path)


def test_fetch_all_honours_its_limit_exactly():
    store, path = _temp_store()
    try:
        for i in range(50):
            store.record_event(source="T", event_type="thought_process",
                               data={"task": "t%d" % i, "success": True})
        assert len(store.fetch_all_memories(limit=7,
                                            event_type="thought_process")) == 7
        assert store.fetch_all_memories(limit=0,
                                        event_type="thought_process") == []
    finally:
        os.unlink(path)


def test_fetch_all_stops_early_when_exhausted():
    """A short page means the table is drained -- no extra round trip."""
    store, path = _temp_store()
    try:
        calls = []
        real = store.query_memories

        def counting(**kw):
            calls.append(kw.get("offset", 0))
            return real(**kw)

        store.query_memories = counting
        for i in range(10):
            store.record_event(source="T", event_type="thought_process",
                               data={"task": "t%d" % i, "success": True})
        store.fetch_all_memories(limit=2000, event_type="thought_process")
        assert len(calls) == 1, "should not page past exhaustion: %r" % calls
    finally:
        os.unlink(path)


# ── the typed writer ───────────────────────────────────────────────────────

def test_record_thought_writes_the_shape_consumers_read():
    from CORE.direct_memory import DirectMemory, THOUGHT_EVENT
    from CORE.local_memory import get_memory_store
    assert THOUGHT_EVENT == "thought_process"
    dm = DirectMemory()
    rid = dm.record_thought(
        task="verify the typed writer",
        reasoning="one typed call instead of a bare literal",
        actions=["read", "assert"],
        success=True,
        source="TestSuite",
        tags=["training"],
    )
    assert rid, "record_thought returned no row id"
    row = get_memory_store().query_memories(event_type="thought_process",
                                            limit=1)[0]
    d = row["data"]
    if isinstance(d, str):
        d = json.loads(d)
    for key in ("task", "reasoning", "actions", "success", "session_id"):
        assert key in d, "payload key %r missing -- consumers read it" % key
    assert d["success"] is True
    assert d["task"] == "verify the typed writer"
    assert "thought" in row["tags"] and "chain-of-thought" in row["tags"]


def test_record_thought_marks_failure_by_severity():
    """A failed trace must be queryable as a failure without extra bookkeeping."""
    from CORE.direct_memory import DirectMemory
    from CORE.local_memory import get_memory_store
    DirectMemory().record_thought(task="a failed trace", reasoning="r",
                                  success=False, source="TestSuite")
    row = get_memory_store().query_memories(event_type="thought_process",
                                            limit=1)[0]
    assert row["severity"] == "warning"
    d = row["data"]
    if isinstance(d, str):
        d = json.loads(d)
    assert d["success"] is False


def test_record_thought_never_writes_an_empty_reasoning():
    from CORE.direct_memory import DirectMemory
    from CORE.local_memory import get_memory_store
    DirectMemory().record_thought(task="blank reasoning", reasoning="   ",
                                  source="TestSuite")
    row = get_memory_store().query_memories(event_type="thought_process",
                                            limit=1)[0]
    d = row["data"]
    if isinstance(d, str):
        d = json.loads(d)
    assert d["reasoning"], "an empty reasoning would train on nothing"


def test_record_thought_truncates_long_traces():
    from CORE.direct_memory import DirectMemory, THOUGHT_REASONING_MAX
    from CORE.local_memory import get_memory_store
    DirectMemory().record_thought(task="long", reasoning="x" * 99999,
                                  source="TestSuite")
    row = get_memory_store().query_memories(event_type="thought_process",
                                            limit=1)[0]
    d = row["data"]
    if isinstance(d, str):
        d = json.loads(d)
    assert len(d["reasoning"]) == THOUGHT_REASONING_MAX


_RECORD_FUNCS = {"record", "record_event", "record_thought"}


def _bare_literal_sites(path):
    """Where a file passes 'thought_process' to a recording call as a literal.

    Deliberately AST-based rather than textual. A regex cannot tell a WRITE
    from a READ: `live_monitor.py` runs raw SQL against
    `event_type='thought_process'` and `record_thought.py` mentions the literal
    in its own docstring, and neither is a call site that has drifted. Only a
    keyword/positional argument reaching a recording function is a real risk.
    """
    import ast
    try:
        tree = ast.parse(open(path, encoding="utf-8").read())
    except (OSError, UnicodeDecodeError, SyntaxError):
        return []
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "attr", None) or getattr(func, "id", None)
        if name not in _RECORD_FUNCS:
            continue
        for arg in node.args:
            if (isinstance(arg, ast.Constant) and arg.value == "thought_process"):
                hits.append(arg.lineno)
        for kw in node.keywords:
            if (kw.arg == "event_type" and isinstance(kw.value, ast.Constant)
                    and kw.value.value == "thought_process"):
                hits.append(kw.value.lineno)
    return hits


def test_no_bare_thought_process_literal_remains_a_writer():
    """The regression this file exists for: a call site that drifted back to
    a bare literal and silently left the training loop."""
    offenders = []
    for sub in ("scripts", os.path.join("src", "CORE"),
                os.path.join("src", "UTILITIES")):
        base = os.path.join(_PROJECT_ROOT, sub)
        for dirpath, _dirs, files in os.walk(base):
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                if fn in ("direct_memory.py", "local_memory.py",
                          "test_thought_corpus_integrity.py"):
                    continue
                path = os.path.join(dirpath, fn)
                for lineno in _bare_literal_sites(path):
                    offenders.append("%s:%d" % (path, lineno))
    assert not offenders, (
        "bare 'thought_process' literal at a write site -- use "
        "DirectMemory.record_thought(): %s" % ", ".join(offenders))


def test_the_scan_actually_catches_a_drifted_write_site(tmp_path):
    """Guard the guard: a check that cannot fail is not a check."""
    bad = tmp_path / "drifted.py"
    bad.write_text(
        "def f(store):\n"
        "    store.record('thought_process', {'task': 'x'})\n",
        encoding="utf-8")
    good = tmp_path / "reader.py"
    good.write_text(
        "def f(con):\n"
        "    return con.execute(\n"
        "        \"SELECT 1 FROM memories WHERE event_type='thought_process'\"\n"
        "    )\n",
        encoding="utf-8")
    doc = tmp_path / "documented.py"
    doc.write_text(
        '"""Rows land as event_type=\'thought_process\' from a source."""\n'
        "def f():\n    return None\n",
        encoding="utf-8")
    assert _bare_literal_sites(str(bad)) == [2], "missed a drifted writer"
    assert _bare_literal_sites(str(good)) == [], "SQL read flagged as a writer"
    assert _bare_literal_sites(str(doc)) == [], "docstring flagged as a writer"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
