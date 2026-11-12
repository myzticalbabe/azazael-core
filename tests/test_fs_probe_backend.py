# coding: utf-8
"""Acceptance tests for the bounded filesystem-probe backend (CORE/fs_probe.py).

Why this file exists: `fs_probe` is the primitive that stops a dead volume from
hanging its regulator, and none of it was covered by a test. The gap cost a full
session on 2026-09-17: with `G:` held by a USB card reader whose card had
stopped answering, a pytest run printed "13 passed in 15.06s" and then *could
not exit at all* -- only SIGKILL ended it, because the probe's worker thread was
parked in the Windows redirector and a thread parked in a native call prevents
clean interpreter shutdown. A thread cannot be killed; a child process can.

Contracts asserted here:
1. Predicates keep their exact in-process semantics (True/False, and `read`
   still RAISES on a missing file rather than degrading to None).
2. `None` means "the volume did not answer" and is distinct from `False`.
3. The FIRST probe of a path is delegated to a disposable child; the SECOND is
   an ordinary in-process stat (responsiveness is a volume property, so this is
   one interpreter start per path, not one per call).
4. A silent probe marks the path unreachable and does NOT mark it responsive.
5. `clear_unreachable_cache()` resets both caches, so the next probe delegates.
6. `bounded_subprocess_output` bounds a child that never returns, and reports
   the timeout instead of blocking the caller (the `wsl -l -v` fix).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from CORE import fs_probe


@pytest.fixture(autouse=True)
def fresh_probe_state():
    """Both caches are process-global; nothing may leak between tests."""
    fs_probe.clear_unreachable_cache()
    yield
    fs_probe.clear_unreachable_cache()


# ── 1 / 2 — predicate semantics and the unknown third value ─────────

def test_exists_and_isdir_match_os_semantics(tmp_path):
    (tmp_path / "adir").mkdir()
    (tmp_path / "afile.txt").write_text("hi", encoding="utf-8")

    assert fs_probe.bounded_is_dir(str(tmp_path / "adir")) is True
    assert fs_probe.bounded_is_dir(str(tmp_path / "afile.txt")) is False
    assert fs_probe.bounded_exists(str(tmp_path / "afile.txt")) is True
    assert fs_probe.bounded_exists(str(tmp_path / "nope.txt")) is False


def test_read_text_returns_content(tmp_path):
    p = tmp_path / "data.json"
    p.write_text('{"a": 1}', encoding="utf-8")
    assert fs_probe.bounded_read_text(str(p)) == '{"a": 1}'


def test_read_text_still_raises_on_a_missing_file(tmp_path):
    """The delegation must not change the in-process contract."""
    with pytest.raises(FileNotFoundError):
        fs_probe.bounded_read_text(str(tmp_path / "absent.json"))


def test_unknown_is_not_false():
    """`None` is "did not answer". A predicate must never invent a value."""
    assert fs_probe.bounded_path_probe(lambda: True) is True
    assert fs_probe.bounded_path_probe(lambda: 1 / 0) is False


# ── 3 — first probe out of process, second in process ───────────────

def test_first_probe_is_delegated_and_the_second_is_not(tmp_path):
    target = str(tmp_path)
    before = fs_probe.probe_backend_stats()["child_probes"]

    assert fs_probe.bounded_is_dir(target) is True
    after_first = fs_probe.probe_backend_stats()["child_probes"]
    assert after_first == before + 1, \
        "the first probe of a path must be delegated to a child"

    assert fs_probe.bounded_is_dir(target) is True
    assert fs_probe.probe_backend_stats()["child_probes"] == after_first, \
        "a proven-responsive path must be statted in-process afterwards"


def test_responsiveness_is_remembered_per_path(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    before = fs_probe.probe_backend_stats()["child_probes"]
    fs_probe.bounded_is_dir(str(a))
    fs_probe.bounded_is_dir(str(b))
    stats = fs_probe.probe_backend_stats()
    assert stats["child_probes"] == before + 2
    assert stats["responsive_paths"] >= 2


# ── 4 — silence is remembered as unreachable, not responsive ────────

def test_a_silent_probe_is_unreachable_and_not_responsive(tmp_path, monkeypatch):
    monkeypatch.setattr(fs_probe, "bounded_subprocess_output",
                        lambda *a, **k: {"ok": False, "out": "",
                                         "timeout": True, "error": ""})
    target = str(tmp_path / "never")
    assert fs_probe.bounded_is_dir(target) is None, \
        "a silent probe must report unknown, never False"
    # Second call must be answered from the unreachable cache (no new child).
    before = fs_probe.probe_backend_stats()["child_probes"]
    assert fs_probe.bounded_is_dir(target) is None
    assert fs_probe.probe_backend_stats()["child_probes"] == before
    assert fs_probe.unreachable_cache_stats()["suppressed_paths"] >= 1


def test_an_answering_error_path_is_marked_responsive(tmp_path):
    """A volume that answers with an error HAS answered."""
    missing = str(tmp_path / "missing" / "deep")
    assert fs_probe.bounded_exists(missing) is False
    stats = fs_probe.probe_backend_stats()
    assert stats["responsive_paths"] >= 1


# ── 5 — cache reset ─────────────────────────────────────────────────

def test_clear_cache_forces_a_fresh_delegated_probe(tmp_path):
    target = str(tmp_path)
    assert fs_probe.bounded_is_dir(target) is True
    before = fs_probe.probe_backend_stats()["child_probes"]
    fs_probe.clear_unreachable_cache()
    assert fs_probe.bounded_is_dir(target) is True
    assert fs_probe.probe_backend_stats()["child_probes"] == before + 1, \
        "clearing the caches must re-delegate, so a remounted drive is re-seen"


# ── bounded_first_dir ───────────────────────────────────────────────

def test_first_dir_skips_absent_candidates(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    candidates = [str(tmp_path / "absent"), str(tmp_path / "also_absent"),
                  str(real)]
    assert fs_probe.bounded_first_dir(candidates) == str(real)


def test_first_dir_returns_none_when_nothing_answers(tmp_path):
    assert fs_probe.bounded_first_dir([str(tmp_path / "x"),
                                       str(tmp_path / "y")]) is None


# ── 6 — bounded subprocess (the `wsl -l -v` deadlock fix) ───────────

def test_bounded_subprocess_bounds_a_child_that_never_returns():
    import time
    t0 = time.time()
    res = fs_probe.bounded_subprocess_output(
        [sys.executable, "-c", "import time; time.sleep(30)"], timeout_s=2.0)
    elapsed = time.time() - t0
    assert elapsed < 8.0, "the caller must not wait on the child"
    assert res["timeout"] is True and res["ok"] is False


def test_bounded_subprocess_returns_output_and_reports_success():
    res = fs_probe.bounded_subprocess_output(
        [sys.executable, "-c", "print('hello-probe')"], timeout_s=15.0)
    assert res["ok"] is True and not res["timeout"]
    assert "hello-probe" in res["out"]
