# coding: utf-8
"""Tests for `tools/monotone_reduction_check.py`.

Every test here is written against SELF-CONTAINED source fixtures, not
against `src/CORE/spine_registry.py`. That is deliberate. An earlier draft
asserted on the real module and "passed" for a month while the checker
could not see a single merge in it -- the assertions were describing the
ledger, not the checker's ability to see. A checker whose own test runs
against the file it is meant to guard cannot distinguish "the code is
clean" from "I am blind".

So each fixture is the smallest source that exhibits one shape, and each
test asserts the shape is seen. The last two tests are the ones that
matter most and are named for what they prevent.
"""
from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import monotone_reduction_check as mrc  # noqa: E402


def _write(tmp_path: Path, src: str, name: str = "fixture.py") -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(src), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# The two shapes this file exists to catch
# ---------------------------------------------------------------------------

#: A monotone store with a lowering write and NO watermark. This is the
#: shape of all five defects in spine_registry.
UNWATERMARKED = """
    from typing import Dict

    _COUNTS: Dict[str, int] = {}

    def _merge_disk_state(payload):
        for k, n in (payload.get("counts") or {}).items():
            _COUNTS[k] = max(_COUNTS.get(k, 0), int(n))

    def reset(key):
        _COUNTS[key] = 0
"""

#: The same store with a floor consulted by the code that computes the
#: merged value -- i.e. the post-fix shape.
WATERMARKED = """
    from typing import Dict

    _COUNTS: Dict[str, int] = {}
    _COUNTS_FLOOR: Dict[str, int] = {}

    def _adopt(key, incoming):
        cur = max(_COUNTS.get(key, 0), int(incoming))
        floor = _COUNTS_FLOOR.get(key)
        if floor is not None and cur > int(floor):
            cur = int(floor)
        return cur

    def _merge_disk_state(payload):
        for k, n in (payload.get("counts") or {}).items():
            _COUNTS[k] = _adopt(k, n)

    def reset(key):
        _COUNTS_FLOOR[key] = 0
        _COUNTS[key] = 0
"""


class _NoAck(mrc.__class__):  # pragma: no cover - not used
    pass


@pytest.fixture(autouse=True)
def _clean_ack():
    """Every test starts from the real ack list; none may leak state."""
    original = mrc.ACK_DEFAULT
    yield
    mrc.ACK_DEFAULT = original


def _analyse(tmp_path: Path, src: str, name: str = "fixture.py") -> dict:
    return mrc.analyse(_write(tmp_path, src, name))


def _by_key(report: dict) -> dict:
    return {s["store"]: s for s in report["stores"]}


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def test_finds_unwatermarked_store(tmp_path):
    r = _analyse(tmp_path, UNWATERMARKED)
    assert "_COUNTS" in _by_key(r), r
    s = _by_key(r)["_COUNTS"]
    assert s["monotone_merge"], "the max() merge was not seen"
    assert s["lowering_writes"], "the `= 0` write was not seen"
    assert s["watermarks"] == []


def test_watermarked_store_is_not_open(tmp_path):
    r = _analyse(tmp_path, WATERMARKED)
    s = _by_key(r)["_COUNTS"]
    assert s["monotone_merge"], "merge hidden behind a helper was missed"
    assert s["lowering_writes"], "the `= 0` write was missed"
    assert s["watermarks"] == ["_COUNTS_FLOOR"]
    assert s["verdict"] == "watermarked"


def test_clean_file_reports_nothing(tmp_path):
    r = _analyse(tmp_path, """
        from typing import Dict

        _ONLY: Dict[str, int] = {}

        def bump(key):
            _ONLY[key] = _ONLY.get(key, 0) + 1
    """)
    assert r["stores"] == []
    assert r["stores_seen"] == 1
    assert r["any_merge_seen"] is False


# ---------------------------------------------------------------------------
# Subscript targets -- the bug that made this tool report "OK" forever
# ---------------------------------------------------------------------------

