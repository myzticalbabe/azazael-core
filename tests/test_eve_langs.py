"""G1 test for src/eve_unleashed/langs.py.

Real toolchains, tiny files: good files check clean, bad files fail by
name, formatting fixes formatting, and backups exist before any write.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from eve_unleashed.langs import check, detect, fix, fmt, have_tools
from eve_unleashed.langs import ARCH_CONTRACTS, check_arch

GOOD_RS = 'pub fn add(a: i32, b: i32) -> i32 {\n    a + b\n}\n'
BAD_RS = 'pub fn add(a: i32, b: i32) -> i32 {\n    a + \n}\n'
GOOD_GO = 'package main\n\nfunc Add(a, b int) int {\n\treturn a + b\n}\n'
BADFMT_GO = 'package main\n\nfunc Add(a, b int) int {\nreturn a + b\n}\n'


def test_detect_three_ways():
    from eve_unleashed.langs import detect as d
    assert d("x.rs") == "rust" and d("x.go") == "go"
    assert d("x.py") == "python"
    with pytest.raises(ValueError):
        d("x.java")


def test_good_files_check_clean(tmp_path):
    rs = tmp_path / "a.rs"
    rs.write_text(GOOD_RS, encoding="utf-8")
    assert check(rs)["ok"], check(rs)["diagnostics"]
    go = tmp_path / "a.go"
    go.write_text(GOOD_GO, encoding="utf-8")
    assert check(go)["ok"], check(go)["diagnostics"]


def test_bad_rust_fails_naming_the_bug(tmp_path):
    rs = tmp_path / "b.rs"
    rs.write_text(BAD_RS, encoding="utf-8")
    rep = check(rs)
    assert not rep["ok"]
    assert "expected" in rep["diagnostics"].lower()


def test_fmt_fixes_go_layout_with_backup(tmp_path):
    go = tmp_path / "c.go"
    go.write_text(BADFMT_GO, encoding="utf-8")
    assert fmt(go) is True
    assert (tmp_path / "c.go.evelang.bak").is_file()
    assert check(go)["ok"]


def test_fix_report_shape(tmp_path):
    go = tmp_path / "d.go"
    go.write_text(BADFMT_GO, encoding="utf-8")
    rep = fix(go)
    assert rep["fixed_by_format"] and rep["clean"] is True


GOOD_PY = "def add(a, b):\n    return a + b\n"
BAD_PY = "def add(a, b):\n    return a + \n"


def test_python_checks_syntax_never_executes(tmp_path):
    py = tmp_path / "e.py"
    py.write_text(GOOD_PY, encoding="utf-8")
    rep = check(py)
    assert rep == {"lang": "python", "ok": True, "diagnostics": ""}
    assert have_tools("python")["python"]  # the running interpreter


def test_python_bad_names_the_line(tmp_path):
    py = tmp_path / "f.py"
    py.write_text(BAD_PY, encoding="utf-8")
    rep = check(py)
    assert not rep["ok"]
    assert "2:" in rep["diagnostics"]  # the failing line, named


def test_python_fmt_is_honest_refusal_and_fix_is_check(tmp_path):
    py = tmp_path / "g.py"
    py.write_text(GOOD_PY, encoding="utf-8")
    with pytest.raises(ValueError):
        fmt(py)
    rep = fix(py)
    assert rep["fixed_by_format"] is False and rep["clean"] is True


def test_arch_contracts_known_and_unknown():
    assert set(ARCH_CONTRACTS) == {"gamora", "lucy", "jaeger-316t"}
    with pytest.raises(ValueError):
        check_arch("metaconscious")
    for name in ARCH_CONTRACTS:
        rep = check_arch(name)
        assert set(rep) == {"arch", "ok", "detail"}
        assert rep["arch"] == name and isinstance(rep["ok"], bool)
    # interpreter-independent arches hold everywhere; gamora holds on 316t
    assert check_arch("lucy")["ok"] is True
    assert check_arch("jaeger-316t")["ok"] is True
