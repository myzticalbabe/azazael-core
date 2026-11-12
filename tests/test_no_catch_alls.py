# coding: utf-8
"""Tests for `scripts/check_no_catch_alls.py` — the catch-all ratchet.

Why these tests exist
---------------------
The guard was wired into `.github/workflows/catchall-guard.yml`, which ran it and
then ran nothing else: no test proved the guard can still see a bare `except`.
A ratchet with no control is indistinguishable from a script that prints "OK",
and this file is that control, in both directions:

* it fires on a bare `except:` and on an unmarked `except Exception:`;
* it stays silent on a clean handler and on a handler carrying the documented
  `# allow-broad` marker;
* `main()` is exercised as a program too, so the exit status the workflow reads
  is tested rather than assumed.

Everything is written to a throwaway tree; nothing here reads the real `src/`.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("check_no_catch_alls",
                                              ROOT / "scripts" / "check_no_catch_alls.py")
guard = importlib.util.module_from_spec(_spec)
sys.modules["check_no_catch_alls"] = guard
_spec.loader.exec_module(guard)


def _module(tmp_path, body: str, name: str = "sample.py") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


# -- it must see a defect ---------------------------------------------------- #

def test_a_bare_except_is_always_a_finding(tmp_path):
    path = _module(tmp_path, "def f():\n    try:\n        return 1\n    except:\n"
                             "        return 2\n")
    bare, broad = [], []
    guard.scan_file(str(path), bare, broad)
    assert [line for _p, line in bare] == [4]
    assert broad == []


def test_an_unmarked_broad_handler_is_counted(tmp_path):
    path = _module(tmp_path, "def f():\n    try:\n        return 1\n"
                             "    except Exception:\n        return 2\n")
    bare, broad = [], []
    guard.scan_file(str(path), bare, broad)
    assert bare == []
    assert [line for _p, line in broad] == [4]


def test_base_exception_is_broad_too(tmp_path):
    path = _module(tmp_path, "def f():\n    try:\n        return 1\n"
                             "    except BaseException:\n        return 2\n")
    bare, broad = [], []
    guard.scan_file(str(path), bare, broad)
    assert len(broad) == 1


# -- and it must stay silent on a healthy one -------------------------------- #

def test_a_narrow_handler_is_not_a_finding(tmp_path):
    path = _module(tmp_path, "def f():\n    try:\n        return 1\n"
                             "    except ValueError:\n        return 2\n")
    bare, broad = [], []
    guard.scan_file(str(path), bare, broad)
    assert (bare, broad) == ([], [])


def test_the_allow_broad_marker_exempts_a_deliberate_boundary(tmp_path):
    path = _module(tmp_path, "def f():\n    try:\n        return 1\n"
                             "    except Exception:  # allow-broad\n        return 2\n")
    bare, broad = [], []
    guard.scan_file(str(path), bare, broad)
    assert broad == []


def test_the_noqa_ble001_marker_exempts_on_the_body_line(tmp_path):
    path = _module(tmp_path, "def f():\n    try:\n        return 1\n"
                             "    except Exception:\n        return 2  # noqa: BLE001\n")
    bare, broad = [], []
    guard.scan_file(str(path), bare, broad)
    assert broad == []


def test_an_unparseable_file_is_skipped_rather_than_counted(tmp_path):
    """A file that will not parse has no except handlers to judge."""
    path = _module(tmp_path, "def f(:\n")
    bare, broad = [], []
    guard.scan_file(str(path), bare, broad)
    assert (bare, broad) == ([], [])


# -- the exit status the workflow reads ------------------------------------- #

def test_main_fails_in_files_mode_on_a_bare_except(tmp_path):
    path = _module(tmp_path, "def f():\n    try:\n        return 1\n    except:\n"
                             "        return 2\n")
    assert guard.main(["--files", str(path)]) == 1


def test_main_passes_in_files_mode_on_a_clean_file(tmp_path):
    path = _module(tmp_path, "def f():\n    try:\n        return 1\n"
                             "    except ValueError:\n        return 2\n")
    assert guard.main(["--files", str(path)]) == 0


def test_main_dir_mode_counts_only_the_directory_it_is_given(tmp_path):
    clean = tmp_path / "pkg"
    clean.mkdir()
    (clean / "ok.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    assert guard.main(["--dirs", str(clean), "--json"]) == 0

    (clean / "bad.py").write_text("def f():\n    try:\n        return 1\n"
                                  "    except:\n        return 2\n", encoding="utf-8")
    assert guard.main(["--dirs", str(clean)]) == 1
