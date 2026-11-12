# coding: utf-8
"""The write-side detector must fire on a missing-encoding write and stay quiet
on the same write with `encoding=` spelled out.

Why this file exists. `tools/encoding_audit.py`'s docstring names
`tests/test_encoding_write_side.py` as "the disabled-path control that would
fail loudly if the detector were stubbed". That control did not exist -- verified
2026-10-19 against both HEAD and the worktree -- so the claim was unearned. It is
earned here, in three clauses:

  * a text write with no ``encoding=`` is reported, naming the call that wrote;
  * the SAME writes WITH ``encoding=``, plus ``wb``/``r`` modes, report nothing.
    Without this clause a detector that flagged every ``open()`` would pass
    clause 1;
  * stubbing ``write_findings`` makes clause 1's fixture report nothing, so the
    assertion provably rests on the detector and not merely on the fixture
    parsing.

The last clause is the one the docstring promised. It is a monkeypatched stub,
not a CLI flag, because the tool has no ``--disable`` for this rule: ``--disable``
targets ``console_print`` only. If a real disable switch is added later, this
clause should be re-pointed at it -- the stub control is the honest stand-in
until then, and the report says so rather than implying a flag exists.
"""

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import encoding_audit  # noqa: E402 - after sys.path
from encoding_audit import main, scan_file, write_findings  # noqa: E402


# -- fixtures ---------------------------------------------------------------
# Each payload is a whole module, because the detector works on a parsed AST.
# The pair MISSING / EXPLICIT differs by exactly one kwarg per call; that is
# what makes the negative control sharp rather than a different file.

MISSING = '''\
from pathlib import Path


def write_all(p, s):
    open(p, "w").write(s)
    open(p, "a").write(s)
    Path(p).write_text(s)
'''


EXPLICIT = '''\
from pathlib import Path


def write_all(p, s):
    open(p, "w", encoding="utf-8").write(s)
    open(p, "a", encoding="utf-8").write(s)
    Path(p).write_text(s, encoding="utf-8")
'''


NOT_A_TEXT_WRITE = '''\
def read_and_binary(p):
    open(p, "r").read()
    open(p).read()
    open(p, "wb").write(b"bytes encode nothing")
    open(p, mode="rb").write(b"neither does this")
'''


# Archive handles: `open` is the method name, but the handle is BINARY. Passing
# `encoding=` to `tarfile.open` raises TypeError, so these must never be
# reported -- five false positives in src/TOOLS/archive_system.py proved the
# name-only rule was too broad (2026-10-19).
ARCHIVE_WRITES = '''\
import gzip
import tarfile
import zipfile


def pack(p):
    tarfile.open(p, "w:gz").close()
    tarfile.open(name=p, mode="w:bz2")
    zipfile.ZipFile(p, "w")
    gzip.open(p, "wt")
    open(p, "w").write("still a text write")
'''


def _write(tmp_path, name, text):
    path = Path(tmp_path) / name
    path.write_text(text, encoding="utf-8")
    return path


# -- clause 1: the defect is reported ---------------------------------------

def test_missing_encoding_text_writes_are_reported(tmp_path):
    result = scan_file(_write(tmp_path, "missing.py", MISSING))
    assert result["scanned"] is True, result
    calls = [row["call"] for row in result["writes"]]
    # The contract is WHICH sites are reported, not the order they come back in:
    # asserting the sequence made this control fail on a correct detector
    # (2026-10-19) because findings are grouped by rule, not by line.
    assert sorted(calls) == sorted(["open(mode='w')", "open(mode='a')",
                                    "Path.write_text()"]), calls
    # The line must point at the CALL, which is what a reader fixes.
    assert sorted(row["line"] for row in result["writes"]) == [5, 6, 7], result["writes"]


def test_open_through_an_attribute_is_covered(tmp_path):
    """`io.open(p, "w")` is the same write; a bare-name match would miss it."""
    src = "import io\n\n\ndef f(p, s):\n    io.open(p, \"w\").write(s)\n"
    result = scan_file(_write(tmp_path, "attr.py", src))
    assert [row["call"] for row in result["writes"]] == ["open(mode='w')"], result


# -- clause 2: the negative control ----------------------------------------

def test_explicit_encoding_is_not_reported(tmp_path):
    result = scan_file(_write(tmp_path, "explicit.py", EXPLICIT))
    assert result["scanned"] is True, result
    assert result["writes"] == [], result["writes"]