def test_subscript_target_is_resolved(tmp_path):
    """`_S[k] = ...` must attribute to `_S`.

    Regression. `_dotted()` walked attributes and names only, so every
    subscript write in the file resolved to the empty string, zero merges
    were detected, and `spine_registry.py` -- the one file the tool was
    written about -- reported "OK" while all five of its stores were
    broken.
    """
    r = _analyse(tmp_path, UNWATERMARKED)
    assert r["any_merge_seen"] is True
    assert _by_key(r)["_COUNTS"]["monotone_merge"]


def test_merge_behind_helper_is_resolved(tmp_path):
    """`_S[k] = _adopt(k, n)` counts as a merge.

    Regression. The spine's own fix moved the `max()` into `_adopt_evidence`,
    and a checker looking only for a literal `max(` then reported the file
    clean. A guard that stops seeing the defect because the defect was
    refactored is worse than no guard.
    """
    r = _analyse(tmp_path, WATERMARKED)
    assert _by_key(r)["_COUNTS"]["monotone_merge"]


def test_alias_write_is_attributed_to_its_store(tmp_path):
    """`rec = _S.get(k)` then `rec[f] = 0` attributes to `_S`.

    Regression. Neither line mentions `_S` by subscript. Without Call
    peeling in `_base()`, the counter-reset instance was invisible.
    """
    r = _analyse(tmp_path, """
        from typing import Any, Dict

        _S: Dict[str, Dict[str, Any]] = {}

        def _adopt(key, f, incoming, current):
            return max(int(current), int(incoming))

        def _merge(payload):
            for k, v in (payload or {}).items():
                cur = _S.get(k)
                for f in ("successes", "failures"):
                    cur[f] = _adopt(k, f, v.get(f, 0), cur.get(f, 0))

        def reset(key):
            rec = _S.get(key)
            for f in ("successes", "failures"):
                rec[f] = 0
    """)
    s = _by_key(r)["_S"]
    assert s["monotone_merge"], "merge through an alias was missed"
    assert any(w["kind"] == "= 0" for w in s["lowering_writes"]), s["lowering_writes"]


def test_watermark_read_by_get_counts(tmp_path):
    """`_FLOOR.get(key)` is a read of the watermark.

    Regression. Only `S[k]` subscript reads were collected, so the floors
    consulted by `_adopt_evidence` / `_adopt_counter` -- which use `.get`
    -- did not count, and two genuinely watermarked stores reported
    NONE FOUND.
    """
    assert _by_key(_analyse(tmp_path, WATERMARKED))["_COUNTS"]["watermarks"]


# ---------------------------------------------------------------------------
# Scope -- a co-located read is not a watermark
# ---------------------------------------------------------------------------

def test_watermark_in_another_function_is_not_attributed(tmp_path):
    """`_merge_disk_state` reading a floor does not watermark every store.

    The real merge function reads the floors and merges four unrelated
    stores. Crediting all four because one call appears in the same
    function would make the watermark column meaningless.
    """
    r = _analyse(tmp_path, """
        from typing import Dict

        _A: Dict[str, int] = {}
        _B: Dict[str, int] = {}
        _FLOOR: Dict[str, int] = {}

        def merge(payload):
            for k, n in (payload.get("a") or {}).items():
                _A[k] = max(_A.get(k, 0), int(n))
            for k, n in (payload.get("b") or {}).items():
                _B[k] = max(_B.get(k, 0), int(n))
            if _FLOOR.get("guard"):
                _A["guard"] = 0
    """)
    st = _by_key(r)
    assert st["_A"]["watermarks"] == [], st["_A"]
    assert st["_B"]["watermarks"] == []


# ---------------------------------------------------------------------------
# Not-stores
# ---------------------------------------------------------------------------

def test_dispatch_table_is_not_a_store(tmp_path):
    """`Dict[str, Callable[...]]` is a registry, replaced wholesale."""
    r = _analyse(tmp_path, """
        from typing import Callable, Dict

        STAGES: Dict[str, Callable[[int], int]] = {}

        def register(name):
            STAGES[name] = lambda x: x
    """)
    assert r["stores_seen"] == 0
    assert r["stores"] == []


