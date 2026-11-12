"""Tests for tools/arc_provenance.py -- the ARC evidence leg.

The leg exists to answer one question about a pin: *can the thing it names still
be looked at?* Its value is entirely in refusing an answer it cannot support, so
these tests are mostly negative controls -- a pin that looks backed, fed to the
grader with the bytes removed, the digest altered, the quote rewritten or the
instrument deleted, must come back as a DEFECT rather than as a pass.

Every fixture is a synthetic tree under ``tmp_path``. The grader takes ``root`` as
an argument precisely so this is possible: a test that needed the real ARC tree
would fail for the tree's reasons instead of the code's.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "arc_provenance.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("arc_provenance_under_test", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # The module registers itself in sys.modules while exec_module runs (typing
    # and dataclass lookups go through sys.modules); without this the body
    # raises AttributeError on the module's own __dict__.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ap = _load_module()

CAPTURE = b"a captured page, byte for byte\n"
CAPTURE_SHA = hashlib.sha256(CAPTURE).hexdigest()
# Transcribed from docs/ESQUIRE_CANON_2026-11-03.md:69-71, which is the shape that
# broke the first version of this check: a blockquote continued across lines, with
# emphasis *inside* the sentence. An earlier fixture put the emphasis only at the
# sentence edges, where a plain substring search still succeeds -- it passed with
# the bug present, which is what a fixture written from the code's intent rather
# than the producer's text does.
CANON_LINES = (
    "> **A requirement that installs a human where a machine performed the work is a\n"
    "> *human-of-record mandate*. It is not featherbedding, and calling it that is "
    "an error of\n"
    "> law as well as of usage.**\n"
)
QUOTE = (
    "A requirement that installs a human where a machine performed the work is a "
    "*human-of-record mandate*. It is not featherbedding, and calling it that is an "
    "error of law as well as of usage."
)
OTHER_QUOTE = (
    "Nothing in this document says anything like the sentence the pin asserts, "
    "and the reader will not find it here."
)


def _root(tmp_path: Path) -> Path:
    (tmp_path / "ARC" / "alpha").mkdir(parents=True)
    (tmp_path / "ARC" / "alpha" / "capture.json").write_bytes(CAPTURE)
    (tmp_path / "ARC" / "alpha" / "pins.json").write_text('{"pins": []}', encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "canon.md").write_text(
        "# canon\n\nThe rule reads:\n\n%s" % CANON_LINES, encoding="utf-8"
    )
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "ok_instrument.py").write_text(
        "print('ran')\n", encoding="utf-8"
    )
    (tmp_path / "Project_BETA" / "tools").mkdir(parents=True)
    (tmp_path / "Project_BETA" / "tools" / "beta_gate.py").write_text(
        "print('gate')\n", encoding="utf-8"
    )
    return tmp_path


def _wing(name="alpha", **kwargs):
    wing = {"wing": name, "ledger": "ARC/%s/pins.json" % name}
    wing.update(kwargs)
    return wing


def _grade(root, *items, **wing_kwargs):
    wing = _wing(**wing_kwargs)
    ledger = root / wing["ledger"]
    ledger.write_text(json.dumps({"pins": list(items)}), encoding="utf-8")
    return ap.grade_wing(root, wing)


def _pin(**fields):
    base = {"id": "T1", "tier": "A", "text": "a stated requirement, long enough to qualify"}
    base.update(fields)
    return base


class TestBytes:
    def test_named_capture_is_rehashed_and_backed(self, tmp_path):
        root = _root(tmp_path)
        report = _grade(
            root,
            _pin(verified="ARC/alpha/capture.json sha256:%s" % CAPTURE_SHA),
        )
        assert report["items"][0]["state"] == ap.STATE_BACKED
        assert report["verdict"] == ap.VERDICT_READY
        assert report["exit"] == 0

    def test_recorded_digest_that_no_longer_matches_is_a_defect(self, tmp_path):
        root = _root(tmp_path)
        report = _grade(
            root,
            _pin(verified="ARC/alpha/capture.json sha256:%s" % ("0" * 64)),
        )
        assert report["items"][0]["state"] == ap.STATE_DEFECT
        assert "digest mismatch" in report["items"][0]["detail"]
        assert report["verdict"] == ap.VERDICT_GAPS
        assert report["exit"] != 0

    def test_deleted_capture_is_a_defect_not_a_gap(self, tmp_path):
        root = _root(tmp_path)
        (root / "ARC" / "alpha" / "capture.json").unlink()
        report = _grade(root, _pin(verified="ARC/alpha/capture.json sha256:%s" % CAPTURE_SHA))
        assert report["items"][0]["state"] == ap.STATE_DEFECT
        assert "capture missing" in report["items"][0]["detail"]

    def test_digest_without_a_filename_resolves_against_the_wing_bytes(self, tmp_path):
        root = _root(tmp_path)
        report = _grade(root, _pin(verified="sha256:%s" % CAPTURE_SHA))
        assert report["items"][0]["state"] == ap.STATE_BACKED

    def test_digest_no_bytes_produce_is_a_defect(self, tmp_path):
        root = _root(tmp_path)
        report = _grade(root, _pin(verified="sha256:%s" % ("a" * 64)))
        assert report["items"][0]["state"] == ap.STATE_DEFECT
        assert "digest unresolved" in report["items"][0]["detail"]

    def test_mechanism_shape_is_read_the_same_way(self, tmp_path):
        """cain's ledger nests tier and evidence under ``mechanism``."""
        root = _root(tmp_path)
        report = _grade(
            root,
            {
                "id": "C1",
                "mechanism": {
                    "tier": "A",
                    "verified": "ARC/alpha/capture.json sha256:%s" % CAPTURE_SHA,
                },
            },
        )
        assert report["items"][0]["tier"] == "A"
        assert report["items"][0]["state"] == ap.STATE_BACKED


