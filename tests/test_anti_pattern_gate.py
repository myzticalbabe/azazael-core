# coding: utf-8
"""Tests for `tools/anti_pattern_gate.py` — the anti-pattern ratchet.

Why these tests exist
---------------------
`reports/ERROR_LESSON_AUDIT_2026-10-03.md` measured the anti-pattern cluster as
"MECHANIZED BUT NOT ENFORCED": the scanner was built, the hook was written, and
neither was wired to anything that blocks. The failure mode of a quality gate is
never "it crashes" -- it is "it reports OK". So these tests pin the ways this
one could reassure while wrong:

* a count INCREASE must fail,
* a NEW category must fail even if the total is unchanged,
* a count DECREASE must NOT fail (driving a class to zero is the goal),
* a missing or scheme-mismatched baseline must not read as "nothing is wrong",
* and a narrowed run must not silently overwrite the whole-tree baseline.

The scanner is injected rather than invoked: a real run is ~5 s and would make
this file's result depend on the tree it is measuring. One test does run the
real scanner over a temp dir, as the positive/negative control for the injected
ones -- a gate proven only against a fake scanner proves nothing about the real
one.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import anti_pattern_gate as gate  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────


def _fake_scan(counts: dict, files: int = 10):
    """Return a replacement for gate.scan yielding fixed counts."""
    def _scan(target: str, *a, **k):
        return Counter(counts), files
    return _scan


def _fake_scan_with_files(counts: dict, files: int = 10, per_file: dict | None = None):
    """Replacement for gate.scan_with_files: fixed counts plus per-file detail."""
    def _scan(target: str, *a, **k):
        return Counter(counts), files, dict(per_file or {})
    return _scan


def _write_baseline(path: Path, counts: dict, scheme: str = gate.IDENTITY_SCHEME,
                    file_counts: dict | None = None):
    """Write a baseline. `file_counts` is omitted unless given, so the tests
    that pin the pre-2026-10-13 (no per-file counts) fallback stay honest."""
    payload = {
        "recorded_at": "2026-10-03T00:00:00+00:00",
        "identity_scheme": scheme,
        "category_counts": counts,
    }
    if file_counts is not None:
        payload["file_counts"] = file_counts
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def base_file(tmp_path):
    return tmp_path / "anti_pattern_baseline.json"


# ── the ratchet decision ─────────────────────────────────────────────────


class TestCheck:
    def test_equal_counts_pass(self, base_file, monkeypatch, capsys):
        _write_baseline(base_file, {"unicode_emoji": 5, "os_system": 0})
        monkeypatch.setattr(gate, "scan_with_files",
                            _fake_scan_with_files({"unicode_emoji": 5}))
        rc = gate.cmd_check("src", base_file)
        assert rc == 0
        assert "OK: no anti-pattern regressions" in capsys.readouterr().out

    def test_a_baseline_rewritten_during_the_scan_is_refused(
            self, base_file, monkeypatch, capsys):
        """A mid-run rewrite must be refused, not diffed against stale counts.

        The check loads the baseline, scans the tree (seconds to minutes), then
        compares. Several lanes share this worktree, so the file can be
        replaced inside that window; the comparison would then be against bytes
        this process never held, which reads as a pass or as a regression that
        belongs to no changeset. Measured 2026-11-04.
        """
        _write_baseline(base_file, {"unicode_emoji": 5})

        def _scan_while_rewriting(target, *a, **k):
            _write_baseline(base_file, {"unicode_emoji": 999})
            return Counter({"unicode_emoji": 5}), 10, {}

        monkeypatch.setattr(gate, "scan_with_files", _scan_while_rewriting)
        rc = gate.cmd_check("src", base_file)
        err = capsys.readouterr().err
        assert rc == 2
        assert "changed while this scan was running" in err

    def test_baseline_write_is_atomic_and_journalled(self, base_file, monkeypatch):
        """`--baseline` replaces the file and leaves one journal line.

        A truncate-and-write leaves a window where another reader sees partial
        JSON and dies with "baseline is unreadable" -- a message that names the
        symptom and hides the race. The journal is what makes a rewrite
        attributable after the fact.
        """
        monkeypatch.setattr(gate, "scan_with_files",
                            _fake_scan_with_files({"unicode_emoji": 2}))
        assert gate.cmd_baseline("src", base_file, False) == 0

        written = json.loads(base_file.read_text(encoding="utf-8"))
        assert written["writer_pid"] == os.getpid()
        assert list(base_file.parent.glob(base_file.name + ".tmp*")) == []

        journal = base_file.with_suffix(".journal.jsonl")
        assert journal.exists()
        last = json.loads(journal.read_text(encoding="utf-8").strip().splitlines()[-1])
        assert last["pid"] == os.getpid()
        assert last["total_findings"] == 2

    def test_increase_fails(self, base_file, monkeypatch, capsys):
        _write_baseline(base_file, {"unicode_emoji": 5})
        # The FAIL path now works out which CHANGED files carry the category,
        # and that shells out to git. Pinned, so this test measures the gate
        # rather than the live worktree (the reason the scanner is injected at
        # all). The assertion below is about the per-category delta either way.
        monkeypatch.setattr(gate, "_changed_paths", lambda target: {"CORE/x.py"})
        monkeypatch.setattr(gate, "scan_with_files",
                            _fake_scan_with_files({"unicode_emoji": 6}))
        rc = gate.cmd_check("src", base_file)
        assert rc == 1
        out = capsys.readouterr().out
        assert "REGRESSIONS" in out and "5 ->     6" in out

    def test_new_category_fails_even_if_total_equal(self, base_file, monkeypatch,
                                                    capsys):
        """A swap is a regression: one class down, a NEW class appears."""
        _write_baseline(base_file, {"unicode_emoji": 10})
        monkeypatch.setattr(gate, "_changed_paths", lambda target: {"CORE/x.py"})
        monkeypatch.setattr(gate, "scan_with_files",
                            _fake_scan_with_files({"unicode_emoji": 9,
                                                   "os_system": 1}))
        rc = gate.cmd_check("src", base_file)
        assert rc == 1
        out = capsys.readouterr().out
        assert "NEW category" in out and "os_system" in out

    def test_decrease_does_not_fail(self, base_file, monkeypatch, capsys):
        _write_baseline(base_file, {"unicode_emoji": 10, "os_system": 0})
        monkeypatch.setattr(gate, "scan_with_files",
                            _fake_scan_with_files({"unicode_emoji": 3}))
        rc = gate.cmd_check("src", base_file)
        assert rc == 0
        out = capsys.readouterr().out
        assert "improved" in out
        assert "cleared" in out and "os_system" in out

    def test_category_at_zero_recorded_and_stays_zero(self, base_file, monkeypatch):
        """The three driven-to-zero categories must not re-appear as NEW."""
        _write_baseline(base_file, {"t_string": 0, "os_system": 0,
                                    "cls_new_factory": 0, "unicode_emoji": 5})
        monkeypatch.setattr(gate, "scan_with_files",
                            _fake_scan_with_files({"unicode_emoji": 5}))
        assert gate.cmd_check("src", base_file) == 0


# ── the baseline must not be trusted blindly ─────────────────────────────


class TestBaselineIntegrity:
    def test_missing_baseline_is_not_a_pass(self, tmp_path):
        with pytest.raises(SystemExit) as exc:
            gate.cmd_check("src", tmp_path / "does_not_exist.json")
        assert exc.value.code == 2

    def test_scheme_mismatch_is_refused(self, base_file, monkeypatch, capsys):
        """Counts are not comparable across schemes; refuse, do not diff."""
        _write_baseline(base_file, {"unicode_emoji": 5}, scheme="old-scheme-v0")
        monkeypatch.setattr(gate, "scan_with_files",
                            _fake_scan_with_files({"unicode_emoji": 5}))
        rc = gate.cmd_check("src", base_file)
        assert rc == 2
        assert "identity_scheme" in capsys.readouterr().err

    def test_tracked_only_baseline_is_not_redirected(self):
        """`--tracked-only` is a PREDICATE, not a narrowing.

        Measured 2026-11-01: it was treated as narrowed, so `--baseline
        --tracked-only` wrote `anti_pattern_baseline.partial.json` while
        `--check --tracked-only` (what the hook runs) read
        `anti_pattern_baseline.json` -- the file nothing updated. The ratchet then
        diffed tracked counts against a worktree baseline and went red on
        untracked scratch from another lane. Only `--dir` may redirect.
        """
        # narrowed is False for tracked-only now; the redirect must therefore
        # resolve to the path the check reads.
        assert gate._resolve_out(None, narrowed=False, kind="baseline") == gate.BASELINE
        assert gate._resolve_out(None, narrowed=False, kind="baseline").name == \
            "anti_pattern_baseline.json"

    def test_predicate_mismatch_is_refused_not_diffed(self, base_file, monkeypatch,
                                                      capsys):
        """A worktree baseline diffed against a tracked scan is not comparable.

        The negative control for the fix above: if this ever passes silently, the
        gate is back to measuring one tree and reporting on another.
        """
        _write_baseline(base_file, {"unicode_emoji": 5})  # recorded tracked_only=False
        monkeypatch.setattr(gate, "scan_with_files",
                            _fake_scan_with_files({"unicode_emoji": 5}))
        rc = gate.cmd_check("src", base_file, tracked_only=True)
        assert rc == 2
        err = capsys.readouterr().err
        assert "not comparable" in err and "--tracked-only" in err

    def test_matching_predicate_is_compared(self, base_file, monkeypatch):
        """And the same pair WITH matching predicates still gates normally."""
        data = {"identity_scheme": gate.IDENTITY_SCHEME, "target": "src",
                "tracked_only": True, "files_scanned": 1,
                "category_counts": {"unicode_emoji": 5}, "total_findings": 5}
        base_file.write_text(json.dumps(data), encoding="utf-8")
        monkeypatch.setattr(gate, "scan_with_files",
                            _fake_scan_with_files({"unicode_emoji": 5}))
        assert gate.cmd_check("src", base_file, tracked_only=True) == 0

    def test_each_predicate_has_its_own_baseline_file(self):
        """Two scopes, two sets of counts, two files -- and one reader each.

        The earlier shape kept one file and REFUSED the other predicate, which
        made a plain `--check` unrunnable rather than useful: a diagnostic flag
        that can only ever exit 2 is not a diagnostic.
        """
        tracked, worktree = gate.baseline_path(True), gate.baseline_path(False)
        assert tracked.name == "anti_pattern_baseline.json"
        assert worktree.name == "anti_pattern_baseline.worktree.json"
        assert tracked != worktree

    def test_narrowed_baseline_redirected_to_partial(self, tmp_path):
        """`--baseline --dir src/CORE` must not overwrite the whole-tree file."""
        out = gate._resolve_out(None, narrowed=True, kind="baseline")
        assert out.name == "anti_pattern_baseline.partial.json"
        assert out != gate.BASELINE

    def test_whole_tree_baseline_uses_default_path(self):
        out = gate._resolve_out(None, narrowed=False, kind="baseline")
        assert out == gate.BASELINE

    def test_explicit_path_is_obeyed(self, tmp_path):
        explicit = tmp_path / "custom.json"
        assert gate._resolve_out(str(explicit), narrowed=True,
                                 kind="baseline") == explicit.resolve()


# ── the committed baseline must describe the committed tree ──────────────


class TestCommittedBaseline:
    def test_baseline_exists_and_uses_current_scheme(self):
        assert gate.BASELINE.exists(), (
            "logs/anti_pattern_baseline.json is missing -- the hook's ratchet "
            "cannot run. Record it: python tools/anti_pattern_gate.py --baseline")
        data = json.loads(gate.BASELINE.read_text(encoding="utf-8"))
        assert data["identity_scheme"] == gate.IDENTITY_SCHEME
        assert data["target"] == "src"
        assert data["category_counts"], "baseline recorded no categories"
        # The predicate the hook enforces. Absent this key the check cannot tell
        # a tracked scan from a worktree one, which is how the ratchet came to
        # diff one against the other (2026-11-01).
        assert data.get("tracked_only") is True, (
            "the baseline must record the scan predicate the hook uses; re-record "
            "with: python tools/anti_pattern_gate.py --baseline --tracked-only")

    def test_worktree_baseline_is_a_separate_scope(self):
        """The other predicate is recorded beside it, not instead of it.

        Skipped when this host has not recorded one: the worktree baseline counts
        untracked scratch, which is a property of a working copy rather than of a
        commit, so its absence is "not recorded here" and never "zero findings".
        """
        if not gate.WORKTREE_BASELINE.exists():
            pytest.skip("no worktree-scope baseline recorded on this host; "
                        "record it with: python tools/anti_pattern_gate.py --baseline")
        worktree = json.loads(gate.WORKTREE_BASELINE.read_text(encoding="utf-8"))
        tracked = json.loads(gate.BASELINE.read_text(encoding="utf-8"))
        assert worktree["identity_scheme"] == gate.IDENTITY_SCHEME
        assert worktree.get("tracked_only") is False, worktree.get("tracked_only")
        assert tracked.get("tracked_only") is True
        # Two different scopes cannot be the same measurement of the same tree.
        assert worktree["category_counts"] != tracked["category_counts"] or \
            worktree["files_scanned"] != tracked["files_scanned"]

    def test_baseline_agrees_with_a_live_scan(self):
        """The committed baseline must equal a real scan of the tracked src/ tree.

        This is the positive/negative control for every injected-scanner test
        above: it proves the instrument the gate wraps actually produces the
        numbers the baseline claims. If this fails, the tree grew or shrank and
        `--baseline` must be re-run deliberately.

        TRACKED-ONLY, matching what the hook enforces (2026-11-01). The gate's
        default scan walks the worktree and counts untracked scratch -- session
        probes, dated one-shots, another lane's `??` modules -- so a full-scan
        comparison made this control fail on files no commit contains: measured
        that day, 968 vs 953 `import_inside_function` and 968 files, entirely
        from untracked paths. That is the same defect the `--tracked-only` flag
        was added for on 2026-10-04; the control had simply kept comparing a
        different tree than the gate judges.
        """
        data = json.loads(gate.BASELINE.read_text(encoding="utf-8"))
        live, files = gate.scan("src", tracked_only=True)
        assert dict(sorted(live.items())) == data["category_counts"], (
            "live anti-pattern counts differ from logs/anti_pattern_baseline.json; "
            "re-run: python tools/anti_pattern_gate.py --baseline --tracked-only")
        assert files == data["files_scanned"], (
            "the baseline describes %s file(s) but a tracked scan covers %s -- "
            "re-record it with --tracked-only" % (data["files_scanned"], files))


# ── the scanner itself (positive + negative control) ─────────────────────


class TestRealScanner:
    def test_clean_file_has_no_findings(self, tmp_path):
        """A clean file must scan clean -- the negative control."""
        (tmp_path / "clean.py").write_text("import os\n\n\n"
                                           "def f() -> int:\n    return 1\n",
                                           encoding="utf-8")
        counts, files = gate.scan(str(tmp_path))
        assert files == 1
        assert not counts

    def test_emoji_file_is_detected(self, tmp_path):
        """The positive control: a real emoji must be counted."""
        (tmp_path / "bad.py").write_text('X = "\U0001F600"\n', encoding="utf-8")
        counts, _files = gate.scan(str(tmp_path))
        assert counts.get("unicode_emoji", 0) >= 1


# ── per-file attribution (followup #3, 2026-10-13) ───────────────────────


class TestAttribution:
    """A regression must name the FILE, not just the category.

    Earned 2026-10-13: an unrelated concurrent edit to two `src/` files took the
    repo-wide count over baseline and blocked a commit whose own diff was a
    single test file. The per-category delta was correct and useless -- nothing
    in the output said which file to look at, so the committer had to bisect the
    worktree by hand. This class pins the "where" the FAIL path was missing.

    The `TestCheck` fakes above now patch `scan_with_files` rather than `scan`,
    because `cmd_check` reads the finer view to print that section: the seam
    moved, so the injection follows it. `scan` keeps its own callers
    (`cmd_report`, `TestCommittedBaseline`) unchanged; `--baseline` records
    per-file counts through `scan_with_files`, which is what makes the EXACT
    mode below available to the next failure.

    A ranking of the files HOLDING a category was not good enough. Measured the
    same day: it named an untouched file with 32 findings as the owner of a
    regression carried by a file with 3, and the file that actually grew did not
    appear in the list at all. So the blame is a DELTA where the baseline allows
    it, a labelled changed-files view where it does not, and both a cap and an
    unexplained increase are always admitted in words rather than silently.
    """

    def test_scan_with_files_agrees_with_scan_and_attributes_per_file(self, tmp_path):
        """The finer view is the SAME measurement, and it names the right file."""
        (tmp_path / "clean.py").write_text("def f() -> int:\n    return 1\n",
                                           encoding="utf-8")
        (tmp_path / "bad.py").write_text('X = "\U0001F600"\n', encoding="utf-8")

        counts, files = gate.scan(str(tmp_path))
        counts2, files2, per_file = gate.scan_with_files(str(tmp_path))

        # One instrument, one scope: the two views cannot disagree.
        assert counts2 == counts
        assert files2 == files == 2
        # The responsible file is named, by a path RELATIVE to the scan root --
        # not the absolute path the scanner reports, which would be noise in a
        # failure message.
        assert "bad.py" in per_file, per_file
        assert per_file["bad.py"].get("unicode_emoji", 0) >= 1
        assert str(tmp_path) not in " ".join(per_file)
        assert "clean.py" not in per_file, "a clean file has nothing to attribute"
        # The attribution explains the total rather than restating a different
        # one: summing it must reproduce the whole-tree counts exactly.
        summed: Counter = Counter()
        for bucket in per_file.values():
            summed.update(bucket)
        assert summed == counts

    def test_exact_mode_names_only_the_files_that_increased(self, base_file,
                                                            monkeypatch, capsys):
        """A baseline WITH per-file counts makes the blame a real delta.

        Three things must hold at once: the file that GREW is named first, a file
        that merely HOLDS a lot of the category is not named at all, and a file
        that shrank (or is gone) is not either. Without the second and third the
        list degenerates into a popularity ranking, which is exactly what the
        first version of this section printed.
        """
        worst = "CORE/loud.py"
        per_file = {worst: Counter({"unicode_emoji": 9}),
                    "CORE/holder.py": Counter({"unicode_emoji": 1})}
        for i in range(gate.ATTRIBUTION_LIMIT):
            per_file["CORE/other_%d.py" % i] = Counter({"unicode_emoji": 3})
        total = 9 + gate.ATTRIBUTION_LIMIT * 3 + 1
        _write_baseline(base_file, {"unicode_emoji": 7},
                        file_counts={worst: {"unicode_emoji": 2},
                                     "CORE/holder.py": {"unicode_emoji": 1},
                                     "CORE/gone.py": {"unicode_emoji": 4}})
        monkeypatch.setattr(
            gate, "scan_with_files",
            _fake_scan_with_files({"unicode_emoji": total},
                                  files=len(per_file), per_file=per_file))

        rc = gate.cmd_check("src", base_file)

        assert rc == 1
        out = capsys.readouterr().out
        assert "WHERE" in out
        # The reader is told which reference produced the numbers.
        assert "reference: per-file increase vs baseline" in out
        # Worst first, and shown as an increase: 2 -> 9.
        assert out.index(worst) < out.index("CORE/other_0.py")
        assert "+7" in out
        # A holder that did not move is not a cause, and neither is a file that
        # only existed in the baseline.
        assert "CORE/holder.py" not in out
        assert "CORE/gone.py" not in out
        # The cap is REPORTED: 1 + ATTRIBUTION_LIMIT owners against a limit of
        # ATTRIBUTION_LIMIT leaves exactly one file unshown.
        assert "(+1 more file(s) not shown)" in out

    def test_fallback_mode_labels_itself_and_names_a_changed_file(
            self, base_file, monkeypatch, capsys):
        """No `file_counts` in the baseline -> blame falls on CHANGED content.

        Every baseline recorded before 2026-10-13 lacks per-file counts, so this
        is the mode the installed hook actually runs in. It must name the changed
        file, refuse to name an untouched holder, and SAY that the reference is
        the changed-file view rather than an exact delta -- a weaker reference
        that does not announce itself is how the first version misled.
        """
        _write_baseline(base_file, {"unicode_emoji": 1})       # no file_counts
        monkeypatch.setattr(gate, "_changed_paths",
                            lambda target: {"CORE/changed.py"})
        per_file = {"CORE/changed.py": Counter({"unicode_emoji": 4}),
                    "CORE/untouched.py": Counter({"unicode_emoji": 30})}
        monkeypatch.setattr(
            gate, "scan_with_files",
            _fake_scan_with_files({"unicode_emoji": 34}, files=2,
                                  per_file=per_file))

        rc = gate.cmd_check("src", base_file)

        assert rc == 1
        out = capsys.readouterr().out
        assert "no per-file counts" in out
        assert "re-run --baseline" in out
        assert "CORE/changed.py" in out
        assert "CORE/untouched.py" not in out, "a holder is not a cause"

    def test_fallback_mode_reports_an_increase_no_changed_file_explains(
            self, base_file, monkeypatch, capsys):
        """An unexplained increase must be SAID, not omitted.

        If the count grew while no changed file carries the category, the
        baseline is stale. Printing nothing there would read as "the tree is
        fine", which is the false reassurance this gate exists to prevent.
        """
        _write_baseline(base_file, {"unicode_emoji": 1})
        monkeypatch.setattr(gate, "_changed_paths", lambda target: set())
        monkeypatch.setattr(
            gate, "scan_with_files",
            _fake_scan_with_files({"unicode_emoji": 30}, files=1,
                                  per_file={"CORE/untouched.py":
                                            Counter({"unicode_emoji": 30})}))

        rc = gate.cmd_check("src", base_file)

        assert rc == 1
        out = capsys.readouterr().out
        assert "NOT explained by the working tree" in out
        assert "stale baseline" in out
        # And it must not quietly promote the unrelated holder to "the cause".
        assert "CORE/untouched.py" not in out
