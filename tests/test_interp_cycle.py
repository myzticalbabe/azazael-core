"""Unit tests for tools/interp_cycle.py — pure logic only.

No network, no compiler, no interpreter builds. The live legs (watch
against api.github.com, verify/promote against a real candidate) are
exercised by running the tool, not by this file.
"""
import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "interp_cycle", str(Path(__file__).resolve().parent.parent
                        / "tools" / "interp_cycle.py"))
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


def test_pinned_sha_prefers_evidence_sha():
    rec = {"evidence_sha": "a" * 40, "source": "tree @ " + "b" * 40}
    assert MOD.pinned_sha(rec) == "a" * 40


def test_pinned_sha_falls_back_to_source():
    rec = {"source": "C:/x/cpython-316 @ " + "c" * 40}
    assert MOD.pinned_sha(rec) == "c" * 40


def test_pinned_sha_empty_without_sha():
    assert MOD.pinned_sha({"source": "no sha here"}) == ""


def test_classify_files_splits_core():
    out = MOD.classify_files(["Python/ceval.c", "Doc/whatsnew.rst",
                              "PCbuild/python.vcxproj", "README.md"])
    assert out["core_count"] == 2
    assert out["other_count"] == 2
    assert out["core"] == ["PCbuild/python.vcxproj", "Python/ceval.c"]


def test_parse_selftest_pass_shape():
    n, ok = MOD.parse_selftest("selftest: 33 check(s), 0 failed, 0 skipped")
    assert (n, ok) == (33, True)


def test_parse_selftest_fail_shape():
    n, ok = MOD.parse_selftest("selftest: 33 check(s), 1 failed, 0 skipped")
    assert (n, ok) == (33, False)


def test_parse_selftest_unparseable():
    assert MOD.parse_selftest("usage: HYPER_EDGE selftest [-h]") == (None, False)


def test_parse_pytest_pass_shape():
    assert MOD.parse_pytest("51 passed, 2 warnings in 1.05s") == (51, True)


def test_parse_pytest_fail_shape():
    assert MOD.parse_pytest("50 passed, 1 failed in 2.1s") == (50, False)


def _classify(lines):
    editable = [ln for ln in lines
                if not ln[3:].startswith(("Programs/", "Include/pyconfig.h",
                                          "libs/", "PCbuild/"))]
    if not lines:
        return "CLEAN"
    return "BENIGN" if not editable else "SOURCE_EDIT"


def test_dirty_clean_tree():
    assert _classify([]) == "CLEAN"


def test_dirty_benign_build_artifacts():
    assert _classify([" M Programs/test_frozenmain.h",
                      "?? Include/pyconfig.h",
                      "?? libs/"]) == "BENIGN"


def test_dirty_source_edit_flagged():
    assert _classify([" M Programs/test_frozenmain.h",
                      " M Python/ceval.c"]) == "SOURCE_EDIT"


def test_lock_holder_empty_without_file(tmp_path, monkeypatch):
    monkeypatch.setattr(MOD, "BUILD_LOCK", tmp_path / "no-such.lock")
    assert MOD.lock_holder() == ""


def test_lock_take_and_release(tmp_path, monkeypatch):
    monkeypatch.setattr(MOD, "BUILD_LOCK", tmp_path / "b.lock")
    ns = type("NS", (), {})()
    ns.take, ns.release, ns.force = "freebuff", False, False
    assert MOD.do_lock(ns) == 0
    assert MOD.lock_holder() == "freebuff"
    ns.take, ns.release = None, True
    assert MOD.do_lock(ns) == 0
    assert MOD.lock_holder() == ""
