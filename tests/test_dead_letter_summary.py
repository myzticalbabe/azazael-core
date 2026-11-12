# coding: utf-8
"""Absorbed NCM DLQ API on DeadLetterQueue (U3 surgery 2026-10-01).

get_dead_letter_summary / retry_all_failed_worms moved here from
ClusterManager with the dimension flattened (cluster_id category
instead of nature). Contract: same keys, windowed, never raises.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from CORE.WORMS.dead_letter_queue import DeadLetterQueue


def _queue(*worms):
    tmp = tempfile.mkdtemp(prefix="dlq-absorb-")
    q = DeadLetterQueue(storage_path=Path(tmp) / "dl.json")
    for name, cat in worms:
        q.enqueue(name, {"error": "boom-%s" % name}, config={})
        q._letters[-1].cluster_id = cat
    q._save()
    return q


def test_summary_window_and_shape():
    q = _queue(("W1", "monitor"), ("W1", "monitor"), ("W2", "diagnostic"))
    s = q.get_dead_letter_summary(lookback_seconds=3600)
    assert s["total_failed"] == 3
    assert s["affected_categories"] == 2
    assert s["per_category"] == {"monitor": 2, "diagnostic": 1}
    assert s["per_worm"] == {"W1": 2, "W2": 1}
    assert len(s["recent_errors"]) == 3
    assert s["recent_errors"][0]["category"] in ("monitor", "diagnostic")
    assert all(e["timestamp_epoch"] > 0 for e in s["recent_errors"])


def test_retry_scoped_to_category():
    q = _queue(("W1", "monitor"), ("W2", "diagnostic"))
    r = q.retry_all_failed_worms(max_retries=2, category="monitor")
    assert r["total_retried"] == 1
    assert set(r["per_category"]) == {"monitor"}


def test_summary_empty_window_reports_zero_not_none():
    q = _queue(("W1", "monitor"))
    s = q.get_dead_letter_summary(lookback_seconds=-1)
    assert s["total_failed"] == 0
    assert s["recent_errors"] == []


def test_retry_without_executor_reports_eligibility():
    q = _queue(("W1", "monitor"), ("W2", "diagnostic"))
    r = q.retry_all_failed_worms(max_retries=2)
    assert r["total_retried"] == 2
    assert r["still_failed"] == 2
    assert r["total_succeeded"] == 0
    assert q.count_pending() == 2, "reporting must not consume"


def test_retry_with_executor_replays():
    q = _queue(("W1", "monitor"))
    r = q.retry_all_failed_worms(
        max_retries=2, execute_fn=lambda w, c: {"success": True})
    assert r["total_retried"] == 1
    assert r["total_succeeded"] == 1
    assert r["still_failed"] == 0
