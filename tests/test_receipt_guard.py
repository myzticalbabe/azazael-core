# coding: utf-8
"""Tests for `tools/receipt_guard.py` — subagent guard G2 (board row 47198).

Pins the behaviour the 2026-11-02 guards audit asked for: a completion must
carry a receipt (command, exit_code, stdout_sha256) or be refused, and the
refusal must name the exact field.

Section 1 is the negative controls: the guard must FIRE on each malformed
receipt. Section 2 is the false-positive control that makes section 1 mean
something — a valid receipt MUST be accepted. A guard that refuses everything
passes every test in section 1 and is worthless, which is the failure mode
RULE 0B-C exists to catch.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import receipt_guard as rg

VALID = {
    "command": "python -m pytest tests/test_receipt_guard.py -q",
    "exit_code": 0,
    "stdout_sha256": "9f2c" + "0" * 60,
}


class TestNegativeControls:
    def test_corpus_cases_refused_on_expected_field(self):
        assert rg.CASES, "guard corpus must not be empty"
        for label, receipt, field in rg.CASES:
            named = {f for f, _ in rg.validate(receipt)}
            assert named, f"{label} was accepted; it must be refused"
            assert field in named, (label, sorted(named), field)

    def test_missing_command_is_named(self):
        problems = dict(rg.validate({"exit_code": 0,
                                     "stdout_sha256": VALID["stdout_sha256"]}))
        assert "missing" in problems["command"]

    def test_empty_command_is_refused(self):
        problems = dict(rg.validate(dict(VALID, command="   ")))
        assert "empty" in problems["command"]

    def test_exit_code_string_zero_is_refused(self):
        problems = dict(rg.validate(dict(VALID, exit_code="0")))
        assert "str" in problems["exit_code"]

    def test_bool_exit_code_is_refused(self):
        # True == 1 under isinstance(True, int); the guard must not accept it.
        problems = dict(rg.validate(dict(VALID, exit_code=True)))
        assert "bool" in problems["exit_code"]

    def test_63_char_hash_names_the_length(self):
        problems = dict(rg.validate(dict(VALID, stdout_sha256="a" * 63)))
        assert "63" in problems["stdout_sha256"]

    def test_uppercase_hash_names_the_case(self):
        problems = dict(rg.validate(dict(VALID, stdout_sha256="A" * 64)))
        assert "uppercase" in problems["stdout_sha256"]

    def test_non_hex_hash_names_the_character(self):
        problems = dict(rg.validate(dict(VALID, stdout_sha256="z" + "a" * 63)))
        assert "non-hex" in problems["stdout_sha256"]

    def test_absent_receipt_is_refused(self):
        assert rg.validate(None)[0][0] == "<root>"

    def test_non_object_receipt_is_refused(self):
        assert rg.validate([VALID])[0][0] == "<root>"


class TestFalsePositiveControls:
    def test_corpus_clean_accepted(self):
        assert rg.CLEAN, "guard clean corpus must not be empty"
        for label, receipt in rg.CLEAN:
            problems = rg.validate(receipt)
            assert problems == [], (label, problems)

    def test_valid_receipt_is_accepted(self):
        assert rg.validate(VALID) == []

    def test_failing_command_is_still_evidence(self):
        # A red test run is a receipt; an exit code of 1 is not malformed.
        assert rg.validate(dict(VALID, exit_code=1)) == []

    def test_extra_annotations_are_ignored(self):
        assert rg.validate(dict(VALID, board=47198, agent="freebuff")) == []


class TestCli:
    def test_accepts_valid_receipt_with_exit_0(self, tmp_path, capsys):
        path = tmp_path / "receipt.json"
        path.write_text(json.dumps(VALID), encoding="utf-8")
        assert rg.main(["--receipt", str(path)]) == 0
        assert "ACCEPTED" in capsys.readouterr().out

    def test_refuses_malformed_receipt_with_exit_1(self, tmp_path, capsys):
        path = tmp_path / "receipt.json"
        path.write_text(json.dumps(dict(VALID, stdout_sha256="a" * 63)),
                        encoding="utf-8")
        assert rg.main(["--receipt", str(path)]) == 1
        out = capsys.readouterr().out
        assert "REFUSED" in out and "stdout_sha256" in out

    def test_missing_receipt_file_exits_1(self, tmp_path):
        assert rg.main(["--receipt", str(tmp_path / "nope.json")]) == 1

    def test_not_json_exits_1(self, tmp_path):
        path = tmp_path / "receipt.json"
        path.write_text("{not json", encoding="utf-8")
        assert rg.main(["--receipt", str(path)]) == 1

    def test_no_arguments_refuses(self):
        assert rg.main([]) == 1

    def test_run_writes_a_receipt_that_validates(self, tmp_path):
        """The receipt producer and the validator must agree, or every
        completion is refused by its own evidence."""
        out = tmp_path / "receipt.json"
        # --out precedes --run: everything after --run is the command. The
        # child writes no newline, so the expected digest is byte-exact on
        # Windows as well as POSIX (print() would emit \r\n here).
        code = rg.main(["--out", str(out), "--run", "--", sys.executable, "-c",
                        "import sys; sys.stdout.write('7')"])
        assert code == 0
        receipt = json.loads(out.read_text(encoding="utf-8"))
        assert rg.validate(receipt) == []
        assert receipt["exit_code"] == 0
        assert len(receipt["stdout_sha256"]) == 64
        assert receipt["stdout_sha256"] == rg.sha256_bytes(b"7")

    def test_run_records_a_failing_exit_code(self, tmp_path):
        out = tmp_path / "receipt.json"
        code = rg.main(["--out", str(out), "--run", "--", sys.executable, "-c",
                        "import sys; sys.exit(3)"])
        # A nonzero exit is recorded, not refused: the receipt is evidence of
        # what happened, and refusing it would hide the failure.
        assert code == 0
        assert json.loads(out.read_text(encoding="utf-8"))["exit_code"] == 3

    def test_selftest_passes(self):
        assert rg.selftest() == 0


@pytest.mark.parametrize("digest", ["0" * 64, "f" * 64, "9f2c" + "0" * 60])
def test_lowercase_hex_boundaries_accepted(digest):
    assert rg.validate(dict(VALID, stdout_sha256=digest)) == []