def test_absent_guard_is_not_a_lowering_write(tmp_path):
    """`_S[k] = v` under `if cur is None:` is a first write, not a cut.

    Without this, every adopt-on-load in a file looks like a downward
    write and buries the real findings.
    """
    r = _analyse(tmp_path, """
        from typing import Any, Dict

        _S: Dict[str, Dict[str, Any]] = {}
        _N: Dict[str, int] = {}

        def _adopt(k, v):
            return max(0, int(v))

        def merge(payload):
            for k, v in (payload or {}).items():
                cur = _S.get(k)
                if cur is None:
                    _S[k] = v
                    continue
                cur["n"] = _adopt(k, v.get("n", 0))

        def zero(k):
            _N[k] = 0

        def merge_n(payload):
            for k, n in (payload or {}).items():
                _N[k] = max(_N.get(k, 0), int(n))

        def reset_s(k):
            rec = _S.get(k)
            rec["n"] = 0
    """)
    st = _by_key(r)
    # The adopt-on-load must not be counted; the genuine reset must be.
    lines = [w["line"] for w in st["_S"]["lowering_writes"]]
    assert len(lines) == 1, st["_S"]["lowering_writes"]
    assert st["_S"]["lowering_writes"][0]["kind"] == "= 0"
    assert st["_N"]["lowering_writes"], "the real reduction must still be seen"


# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------

def test_inconclusive_when_no_merge_is_visible(tmp_path, capsys):
    """Zero merges over a file that declares stores is exit 2, never 0.

    A checker that finds nothing can be right for two very different
    reasons. Reporting both as "OK" is how this tool produced two green
    runs over a file it could not read.
    """
    rc = mrc.main([str(_write(tmp_path, """
        from typing import Dict

        _S: Dict[str, int] = {}

        def bump(k):
            _S[k] = _S.get(k, 0) + 1
    """))])
    assert rc == 2
    assert "INCONCLUSIVE" in capsys.readouterr().out


def test_open_store_exits_one(tmp_path, capsys):
    rc = mrc.main([str(_write(tmp_path, UNWATERMARKED))])
    out = capsys.readouterr().out
    assert rc == 1, out
    assert "OPEN" in out


def test_watermarked_store_exits_zero(tmp_path, capsys):
    rc = mrc.main([str(_write(tmp_path, WATERMARKED))])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "OK" in out


def test_ack_suppresses_a_finding(tmp_path, capsys):
    """Acknowledging is a human claim, and it is what makes rc 1 meaningful."""
    p = _write(tmp_path, UNWATERMARKED, name="fixture.py")
    mrc.ACK_DEFAULT = ("fixture:_COUNTS",)
    assert mrc.main([str(p)]) == 0


def test_json_output_is_parseable(tmp_path, capsys):
    mrc.main(["--json", str(_write(tmp_path, UNWATERMARKED))])
    d = json.loads(capsys.readouterr().out)
    assert d["unreviewed"] == 1
    assert d["inconclusive"] == 0
    assert d["reports"][0]["stores"][0]["store"] == "_COUNTS"


def test_missing_target_does_not_crash(tmp_path, capsys):
    assert mrc.main([str(tmp_path / "nope.py")]) == 0


# ---------------------------------------------------------------------------
# The real file, as a smoke test only
# ---------------------------------------------------------------------------

def test_live_spine_registry_is_not_open():
    """Smoke test. Deliberately says nothing about detection.

    The assertions are on the exit code and on the checker having seen the
    stores at all. Detection is covered by the fixtures above, which is
    the only way to know the checker works rather than assuming it.
    """
    report = mrc.analyse(ROOT / "src" / "CORE" / "spine_registry.py")
    assert report["stores_seen"] > 10
    assert report["any_merge_seen"] is True, \
        "the checker sees no merge in the file it was written for -- it is blind"
    assert report["inconclusive"] is False
    assert all(s["acknowledged"] for s in report["stores"]), \
        [s["key"] for s in report["stores"] if not s["acknowledged"]]
