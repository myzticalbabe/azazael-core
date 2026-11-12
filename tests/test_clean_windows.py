import pytest
# coding: utf-8
"""Cleaner tests: sandbox dirs only, never the live tree."""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

import clean_windows as cw

def _seed(root, files):
    for rel, age_days, size in files:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * size)
        old = time.time() - age_days * 86400.0
        os.utime(p, (old, old))
    return root


def test_scan_counts_eligible_only(tmp_path):
    d = _seed(tmp_path / "t", [("old.tmp", 5, 100), ("new.tmp", 0, 100),
                               ("setup.exe", 40, 100), ("keep.txt", 40, 100)])
    rep = cw.scan()
    assert isinstance(rep, list) and rep  # live-tree scan works
    # Direct eligibility checks (no live deletion):
    assert cw._eligible(d / "old.tmp", 1.0, "temp") is True
    assert cw._eligible(d / "new.tmp", 1.0, "temp") is False
    assert cw._eligible(d / "setup.exe", 30.0, "installers") is True
    assert cw._eligible(d / "keep.txt", 30.0, "installers") is False
    assert cw._eligible(d / "missing.tmp", 0.0, "temp") is False


def test_clean_removes_only_eligible(tmp_path, monkeypatch):
    d = _seed(tmp_path / "t", [("old.tmp", 5, 100), ("new.tmp", 0, 100)])
    monkeypatch.setattr(cw, "targets",
                        lambda include_downloads=False: [
                            ("test-tmp", d, 1.0, "temp")])
    rep = cw.clean()
    assert rep[0]["deleted"] == 1 and rep[0]["failed"] == 0
    assert not (d / "old.tmp").exists()
    assert (d / "new.tmp").exists()
    # Root itself is never removed.
    assert d.is_dir()


def test_clean_never_raises_on_locked(tmp_path, monkeypatch):
    d = _seed(tmp_path / "t", [("a.tmp", 5, 10)])
    monkeypatch.setattr(cw, "targets",
                        lambda include_downloads=False: [
                            ("test-tmp", d, 1.0, "temp"),
                            ("test-missing", d / "nope", 0.0, "temp")])
    rep = cw.clean()
    assert rep[0]["deleted"] == 1
    assert rep[1]["deleted"] == 0  # missing root skipped, not fatal


def test_report_runs_without_apply(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cw, "targets", lambda include_downloads=False: [])
    assert cw.main(["--report"]) == 0
    out = capsys.readouterr().out
    assert "RECOVERABLE" in out and "Untouched by design" in out
