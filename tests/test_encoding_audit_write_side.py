# coding: utf-8
"""WRITE-side detector (tools/encoding_audit.py --write-side) — pinned control.

The READ-side scan shipped first; the WRITE counterpart was the measured gap
(engram #712 Phase 4, cp1252 x2 via #706/#707): `open()` / `Path.write_text()`
with no `encoding=` ENCODES with the OS codec, so a non-ASCII string is written
as mojibake or raises mid-write. A detector whose purpose is "text does not get
lost" cannot cover only half the boundary.

Control discipline (Rule 4 / VERIFY ORACLES). The positive fixture below is the
SHAPE transcribed from a real site in this repository (`tools/terence_invariants.py`
has `open(..., "w")` with no encoding), and a separate test pins the detector to
that real file, so this is a check against the tree rather than against a fixture
written to agree with the detector. The negative fixtures assert the three ways
the detector must stay silent: an explicit encoding, a binary write, a read.
"""
import ast
import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

_SPEC = importlib.util.spec_from_file_location(
    "encoding_audit", REPO / "tools" / "encoding_audit.py")
ea = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ea)


def _scan(src):
    return ea.write_findings(ast.parse(src), src.splitlines())


def test_fires_on_the_transcribed_write_shape():
    # shape taken from tools/terence_invariants.py: open(dst, "w") with no encoding
    rows = _scan('with open(dst, "w") as fh:\n    fh.write(x)\n')
    assert len(rows) == 1, rows
    assert rows[0]["call"] == "open(mode='w')"


def test_append_mode_fires():
    assert len(_scan('open(log, "a")\n')) == 1


def test_write_text_without_encoding_fires():
    rows = _scan('Path(p).write_text(x)\n')
    assert len(rows) == 1 and rows[0]["call"] == "Path.write_text()"


def test_explicit_encoding_is_clean():
    assert _scan('open(p, "w", encoding="utf-8")\n') == []
    assert _scan('Path(p).write_text(x, encoding="utf-8")\n') == []


def test_binary_write_is_clean():
    assert _scan('open(p, "wb")\n') == []


def test_reads_are_clean():
    assert _scan('open(p)\n') == []
    assert _scan('open(p, "r")\n') == []


def test_pinned_control_on_the_real_tree():
    """Pin to the real artifact the fixture is transcribed from, not to a guess.

    If this file stops carrying the shape, the control must be re-pinned to a
    new real site -- that is the point of a pinned control, and it fails loud
    rather than passing on a stale transcription.
    """
    # 2026-10-20 sweep: 0 WRITE-side sites across src/**/*.py + tools/**/*.py
    # (old pin tools/terence_invariants.py remediated; compliance_auditor.py
    # also clean). Pin asserts the measured clean state so a reintroduced
    # bare write in either file fails loud instead of passing vacuously.
    for rel in [REPO / "tools" / "terence_invariants.py",
                REPO / "src" / "CORE" / "compliance_auditor.py"]:
        src = rel.read_text(encoding="utf-8", errors="replace")
        rows = ea.write_findings(ast.parse(src), src.splitlines())
        assert rows == [], (f"{rel.relative_to(REPO)} gained a WRITE-side site: {rows}")