def test_binary_and_read_modes_are_not_writes(tmp_path):
    """A binary write encodes nothing and a read writes nothing."""
    result = scan_file(_write(tmp_path, "not_a_write.py", NOT_A_TEXT_WRITE))
    assert result["writes"] == [], result["writes"]


def test_archive_and_compression_handles_are_not_text_writes(tmp_path):
    """`tarfile.open(p, "w:gz")` has no `encoding=` to add; it must not be reported.

    Only the trailing plain `open(p, "w")` is a defect. `gzip.open(p, "wt")` is
    excluded by receiver and `mode="w:bz2"` by the compression marker, so the
    two routes into the same false positive are covered separately.
    """
    result = scan_file(_write(tmp_path, "archive.py", ARCHIVE_WRITES))
    assert result["scanned"] is True, result
    assert [row["call"] for row in result["writes"]] == ["open(mode='w')"], result


def test_the_control_differs_only_by_encoding(tmp_path):
    """Same shape, same line count: the finding flips on `encoding=` alone."""
    missing = scan_file(_write(tmp_path, "a_missing.py", MISSING))
    explicit = scan_file(_write(tmp_path, "b_explicit.py", EXPLICIT))
    assert len(MISSING.splitlines()) == len(EXPLICIT.splitlines())
    assert len(missing["writes"]) == 3, missing["writes"]
    assert explicit["writes"] == [], explicit["writes"]


# -- clause 3: the detector is load-bearing --------------------------------

def test_a_stubbed_detector_yields_nothing(tmp_path, monkeypatch):
    """The docstring's promised control: stub the rule, the finding disappears.

    This is what would fail loudly if the detector were gutted: the fixture is
    unchanged and still parses, so a passing clause 1 cannot come from the
    fixture itself.
    """
    monkeypatch.setattr(encoding_audit, "write_findings", lambda tree, lines: [])
    result = scan_file(_write(tmp_path, "stubbed.py", MISSING))
    assert result["scanned"] is True, result
    assert result["writes"] == [], result["writes"]


def test_write_findings_is_directly_callable_and_empty_when_clean():
    """The rule is a pure function of (tree, lines), as its signature claims."""
    import ast

    lines = EXPLICIT.splitlines()
    assert write_findings(ast.parse(EXPLICIT), lines) == []
    missing_lines = MISSING.splitlines()
    assert len(write_findings(ast.parse(MISSING), missing_lines)) == 3


# -- the interface a caller actually uses ---------------------------------

def test_cli_does_not_gate_on_writes_without_the_flag(tmp_path, monkeypatch):
    """`--write-side` must be what turns the gate on; silence is not a pass."""
    _write(tmp_path, "missing.py", MISSING)
    monkeypatch.setattr(sys, "argv",
                        ["encoding_audit.py", "--dir", str(tmp_path), "--check"])
    assert main() == 0, "a write finding outside --write-side must not decide it"


def test_cli_write_side_gates_and_names_the_site(tmp_path, monkeypatch, capsys):
    _write(tmp_path, "missing.py", MISSING)
    monkeypatch.setattr(sys, "argv",
                        ["encoding_audit.py", "--dir", str(tmp_path),
                         "--check", "--write-side"])
    assert main() == 1
    out = capsys.readouterr().out
    assert "MISSING encoding= on WRITES" in out, out
    assert "open(mode='w')" in out, out


def test_cli_clean_tree_exits_zero(tmp_path, monkeypatch, capsys):
    _write(tmp_path, "explicit.py", EXPLICIT)
    monkeypatch.setattr(sys, "argv",
                        ["encoding_audit.py", "--dir", str(tmp_path),
                         "--check", "--write-side"])
    assert main() == 0
    assert "MISSING encoding= on WRITES" not in capsys.readouterr().out


# -- the default scope is part of the contract -----------------------------

def test_default_scope_includes_the_trees_that_carry_the_defect():
    """A default that skips `src/` certifies the wrong tree (#999).

    Measured 2026-10-19: the default was `("tools", "scripts")`, so a bare
    `--check` passed while 1,100+ `src/` and `tests/` modules were never
    parsed. A clean exit naming two helper trees is read as evidence about the
    repo, which is worse than no check. Pinning the membership here means a
    later edit cannot silently narrow the default again -- the failure would
    be a red test, not a green audit.
    """
    for tree in ("src", "tests", "packages"):
        assert tree in encoding_audit.DEFAULT_DIRS, (
            "%r was dropped from DEFAULT_DIRS: %r"
            % (tree, encoding_audit.DEFAULT_DIRS))
