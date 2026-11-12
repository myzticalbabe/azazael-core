# coding: utf-8
"""The baseline runner's failure table must be readable by a machine, not a log.

Why this file exists. `--failure-table` was added so a 4,000-test baseline can
hand its red batches to another process instead of a human reading a scroll.
That contract has three parts a naive implementation gets wrong: it must
project a FIXED shape (a journal record carries `timeout_s`, which is not a
failure-table field), it must report the LATEST record per batch (the journal
is append-only, so a retried batch appears twice and only the last one counts),
and a missing journal must read as "no evidence", not as an exception.

Seam: `suite_baseline.JOURNAL` is a module-level Path, so every test
monkeypatches that ONE global. Nothing here writes the real
`state/suite_baseline.jsonl` -- a test that did would corrupt the next
resume run, which is the runner's whole purpose.

Not covered, deliberately: a malformed (non-blank) line. `done_batches` skips
one via try/except but `failure_table`/`summarise` call `json.loads` unguarded,
so a half-written trailing line from a killed process makes `--report` raise.
That asymmetry is reported to the coordinator rather than pinned as behaviour:
a test asserting the crash would make the defect permanent.
"""

import json
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import suite_baseline as sb  # noqa: E402 - after sys.path

FIELDS = {"batch", "returncode", "timed_out", "n_files", "seconds", "files",
          "tail"}


def _rec(batch, returncode=0, *, timed_out=False, n_files=2,
         files=None, tail=None):
    """One journal record in the shape `run_batch` actually writes."""
    return {
        "batch": batch,
        "n_files": n_files,
        "returncode": returncode,
        "timed_out": timed_out,
        "timeout_s": 900,
        "seconds": 1.5,
        "tail": tail if tail is not None else ["1 failed, 19 passed"],
        "files": files if files is not None else ["tests/test_a.py", "tests/test_b.py"],
    }


def _journal(tmp_path, monkeypatch, records, *, name="journal.jsonl"):
    """Point the module's ONE journal seam at a fixture and write the records."""
    path = Path(tmp_path) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records),
                    encoding="utf-8")
    monkeypatch.setattr(sb, "JOURNAL", path)
    return path


# -- projection: fixed shape, latest record wins ---------------------------

def test_one_row_per_failed_batch_with_the_documented_shape(tmp_path, monkeypatch):
    _journal(tmp_path, monkeypatch,
             [_rec(0, 1), _rec(1, 0), _rec(2, 124, timed_out=True, n_files=3,
                                            tail=["[BATCH TIMEOUT after 900s"])])
    rows = sb.failure_table()

    assert [r["batch"] for r in rows] == [0, 2], rows
    for row in rows:
        assert set(row) == FIELDS, row
    assert rows[0]["returncode"] == 1
    assert rows[1]["timed_out"] is True
    assert rows[1]["n_files"] == 3
    assert rows[1]["tail"] == ["[BATCH TIMEOUT after 900s"]
    # `timeout_s` is a journal field, not a table field: a projection that just
    # forwarded the record would leak it here.
    assert "timeout_s" not in rows[0]


def test_the_latest_record_per_batch_decides(tmp_path, monkeypatch):
    """The journal is append-only, so a retried batch has two records."""
    _journal(tmp_path, monkeypatch,
             [_rec(0, 1), _rec(1, 0),          # batch 0 failed, batch 1 passed
              _rec(0, 0), _rec(1, 1)])         # batch 0 re-ran clean, 1 broke
    rows = sb.failure_table()

    assert [r["batch"] for r in rows] == [1], rows
    assert rows[0]["returncode"] == 1


def test_rows_are_sorted_by_batch_regardless_of_journal_order(tmp_path, monkeypatch):
    _journal(tmp_path, monkeypatch, [_rec(5, 1), _rec(2, 1), _rec(9, 1)])
    assert [r["batch"] for r in sb.failure_table()] == [2, 5, 9]


def test_an_all_green_journal_yields_an_empty_table(tmp_path, monkeypatch):
    _journal(tmp_path, monkeypatch, [_rec(0, 0), _rec(1, 0)])
    assert sb.failure_table() == []


# -- the file contract -----------------------------------------------------

def test_the_table_is_written_as_json_and_parents_are_created(tmp_path, monkeypatch):
    _journal(tmp_path, monkeypatch, [_rec(0, 1)])
    out = Path(tmp_path) / "deep" / "nested" / "failures.json"

    rows = sb.failure_table(str(out))

    assert out.exists(), "parent directories were not created"
    assert json.loads(out.read_text(encoding="utf-8")) == rows
    assert rows[0]["batch"] == 0


def test_no_path_means_no_file_is_written(tmp_path, monkeypatch):
    _journal(tmp_path, monkeypatch, [_rec(0, 1)])
    before = set(Path(tmp_path).rglob("*"))

    sb.failure_table()

    assert set(Path(tmp_path).rglob("*")) == before


# -- "no evidence" is not a failure ---------------------------------------

def test_a_missing_journal_is_empty_not_an_exception(tmp_path, monkeypatch):
    monkeypatch.setattr(sb, "JOURNAL", Path(tmp_path) / "never-written.jsonl")
    assert sb.failure_table() == []


def test_blank_lines_do_not_break_parsing(tmp_path, monkeypatch):
    """A journal can end in blank lines; `if x.strip()` is the guard."""
    path = Path(tmp_path) / "journal.jsonl"
    path.write_text(json.dumps(_rec(0, 1)) + "\n\n   \n", encoding="utf-8")
    monkeypatch.setattr(sb, "JOURNAL", path)

    assert [r["batch"] for r in sb.failure_table()] == [0]


# -- the interface a caller actually uses ---------------------------------

def test_cli_report_writes_the_table_and_gates_on_failures(tmp_path, monkeypatch,
                                                           capsys):
    _journal(tmp_path, monkeypatch, [_rec(0, 1)])
    out = Path(tmp_path) / "reports" / "failures.json"

    code = sb.main(["--report", "--failure-table", str(out)])
    printed = capsys.readouterr().out

    assert code == 1, printed
    assert out.exists(), printed
    assert json.loads(out.read_text(encoding="utf-8"))[0]["batch"] == 0
    assert "failure table" in printed, printed


def test_cli_report_on_a_green_journal_exits_zero(tmp_path, monkeypatch, capsys):
    _journal(tmp_path, monkeypatch, [_rec(0, 0)])
    out = Path(tmp_path) / "failures.json"

    code = sb.main(["--report", "--failure-table", str(out)])
    printed = capsys.readouterr().out

    assert code == 0, printed
    assert json.loads(out.read_text(encoding="utf-8")) == []
