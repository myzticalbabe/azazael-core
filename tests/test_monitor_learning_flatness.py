# coding: utf-8
"""EV-8 flatness guard for the learning-loop monitor.

Regression this pins (measured 2026-09-28)
------------------------------------------
`monitor_learning.run_check` flagged a flat evolution trajectory whenever the
BEST series was unchanged. With elitism that is the *normal* case -- the best
cannot move until a child beats the current elite -- so the monitor reported
"fitness has no gradient" on a healthy search. Live row that proved it:
`best=[16.276]*6` while the population mean moved `14.42 -> 15.38`.

The honest condition is that BOTH the best and the mean are unchanged. A flat
best with a moving mean (`flat_best_only`) is the benign elite signature, not a
stall, and is reported as such.

These tests call the extracted pure helper directly AND drive `run_check`
against a synthesised store, so a future edit that drops the mean term fails
here instead of silently crying "no gradient" again.
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

# `monitor_learning` runs `_ensure_gil()` at import, which re-execs the
# interpreter on a free-threaded build. Under pytest that would replace the
# whole session mid-run, so declare the guard already satisfied BEFORE importing
# it. (Verified: nothing else in tests/ sets this or imports the module.)
os.environ["AZAZAEL_GIL_ENFORCED"] = "1"

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_PROJECT_ROOT, "scripts"), _PROJECT_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import monitor_learning as ml  # noqa: E402


# ── the pure helper ────────────────────────────────────────────────────

class TestEvolutionFlatness:
    def test_flat_best_and_flat_mean_is_flat(self):
        flat, best_only = ml.evolution_flatness([5.0] * 4, [4.0] * 4)
        assert flat is True
        assert best_only is False

    def test_flat_best_with_moving_mean_is_not_flat(self):
        """The regression: elitism holds the best flat while the mean climbs."""
        flat, best_only = ml.evolution_flatness(
            [16.276] * 6, [14.42, 14.6, 14.9, 15.1, 15.3, 15.38])
        assert flat is False, "a moving mean is evidence of gradient"
        assert best_only is True

    def test_moving_best_with_flat_mean_is_not_flat(self):
        flat, best_only = ml.evolution_flatness([16.2, 16.276, 16.3],
                                                [15.0] * 3)
        assert flat is False
        assert best_only is False

    def test_neither_series_flat(self):
        assert ml.evolution_flatness([1.0, 2.0], [0.5, 0.7]) == (False, False)

    def test_empty_best_is_not_flat(self):
        assert ml.evolution_flatness([], [1.0, 2.0]) == (False, False)

    def test_legacy_row_without_mean_series_falls_back_to_best(self):
        """Pre-`trajectory_avg` rows keep the old best-only verdict."""
        assert ml.evolution_flatness([3.0] * 5, []) == (True, True)

    def test_rounding_tolerance_holds_below_four_decimals(self):
        # The monitor rounds to 4 dp, so jitter below that reads as unchanged.
        assert ml.evolution_flatness([1.000001, 1.000002], [2.0, 2.0])[0] is True
        assert ml.evolution_flatness([1.0, 1.001], [2.0, 2.0])[0] is False


# ── the monitor's verdict, end to end ──────────────────────────────────

_SCHEMA = (
    "CREATE TABLE memories ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " session_id TEXT NOT NULL,"
    " source TEXT NOT NULL,"
    " event_type TEXT NOT NULL,"
    " data TEXT NOT NULL DEFAULT '{}',"
    " severity TEXT NOT NULL DEFAULT 'info',"
    " tags TEXT NOT NULL DEFAULT '[]',"
    " timestamp TEXT NOT NULL,"
    " created_at TEXT NOT NULL DEFAULT (datetime('now')))"
)


def _store(tmp_path, rows):
    """A minimal memory store: rows are (event_type, data, age_minutes).

    Timestamps are written as AWARE ISO-8601 UTC (`...+00:00`), which is what
    the live writers (`LocalMemoryStore`) actually produce -- verified against
    `~/.azazael/memory_store.db` 2026-09-29. SQLite's own `datetime('now')` is
    naive and makes `_parse_ts` hand back a naive datetime, which the monitor's
    freshness maths then subtracts from `_now_utc()`; using the production
    shape keeps this test about the flatness rule, not that mismatch.
    """
    db = tmp_path / "memory_store.db"
    con = sqlite3.connect(str(db))
    try:
        con.execute(_SCHEMA)
        for event_type, data, age_min in rows:
            stamp = (datetime.now(timezone.utc)
                     - timedelta(minutes=age_min)).isoformat()
            con.execute(
                "INSERT INTO memories (session_id, source, event_type, data,"
                " timestamp) VALUES (?,?,?,?,?)",
                ("test", "test", event_type, json.dumps(data), stamp))
        con.commit()
    finally:
        con.close()
    # run_check opens a SQLite URI; forward slashes are the portable form.
    return str(db).replace(os.sep, "/")


def _evolution(**overrides):
    payload = {
        "trajectory": [16.276] * 6,
        "trajectory_avg": [14.42, 14.6, 14.9, 15.1, 15.3, 15.38],
        "best_genes": {"alpha": 1},
        "generations": 6,
        "seeded_from_prior": False,
    }
    payload.update(overrides)
    return payload


def _flat_warns(warns):
    return [w for w in warns if "flat" in w.lower()]


def _rows(evolution):
    return [
        ("state_saved", {"current_strategies": {"global": "default"}}, 1),
        ("evolution_result", evolution, 5),
    ]


def test_moving_mean_yields_no_flat_warn(tmp_path):
    report, _fails, warns = ml.run_check(_store(tmp_path, _rows(_evolution())), 60)
    assert _flat_warns(warns) == [], warns
    ev = report["signals"]["evolution"]
    assert ev["flat_trajectory"] is False
    assert ev["flat_best_only"] is True


def test_flat_best_and_mean_yields_the_flat_warn(tmp_path):
    db = _store(tmp_path, _rows(_evolution(trajectory_avg=[15.0] * 6)))
    _report, _fails, warns = ml.run_check(db, 60)
    assert _flat_warns(warns), warns


def test_legacy_row_without_mean_still_warns(tmp_path):
    legacy = {"trajectory": [16.276] * 6, "best_genes": {}, "generations": 6}
    _report, _fails, warns = ml.run_check(_store(tmp_path, _rows(legacy)), 60)
    assert _flat_warns(warns), warns
