# coding: utf-8
"""Controls for the requiem recorder: it must survive the crash it describes.

The failure this file exists to prevent is a crash handler that itself throws,
or one that writes a cause it cannot know. Both are tested from the outside —
by calling the recorder with the worst inputs available (an unserializable
context, a grave that cannot be opened, a SecurityError-shaped path) and
asserting that a record still comes back and that nothing propagates.
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
#: The same controls run in two places: the source tree (`src/PSYCHE`) and the
#: materialised public tree (`Project_ELIZA/eliza/psyche`). Testing whichever copy
#: is nearest is the point -- otherwise the shipped file is exercised by a test
#: that imported the *other* one and a divergence passes silently.
if (ROOT / "eliza" / "psyche" / "requiem.py").is_file():
    SRC = ROOT
    MODULE = "eliza.psyche.requiem"
else:
    SRC = ROOT / "src"
    MODULE = "PSYCHE.requiem"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

rq = __import__(MODULE, fromlist=["requiem"])


def _boom(depth: int = 0):
    if depth:
        return _boom(depth - 1)
    raise ValueError("the loop stopped mid-turn")


def _record(tmp_path, **overrides):
    try:
        _boom(2)
    except ValueError as exc:
        return rq.requiem(exc, path=tmp_path / "requiem.jsonl",
                          **{"unit": "eliza.psyche.rt_agent",
                             "where": "rt_agent.step", **overrides})


def test_a_crash_is_recorded_with_its_type_message_and_place(tmp_path):
    record = _record(tmp_path)
    assert record["recorded"] is True, record
    assert record["error"]["type"] == "ValueError"
    assert record["error"]["message"] == "the loop stopped mid-turn"
    assert record["unit"] == "eliza.psyche.rt_agent"
    assert record["where"] == "rt_agent.step"


def test_the_frame_chain_locates_the_death_not_the_entry_point(tmp_path):
    record = _record(tmp_path)
    assert record["frames"], "a requiem with no frames cannot be acted on"
    assert record["frames"][-1]["func"] == "_boom"
    assert record["frames"][-1]["line"] > 0


def test_it_records_but_names_no_cause(tmp_path):
    """Cause is a judgement made at the worst possible moment."""
    record = _record(tmp_path)
    assert "cause" not in record
    assert "diagnosis" not in record


def test_context_survives_values_json_cannot_hold(tmp_path):
    class Opaque:
        def __repr__(self):
            return "<opaque>"

    record = _record(tmp_path, context={"turn": 7, "session": Opaque(),
                                        "blob": b"\x00\x01", "nested": {"a": [1, 2]}})
    assert record["recorded"] is True
    stored = json.loads((tmp_path / "requiem.jsonl").read_text(encoding="utf-8"))
    assert stored["context"]["turn"] == 7
    assert "opaque" in stored["context"]["session"]
    assert stored["context"]["nested"] == {"a": [1, 2]}
    # ...and a scalar three levels down keeps its type instead of becoming a repr.
    assert stored["context"]["nested"]["a"] == [1, 2]


def test_a_grave_that_cannot_be_opened_returns_a_record_instead_of_raising(tmp_path):
    blocked = tmp_path / "a_directory.jsonl"
    blocked.mkdir()                       # opening a directory for append fails
    try:
        _boom(0)
    except ValueError as exc:
        record = rq.requiem(exc, unit="u", where="w", path=blocked)
    assert record["recorded"] is False
    assert record["write_error"]
    assert record["error"]["type"] == "ValueError"


def test_a_base_exception_is_recorded_too(tmp_path):
    try:
        raise KeyboardInterrupt("operator stopped it")
    except BaseException as exc:
        record = rq.requiem(exc, unit="u", where="w", path=tmp_path / "r.jsonl")
    assert record["error"]["type"] == "KeyboardInterrupt"
    assert record["recorded"] is True


def test_requiems_accumulate_and_read_back_newest_last(tmp_path):
    _record(tmp_path)
    _record(tmp_path)
    rows = rq.last(2, path=tmp_path / "requiem.jsonl")
    assert len(rows) == 2
    assert all(row["schema"] == rq.SCHEMA for row in rows)


def test_an_absent_grave_reads_as_empty_not_as_an_error(tmp_path):
    assert rq.last(path=tmp_path / "never_written.jsonl") == []



def test_a_torn_final_line_does_not_hide_the_records_before_it(tmp_path):
    target = tmp_path / "requiem.jsonl"
    _record(tmp_path)
    with target.open("a", encoding="utf-8") as handle:
        handle.write('{"schema": "eliza.requiem/1", "at": "torn')   # hard kill mid-write
    rows = rq.last(10, path=target)
    assert len(rows) == 1, rows


def test_the_epitaph_says_whether_it_was_recorded(tmp_path):
    record = _record(tmp_path)
    line = rq.epitaph(record)
    assert "eliza.psyche.rt_agent" in line and "ValueError" in line
    assert "NOT recorded" not in line

    blocked = tmp_path / "dir.jsonl"
    blocked.mkdir()
    try:
        _boom(0)
    except ValueError as exc:
        failed = rq.requiem(exc, unit="u", where="w", path=blocked)
    assert "NOT recorded" in rq.epitaph(failed)


def test_scream_records_an_uncaught_death_without_changing_the_exit_code(tmp_path):
    """The whole point: a dying process leaves a record AND still dies.

    Run in a subprocess because this asserts on a real interpreter death -- a
    crash handler that turns exit 1 into exit 0 would be a cover-up, and only an
    observed exit status can tell the difference.
    """
    script = tmp_path / "dies.py"
    grave_path = tmp_path / "requiem.jsonl"
    script.write_text(textwrap.dedent('''
        import sys
        sys.path.insert(0, %r)
        requiem = __import__(%r, fromlist=["requiem"])
        requiem.scream(%r)
        raise ValueError("the last scream")
    ''' % (str(SRC), MODULE, str(grave_path))), encoding="utf-8")

    done = subprocess.run([sys.executable, str(script)], capture_output=True,
                          text=True, timeout=60)

    assert done.returncode == 1, (done.returncode, done.stderr)
    assert "ValueError: the last scream" in done.stderr, done.stderr
    rows = rq.last(10, path=grave_path)
    assert len(rows) == 1, rows
    assert rows[0]["where"] == "uncaught"
    assert rows[0]["unit"] == "dies.py"
    assert rows[0]["error"]["message"] == "the last scream"
    assert rows[0]["recorded"] is True


def test_scream_records_a_worker_thread_death(tmp_path):
    grave_path = tmp_path / "requiem.jsonl"
    rq.scream(grave_path, fatal=False)
    try:
        def worker():
            raise RuntimeError("thread died")

        thread = threading.Thread(target=worker, name="worker-7")
        thread.start()
        thread.join(timeout=30)
        rows = rq.last(10, path=grave_path)
        assert len(rows) == 1, rows
        assert rows[0]["where"] == "thread:worker-7"
        assert rows[0]["error"]["type"] == "RuntimeError"
    finally:
        rq.hush()


def test_scream_is_idempotent_and_hush_puts_the_process_back(tmp_path):
    before = (sys.excepthook, threading.excepthook)
    sys.stdout.flush()
    first = rq.scream(tmp_path / "requiem.jsonl", fatal=False)
    second = rq.scream(tmp_path / "requiem.jsonl", fatal=False)
    try:
        assert first["hooks"] == ["sys.excepthook", "threading.excepthook"]
        assert second["hooks"] == [], "a second scream must not chain hooks twice"
        assert rq.screaming() is True
    finally:
        rq.hush()
    assert (sys.excepthook, threading.excepthook) == before
    assert rq.screaming() is False
    assert rq.grave() == rq.DEFAULT_PATH, "the grave must not outlive the scream"


def test_a_grave_that_cannot_be_created_is_reported_not_raised(tmp_path):
    blocked = tmp_path / "file-not-dir"
    blocked.write_text("in the way", encoding="utf-8")
    result = rq.scream(blocked / "requiem.jsonl", fatal=False)
    try:
        assert "write_error" in result
        assert result["fatal"] is False
    finally:
        rq.hush()


def test_the_environment_names_the_grave(monkeypatch, tmp_path):
    target = tmp_path / "env.jsonl"
    monkeypatch.setenv(rq.ENV_PATH, str(target))
    try:
        _boom(0)
    except ValueError as exc:
        record = rq.requiem(exc, unit="u", where="w")   # no explicit path
    assert record["path"] == str(target)
    assert target.is_file()
    assert len(rq.last(path=None)) == 1
