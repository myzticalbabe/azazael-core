# coding: utf-8
"""One learned action, actuated behind a gate.

`DatabaseMonitorWorm._maybe_policy_alert` promotes exactly ONE policy
action ("alert") from advisory to actuated: a known-True "alert" verdict
records ONE extra cooldown-deduped row tagged policy_actuated. These
tests pin the gate that keeps the promotion honest:

* known + "alert" actuates (extra row, policy metric, policy tag + data);
* unknown never actuates; ignore/log/remediate never actuate;
* the MONITOR_POLICY_ACTUATION=0 kill-switch actuates nothing;
* an exploding advice path fails open (False, base alert untouched);
* every report alert carries the advice (advisory half of the promotion);
* execute() drains through the gate (wiring, hermetic via fakes).

Hermetic: fake store (record spy, empty history), faked advice.
"""
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from CORE.WORMS.expansion.db_monitor import (  # noqa: E402
    DatabaseMonitorWorm,
    DbAlert,
    DbHealthSnapshot,
)


class FakeStore:
    """record_event spy; empty history so cooldowns never block."""

    def __init__(self):
        self.rows = []

    def record_event(self, **kw):
        self.rows.append(kw)
        return "fake-id"

    def query_memories(self, **kw):
        return []


def _worm():
    return DatabaseMonitorWorm(store=FakeStore())


def _alert(metric="db_size_mb", severity="warning"):
    return DbAlert(alert_id="a1", severity=severity, message="big",
                   metric=metric, value=1.0, threshold=0.5,
                   timestamp="2026-10-16T00:00:00+00:00")


def _known(action):
    return {"action": 2, "action_name": action, "known": True,
            "state_key": "db:warning", "source": "db"}


def _rows_of(worm):
    return worm._get_store().rows


def test_known_alert_actuates_one_tagged_row(monkeypatch):
    monkeypatch.delenv("MONITOR_POLICY_ACTUATION", raising=False)
    worm = _worm()
    monkeypatch.setattr(worm, "_policy_advice",
                        lambda sev: _known("alert"))
    assert worm._maybe_policy_alert(_alert()) is True
    rows = _rows_of(worm)
    assert len(rows) == 1
    row = rows[0]
    assert row["data"]["metric"] == "policy:db_size_mb"
    assert "policy_actuated" in row["tags"]
    assert row["data"]["policy_action"] == "alert"
    assert row["data"]["alert_id"] == "policy_a1"


def test_unknown_never_actuates(monkeypatch):
    monkeypatch.delenv("MONITOR_POLICY_ACTUATION", raising=False)
    worm = _worm()
    monkeypatch.setattr(worm, "_policy_advice",
                        lambda sev: {"action": -1, "action_name": "unknown",
                                     "known": False, "state_key": "db:warning",
                                     "source": "db"})
    assert worm._maybe_policy_alert(_alert()) is False
    assert _rows_of(worm) == []


def test_non_alert_actions_never_actuate(monkeypatch):
    monkeypatch.delenv("MONITOR_POLICY_ACTUATION", raising=False)
    for action in ("ignore", "log", "remediate"):
        worm = _worm()
        monkeypatch.setattr(worm, "_policy_advice",
                            lambda sev, _a=action: _known(_a))
        assert worm._maybe_policy_alert(_alert()) is False, action
        assert _rows_of(worm) == [], action


def test_kill_switch_actuates_nothing(monkeypatch):
    monkeypatch.setenv("MONITOR_POLICY_ACTUATION", "0")
    worm = _worm()
    monkeypatch.setattr(worm, "_policy_advice",
                        lambda sev: _known("alert"))
    assert worm._maybe_policy_alert(_alert()) is False
    assert _rows_of(worm) == []


def test_advice_explosion_fails_open(monkeypatch):
    monkeypatch.delenv("MONITOR_POLICY_ACTUATION", raising=False)
    worm = _worm()

    def _boom(sev):
        raise RuntimeError("table unreadable")

    monkeypatch.setattr(worm, "_policy_advice", _boom)
    assert worm._maybe_policy_alert(_alert()) is False
    assert _rows_of(worm) == []


def test_report_alerts_carry_advice(monkeypatch):
    worm = _worm()
    monkeypatch.setattr(worm, "_policy_advice",
                        lambda sev: _known("alert"))
    rep = worm._alert_report(_alert())
    assert rep["metric"] == "db_size_mb"  # base fields untouched
    assert rep["policy_advice"]["action_name"] == "alert"


def test_execute_drains_through_gate(monkeypatch):
    monkeypatch.delenv("MONITOR_POLICY_ACTUATION", raising=False)
    worm = _worm()
    monkeypatch.setattr(worm, "_policy_advice",
                        lambda sev: _known("alert"))
    snap = DbHealthSnapshot(
        timestamp="2026-10-16T00:00:00+00:00", db_size_mb=10.0,
        total_memories=1, total_sessions=1, memory_growth_24h=0,
        error_count_7d=0, warning_count_7d=0, sources_count=1,
        event_types_count=1, auto_vacuum_running=False,
        current_session="s", storage_backend="sqlite", healthy=True)
    monkeypatch.setattr(worm, "check_health", lambda: snap)
    worm._alerts.append(_alert())
    calls = []
    orig = worm._maybe_policy_alert
    monkeypatch.setattr(worm, "_maybe_policy_alert",
                        lambda a: calls.append(a.metric) or orig(a))
    report = worm.execute()
    assert report["total_alerts"] == 1
    assert calls == ["db_size_mb"]  # gate consulted once per alert
    metrics = [r["data"]["metric"] for r in _rows_of(worm)
               if r.get("event_type") == "db_health_alert"]
    assert "db_size_mb" in metrics  # base alert recorded
    assert "policy:db_size_mb" in metrics  # actuated row added
    assert report["alerts"][0]["policy_advice"]["known"] is True