class TestSource:
    def test_quote_in_an_emphasised_blockquote_is_located(self, tmp_path):
        root = _root(tmp_path)
        report = _grade(root, _pin(text=QUOTE, source="docs/canon.md"))
        assert report["items"][0]["state"] == ap.STATE_BACKED_SOURCE

    def test_quote_the_document_does_not_contain_is_a_defect(self, tmp_path):
        root = _root(tmp_path)
        report = _grade(root, _pin(text=OTHER_QUOTE, source="docs/canon.md"))
        assert report["items"][0]["state"] == ap.STATE_DEFECT
        assert "quote not in source" in report["items"][0]["detail"]
        assert report["defects"] == 1

    def test_tier_b_source_is_recorded_not_promoted_to_backed(self, tmp_path):
        root = _root(tmp_path)
        report = _grade(root, _pin(tier="B", text=OTHER_QUOTE, source="docs/canon.md"))
        assert report["items"][0]["state"] == ap.STATE_NOT_REQUIRED
        assert report["not_required"] == 1
        assert report["verdict"] == ap.VERDICT_READY

    def test_prose_about_a_source_is_not_a_capture(self, tmp_path):
        """A filename inside a quoted sentence must not be read as evidence."""
        root = _root(tmp_path)
        report = _grade(
            root,
            _pin(text="the document capture.json is discussed but not retained here"),
        )
        assert report["items"][0]["state"] == ap.STATE_UNBACKED


class TestInstrument:
    def test_existing_instrument_is_reported_rerunnable(self, tmp_path):
        root = _root(tmp_path)
        report = _grade(root, _pin(command="python tools/ok_instrument.py"))
        assert report["items"][0]["state"] == ap.STATE_INSTRUMENT
        assert report["instruments"] == 1

    def test_missing_instrument_is_a_defect(self, tmp_path):
        root = _root(tmp_path)
        report = _grade(root, _pin(command="python tools/deleted.py --check"))
        assert report["items"][0]["state"] == ap.STATE_DEFECT
        assert "instrument missing" in report["items"][0]["detail"]

    def test_instrument_resolves_inside_the_declared_project_root(self, tmp_path):
        root = _root(tmp_path)
        report = _grade(
            root,
            _pin(command="python tools/beta_gate.py --check"),
            project_root="Project_BETA",
        )
        assert report["items"][0]["state"] == ap.STATE_INSTRUMENT
        assert "beta_gate.py" in report["items"][0]["detail"]

    def test_a_command_saying_nothing_reproduces_it_is_a_defect(self, tmp_path):
        root = _root(tmp_path)
        report = _grade(
            root, _pin(command="no command in the tree reproduces the 4.2 percent figure")
        )
        assert report["items"][0]["state"] == ap.STATE_DEFECT
        assert "no instrument" in report["items"][0]["detail"]


