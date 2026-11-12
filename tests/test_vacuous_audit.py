# coding: utf-8
"""Tests for vacuous-audit repeat grouping (pattern findings)."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import vacuous_check_audit as audit  # noqa: E402


def _row(detector, path, code, line, sev="MED"):
    return {"detector": detector, "path": path, "code": code, "line": line,
            "severity": sev, "why": "w"}


def test_groups_repeated_idiom():
    rows = [_row("assert_true", "a.py", "assert True", 10),
            _row("assert_true", "a.py", "assert True", 20),
            _row("assert_true", "a.py", "assert True", 30)]
    groups = audit.group_repeats(rows)
    assert len(groups) == 1
    g = groups[0]
    assert g["kind"] == "pattern"
    assert g["count"] == 3
    assert g["locations"] == [10, 20, 30]


def test_singles_and_cross_file_not_grouped():
    rows = [_row("assert_true", "a.py", "assert True", 10),
            _row("assert_true", "b.py", "assert True", 10),
            _row("bare_except", "a.py", "except:", 40)]
    assert audit.group_repeats(rows) == []


def test_whitespace_normalized():
    rows = [_row("assert_true", "a.py", "assert   True", 10),
            _row("assert_true", "a.py", "assert True", 20)]
    groups = audit.group_repeats(rows)
    assert len(groups) == 1 and groups[0]["count"] == 2


def test_empty_code_never_grouped():
    rows = [_row("assert_true", "a.py", "", 10),
            _row("assert_true", "a.py", "", 20)]
    assert audit.group_repeats(rows) == []


# -- prose is not a check (engram #569, measured) ----------------------------
# 13 of the live findings were docstrings or comments that merely QUOTED the
# pattern, and the meta-evolution control plane selected one of them and
# proposed to rewrite documentation as if it were a check. `_mask_prose` blanks
# comments and docstrings only; inline string literals stay, so the detector it
# sharpens keeps firing.

_PROSE_SRC = "\n".join([
    "def f():",
    '    """Doc: result.get("ok", True) was the old default."""',
    '    # comment: d.get("success", True)',
    "    return 1",
    "",
])


def test_prose_quoting_the_pattern_is_masked():
    masked = audit._mask_prose(_PROSE_SRC)
    assert 'result.get("ok", True)' not in masked
    assert 'd.get("success", True)' not in masked
    # Offsets and line structure are preserved, so line/code stay exact.
    assert len(masked) == len(_PROSE_SRC)
    assert masked.count("\n") == _PROSE_SRC.count("\n")


def test_real_code_still_fires_the_detector():
    """Negative control: masking must not delete the detector it sharpens."""
    src = 'def f(result):\n    ok = bool(result.get("ok", True))\n    return ok\n'
    masked = audit._mask_prose(src)
    assert 'result.get("ok", True)' in masked
    patterns = {name: pat for name, pat, _sev, _why in audit.DETECTORS}
    assert len(re.findall(patterns["default_true"], masked)) == 1


def test_inline_string_literal_is_left_alone():
    src = 'PATTERN = \'k.get("ok", True)\'\n'
    assert 'k.get("ok", True)' in audit._mask_prose(src)


def test_mask_survives_non_ascii_before_the_pattern():
    """The measured crash: ast reports BYTES, tokenize reports CHARACTERS.

    A non-ASCII character earlier on a line pushed the byte column past the end
    of the character buffer, so `_mask_prose` raised IndexError on such files.
    """
    src = "\n".join([
        "def f():",
        '    """\u2192 \u2588 arrow and box in prose. result.get("ok", True)"""',
        "    return 1",
        "",
    ])
    masked = audit._mask_prose(src)
    assert 'result.get("ok", True)' not in masked
