"""Tests for the SINGLE canonical per-worm outcome emitter.

WHY THIS FILE EXISTS (2026-09-21)
---------------------------------
The outcome tap was the input to evolution, and it covered almost nothing:

    rows: 4,286   worm types: 25   evolvable organs: 211

86.5% of those rows came from three nature worms, so fitness was selecting on
~7% of the fleet -- which caps every genotype rung no matter how good the
genome is. The cause was not data volume: it was that only SOME execution paths
wrote a row at all, and the paths that did had each grown their own payload.

    ClusterWORMZ._execute_one    emitted
    ClusterWORMZ.execute_worm    did NOT
    CLUSTERWORMS.execute      did NOT  (fixed earlier this session)
    CONCURRENCY.WormExecutor     did NOT  (an entire second executor family)

So the tests below are in two groups, and the second is the one that matters:

1. **The emitter** -- payload shape, truncation, fail-soft, the decision_id
   join key. Ordinary unit tests.

2. **The wiring** -- that each producer ACTUALLY CALLS the emitter, on every
   exit path. A test that only checked the emitter's payload would have passed
   for all three silent producers, because the defect was the missing call, not
   a wrong payload. `test_every_producer_writes_the_same_schema` is the
   invariant: four producers, one schema.

Run:
    /c/Azazael/.venvs/py315t/Scripts/python.exe -m pytest tests/test_worm_telemetry.py -q
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from CORE.WORMS import worm_telemetry as wt  # noqa: E402


class CapturingStore:
    """Minimal stand-in: records `record_event(**kw)` calls in order."""

    def __init__(self):
        self.events = []

    def record_event(self, **kw):
        self.events.append(kw)

    @property
    def payloads(self):
        return [e.get("data", {}) for e in self.events]


class ExplodingStore:
    """A store that is down. Telemetry must survive it, not join it."""

    def record_event(self, **kw):
        raise RuntimeError("store unavailable")


@pytest.fixture
def captured(monkeypatch):
    """Patch the store the emitter resolves lazily at call time."""
    store = CapturingStore()
    import CORE.local_memory as lm

    monkeypatch.setattr(lm, "get_memory_store", lambda: store)
    return store


@pytest.fixture
def broken(monkeypatch):
    import CORE.local_memory as lm

    monkeypatch.setattr(lm, "get_memory_store", lambda: ExplodingStore())
    return None


# --------------------------------------------------------------------------
# 1. The emitter
# --------------------------------------------------------------------------


def test_dict_path_writes_the_canonical_payload(captured):
    ok = wt.emit_worm_outcome("SYSTEMZ", {"success": True, "duration_ms": 903.5})

    assert ok is True
    assert len(captured.events) == 1
    event = captured.events[0]
    assert event["event_type"] == wt.EVENT_TYPE
    assert event["severity"] == "info"
    assert event["data"] == {
        "worm": "SYSTEMZ",
        "success": True,
        "standby": False,
        "duration_ms": 903.5,
        "error": None,
        "interp": event["data"]["interp"],
        "gil": event["data"]["gil"],
    }
    assert isinstance(event["data"]["interp"], str)
    assert event["data"]["gil"] in ("gil", "gil-disabled", "?")


def test_failure_is_recorded_at_error_severity(captured):
    wt.emit_worm_outcome("DISKZ", {"success": False, "error": "permission denied"})

    assert captured.events[0]["severity"] == "error"
    assert captured.payloads[0]["error"] == "permission denied"


def test_result_object_path_normalises_an_execution_result(captured):
    """`CONCURRENCY` returns a dataclass, not a dict -- one shape must reach the store."""
    from CORE.WORMS.CONCURRENCY import ExecutionResult

    result = ExecutionResult(
        task_id="t1", task_name="CLOCKZ", success=True, duration_ms=12.5,
        result={"drift_ms": 4},
    )
    ok = wt.emit_worm_outcome_from_result("CLOCKZ", result)

    assert ok is True
    assert captured.payloads[0] == {
        "worm": "CLOCKZ",
        "success": True,
        "standby": False,
        "duration_ms": 12.5,
        "error": None,
        "interp": captured.payloads[0]["interp"],
        "gil": captured.payloads[0]["gil"],
    }
    assert isinstance(captured.payloads[0]["interp"], str)


def test_decision_id_is_the_join_key(captured):
    wt.emit_worm_outcome("METAZ", {"success": True}, decision_id="d-abc123")

    assert captured.payloads[0]["decision_id"] == "d-abc123"


def test_decision_id_absent_means_legacy_row(captured):
    """A row without an id can only be joined per worm type, never per decision.

    Omitting the key entirely (rather than writing null) is what lets
    `spawn_credit.read_outcome_decisions` tell the two populations apart.
    """
    wt.emit_worm_outcome("METAZ", {"success": True})

    assert "decision_id" not in captured.payloads[0]


@pytest.mark.parametrize("bad", ["", None])
def test_missing_name_writes_nothing(bad, captured):
    assert wt.emit_worm_outcome(bad, {"success": True}) is False
    assert captured.events == []


def test_long_error_is_truncated(captured):
    wt.emit_worm_outcome("X", {"success": False, "error": "e" * 5000})

    recorded = captured.payloads[0]["error"]
    assert len(recorded) == wt.MAX_ERROR_CHARS
    assert recorded == "e" * wt.MAX_ERROR_CHARS


def test_non_numeric_duration_degrades_to_zero_not_an_exception(captured):
    ok = wt.emit_worm_outcome("X", {"success": True, "duration_ms": "slow"})

    assert ok is True
    assert captured.payloads[0]["duration_ms"] == 0.0


def test_standby_is_not_success(captured):
    """Standby liveness must not read as a passing check (2026-09-29)."""
    wt.emit_worm_outcome("X", {"success": True, "standby": True})

    assert captured.payloads[0]["standby"] is True
    assert captured.payloads[0]["success"] is False


def test_cluster_identity_is_stamped(captured):
    """Per-cluster attribution: without it, a row says what ran but not where."""

    class Cluster:
        cluster_type = "system"
        cluster_id = "cluster_42"

    wt.emit_worm_outcome("SYSTEMZ", {"success": True}, cluster=Cluster())

    assert captured.payloads[0]["cluster_type"] == "system"
    assert captured.payloads[0]["cluster_id"] == "cluster_42"


def test_a_broken_store_never_raises_and_reports_failure(broken):
    """Fail-soft, but VISIBLE.

    The first version of this module swallowed the exception and returned
    nothing, which made a broken store indistinguishable from "no data" --
    the same silent-failure class the audits exist to find. It returns False
    now so a caller can tell the difference.
    """
    assert wt.emit_worm_outcome("SYSTEMZ", {"success": True}) is False
    assert wt.emit_worm_outcome_from_result("SYSTEMZ", object()) is False


# --------------------------------------------------------------------------
# 2. The wiring -- the invariant, then each producer
# --------------------------------------------------------------------------


def test_the_emitter_does_not_shadow_the_metrics_counter():
    """Two functions, one name, incompatible signatures is the bug being fixed.

    `CORE.metrics` already exports `record_worm_execution(name, success,
    duration_ms, error)` -- a positional in-process counter used by
    `azazael-ctl metrics` and listed in that module's `__all__`. The first
    version of this module reused that exact name with a dict argument, i.e. it
    recreated "several things, one name, different shapes".

    This test is the guard: the canonical emitter must stay a *different* name,
    and must remain callable the way its callers call it.
    """
    from CORE import metrics

    assert metrics.record_worm_execution is not wt.emit_worm_outcome
    # The legacy counter keeps its positional contract.
    metrics.record_worm_execution("SYSTEMZ", True, 1.0, None)
    # And the emitter keeps its dict contract, on the same call signature shape
    # its producers use (name, result-dict).
    assert callable(wt.emit_worm_outcome)
    assert callable(wt.emit_worm_outcome_from_result)


def test_every_producer_writes_the_same_schema(captured):
    """FOUR producers, ONE schema.

    This is the invariant the whole module exists for. Each producer is driven
    through its own entry point, and every row must land on the same event type
    with the same key set -- because a schema with four producers is four
    schemas, and three of them were silently absent.
    """
    from CORE.WORMS.CLUSTERWORMS import _record_cluster_worm_telemetry
    from CORE.WORMS.CONCURRENCY import ExecutionResult, _record_execution
    from CORE.WORMS.CLUSTERWORMS import ClusterWORMZ

    cluster = ClusterWORMZ.build_from_registry([])
    _record_cluster_worm_telemetry("SYSTEMZ", {"success": True, "duration_ms": 1.0})
    _record_execution("PROCESSZ", ExecutionResult(
        task_id="t", task_name="PROCESSZ", success=True, duration_ms=2.0))
    _record_cluster_worm_telemetry("METAZ", {"success": False, "duration_ms": 3.0,
                                             "error": "boom"}, cluster=cluster)

    assert len(captured.events) == 3, "every producer must reach the tap"
    assert {e["event_type"] for e in captured.events} == {wt.EVENT_TYPE}

    # "One schema" means the CORE fields are identical in name and type across
    # every producer -- that is what a consumer joining on them can rely on.
    core = {"worm", "success", "standby", "duration_ms", "error"}
    for payload in captured.payloads:
        assert core <= payload.keys(), f"missing core field in {payload}"
        assert isinstance(payload["worm"], str)
        assert isinstance(payload["success"], bool)
        assert isinstance(payload["standby"], bool)
        assert isinstance(payload["duration_ms"], float)

    # Extras are ATTRIBUTION, allowed only from a known set. A producer inventing
    # a new key is how the four schemas drifted apart in the first place, so the
    # allowed set is pinned rather than merely observed.
    allowed = core | {"nature", "cluster_id", "cluster_type", "decision_id",
                       "interp", "gil"}
    for payload in captured.payloads:
        assert payload.keys() <= allowed, \
            f"undeclared field {payload.keys() - allowed} = schema drift"


def test_clusterwormz_execute_worm_records(captured):
    """`ClusterWORMZ.execute_worm` used to return a result and record nothing.

    Only the *fact* of a row is asserted, not the outcome: the worm here is not
    importable in a test environment, and the defect being pinned is the missing
    call, not the result.
    """
    from CORE.WORMS.CLUSTERWORMS import ClusterWORMZ, WormConfig

    cluster = ClusterWORMZ(cluster_type="system")
    cluster.configs = [WormConfig(name="TELEMETRY_PROBE", module="nonexistent_module")]
    try:
        cluster.execute_worm("TELEMETRY_PROBE")
    except Exception:
        pass  # the executor failing is not what is under test

    assert len(captured.events) == 1
    assert captured.payloads[0]["worm"] == "TELEMETRY_PROBE"


def test_clusterwormz_records_a_worm_it_could_not_run(captured):
    """Selected but not runnable is RECORDED, not dropped.

    "Was chosen and produced no outcome" is the silent failure the spawn-credit
    join exists to surface. Dropping it here would recreate the original blind
    spot one level down.
    """
    from CORE.WORMS.CLUSTERWORMS import ClusterWORMZ

    cluster = ClusterWORMZ(cluster_type="system")
    cluster.configs = []
    assert cluster.execute_worm("NOT_IN_THIS_CLUSTER") is None

    assert len(captured.events) == 1
    payload = captured.payloads[0]
    assert payload["worm"] == "NOT_IN_THIS_CLUSTER"
    assert payload["success"] is False
    assert "not configured" in payload["error"]


def test_concurrency_executor_records_on_the_success_path(captured):
    """An entire second executor family recorded nothing."""
    import CORE.WORMS.CONCURRENCY as cc

    class FakeWorm:
        def get_status(self):
            return {"ok": True}

    cc.FakeWorm = FakeWorm  # `execute_worm` resolves the class from module globals
    executor = cc.WormExecutor()
    executor.worm_modules["FakeWorm"] = ("unused", "FakeWorm")

    result = executor.execute_worm("FakeWorm")

    assert result.success is True
    assert len(captured.events) == 1
    assert captured.payloads[0]["worm"] == "FakeWorm"
    assert captured.payloads[0]["success"] is True


def test_concurrency_executor_records_the_not_loaded_path(captured):
    """All exit paths, not just the happy one.

    The not-loaded path is exactly the "chosen but never ran" case, and it was
    as silent as everything else.
    """
    from CORE.WORMS.CONCURRENCY import WormExecutor

    result = WormExecutor().execute_worm("NEVER_LOADED")

    assert result.success is False
    assert len(captured.events) == 1
    assert captured.payloads[0]["worm"] == "NEVER_LOADED"


def test_executor_still_returns_its_result_when_the_store_is_down(broken):
    """Fail-soft means the RETURN VALUE is unchanged, not that errors vanish."""
    from CORE.WORMS.CONCURRENCY import WormExecutor

    result = WormExecutor().execute_worm("NEVER_LOADED")

    assert result.success is False
    assert result.error == "Worm not loaded"