class TestCoverage:
    def test_tier_a_with_nothing_behind_it_is_coverage_not_a_defect(self, tmp_path):
        root = _root(tmp_path)
        report = _grade(root, _pin(verified="2026-11-04 read at source, HTTP 200"))
        assert report["items"][0]["state"] == ap.STATE_UNBACKED
        assert report["coverage_gaps"] == 1
        assert report["defects"] == 0
        assert report["verdict"] == ap.VERDICT_INCOMPLETE
        assert report["errors"][0].startswith("T1 (A) UNBACKED:")

    def test_tier_b_with_nothing_is_recorded_and_required_nothing(self, tmp_path):
        root = _root(tmp_path)
        report = _grade(root, _pin(tier="B", verified="read at source, HTTP 200"))
        assert report["items"][0]["state"] == ap.STATE_NOT_REQUIRED
        assert report["verdict"] == ap.VERDICT_READY
        assert report["errors"] == []


class TestBlind:
    def test_missing_ledger_is_blind_with_a_reason(self, tmp_path):
        root = _root(tmp_path)
        (root / "ARC" / "alpha" / "pins.json").unlink()
        report = ap.grade_wing(root, _wing())
        assert report["verdict"] == ap.VERDICT_BLIND
        assert report["exit"] == 2
        assert report["pins"] is None
        assert "ledger unreadable" in report["reason"]

    def test_ledger_that_is_not_json_is_blind(self, tmp_path):
        root = _root(tmp_path)
        (root / "ARC" / "alpha" / "pins.json").write_text("{not json", encoding="utf-8")
        report = ap.grade_wing(root, _wing())
        assert report["verdict"] == ap.VERDICT_BLIND
        assert "not JSON" in report["reason"]

    def test_ledger_holding_the_wrong_shape_is_blind(self, tmp_path):
        root = _root(tmp_path)
        (root / "ARC" / "alpha" / "pins.json").write_text('"a string"', encoding="utf-8")
        report = ap.grade_wing(root, _wing())
        assert report["verdict"] == ap.VERDICT_BLIND


class TestCombine:
    def test_blind_outranks_every_other_state(self):
        rows = [
            {"verdict": ap.VERDICT_READY},
            {"verdict": ap.VERDICT_GAPS},
            {"verdict": ap.VERDICT_BLIND},
        ]
        assert ap.combine(rows) == (ap.VERDICT_BLIND, 2)

    def test_gaps_outranks_incomplete(self):
        rows = [{"verdict": ap.VERDICT_INCOMPLETE}, {"verdict": ap.VERDICT_GAPS}]
        assert ap.combine(rows) == (ap.VERDICT_GAPS, 1)

    def test_incomplete_is_not_ready(self):
        rows = [{"verdict": ap.VERDICT_READY}, {"verdict": ap.VERDICT_INCOMPLETE}]
        assert ap.combine(rows) == (ap.VERDICT_INCOMPLETE, 1)


class TestCli:
    def test_a_tree_with_no_ledgers_is_blind_and_writes_no_false_pass(
        self, tmp_path, monkeypatch
    ):
        """The wing table comes from arc_check; an empty tree must grade BLIND."""
        monkeypatch.setattr(ap, "ROOT", tmp_path)
        out = tmp_path / "report.json"
        code = ap.main(["--check", "--json", str(out)])
        assert code == 2
        report = json.loads(out.read_text(encoding="utf-8"))
        assert report["verdict"] == ap.VERDICT_BLIND
        assert {wing["verdict"] for wing in report["wings"]} == {ap.VERDICT_BLIND}
        assert all(wing["errors"] for wing in report["wings"])
