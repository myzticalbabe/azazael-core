# coding: utf-8
"""AOA delegation tracer — the outcome survives the process, and the control fires.

The defect being closed: ``router.route_task`` grades every delegation
(``flat["outcome"]``) and then the grade dies in a bounded in-memory deque, so
"was this arm the right choice" was answerable only from a live process. These
tests assert the grade reaches a durable row, and -- the part that matters -- that
the oracle can go quiet: with ``AZAZAEL_AOA_TRACING=off`` **nothing** is written.

Every test runs against a temp database. Redirecting
``CORE.cd3_exporter.TELEMETRY_DB_PATH`` is what makes that true; writing these
tests against the default path would append to the live
``~/.azazael/telemetry_store.db``, which holds the lane board.
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_AOA = os.path.join(_project_root, "src", "AOA")
for _p in (_project_root, _AOA, os.path.join(_project_root, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import CORE.cd3_exporter as cd3  # noqa: E402
from core import router as router_mod  # noqa: E402
from core.delegation_spec import DelegationSpec  # noqa: E402
from core.delegation_tracer import (  # noqa: E402
    SEMCONV_CORRELATION_ATTRIBUTES,
    SEMCONV_FIELD_ATTRIBUTES,
    build_attributes,
    delegation_outcomes,
    emit_delegation,
    tracing_enabled,
)

#: The ten field-mapping attribute names, written out by hand from
#: docs/OSS_OTEL_SEMCONV_AGENT_DELEGATION_2026-11-02.md. Frozen on purpose: a test
#: that reads the names from the module cannot fail when the module renames one.
_DRAFT_FIELD_ATTRIBUTES = {
    "agent.delegation.instruction",
    "agent.delegation.context",
    "agent.delegation.arm_id",
    "agent.delegation.capability",
    "agent.delegation.model",
    "agent.delegation.provenance",
    "agent.delegation.stale_after_s",
    "agent.delegation.produced_at",
    "agent.delegation.is_stale",
    "agent.delegation.status",
}
_DRAFT_CORRELATION_ATTRIBUTES = {
    "agent.delegation.trace_id",
    "agent.delegation.span_id",
    "agent.delegation.parent_span_id",
}


def _rows(db_path):
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM otel_spans ORDER BY start_time ASC")]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


class _FakeArm:
    """Minimal Arm: declares one capability and returns ok (or raises)."""

    def __init__(self, arm_id="arm1", caps=("get_status",), raises=None,
                 priority=5, status="ok"):
        self._arm_id = arm_id
        # ``CrossArmRouter.capability_index`` reads ``cap.name`` (or a dict's
        # "name"); a bare string is silently skipped, so an arm built with
        # ``caps=("get_status",)`` would be unroutable and every routing test
        # would pass through the no-candidate path without exercising this code.
        self._caps = [type("Cap", (), {"name": c})() for c in caps]
        self._raises = raises
        self._status = status
        self.is_initialized = False
        self.metadata = type("M", (), {"priority": priority,
                                       "arm_id": arm_id})()

    def initialize(self):
        self.is_initialized = True
        return True

    def execute(self, capability, payload=None):
        if self._raises is not None:
            raise self._raises
        return {"status": self._status, "data": {"arm": self._arm_id}}

    def get_capabilities(self):
        return list(self._caps)


class _Registry:
    def __init__(self, arms):
        self._arms = {a._arm_id: a for a in arms}

    @property
    def arms(self):
        return self._arms

    def get_arm(self, arm_id):
        return self._arms.get(arm_id)

    def list_arms(self):
        return [{"arm_id": a, "category": "q", "priority": 5}
                for a in self._arms]


class _Spine:
    def get_q_weight(self, nature, worm):
        return 0.0

    def get_q_updates(self, nature, worm):
        return 0

    def record_outcome(self, nature, worm, success, duration_ms,
                       censored=False):
        return True


class _Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self._tmp.name) / "telemetry_store.db")
        self._orig_path = cd3.TELEMETRY_DB_PATH
        self._orig_env = os.environ.get("AZAZAEL_AOA_TRACING")
        cd3.TELEMETRY_DB_PATH = Path(self.db)
        cd3._schema_ready.discard(self.db)
        self._had_spine = hasattr(router_mod, "_spine")
        self._orig_spine = getattr(router_mod, "_spine", None)
        router_mod._spine = lambda: _Spine()

    def tearDown(self):
        cd3.TELEMETRY_DB_PATH = self._orig_path
        cd3._schema_ready.discard(self.db)
        if self._orig_env is None:
            os.environ.pop("AZAZAEL_AOA_TRACING", None)
        else:
            os.environ["AZAZAEL_AOA_TRACING"] = self._orig_env
        if self._had_spine:
            router_mod._spine = self._orig_spine
        else:
            try:
                del router_mod._spine
            except AttributeError as exc:
                note_failure("tests/test_delegation_tracer.py", exc)
                pass
        self._tmp.cleanup()

    def router(self, *arms):
        from core.router import CrossArmRouter
        return CrossArmRouter(registry=_Registry(list(arms)))


class TestSemconvNamesMatchTheDraft(unittest.TestCase):

    def test_field_attribute_names(self):
        assert set(SEMCONV_FIELD_ATTRIBUTES) == _DRAFT_FIELD_ATTRIBUTES
        assert len(SEMCONV_FIELD_ATTRIBUTES) == 10

    def test_correlation_attribute_names(self):
        assert set(SEMCONV_CORRELATION_ATTRIBUTES) == \
            _DRAFT_CORRELATION_ATTRIBUTES


class TestBuildAttributes(_Fixture):

    def test_absent_fields_are_omitted_not_nulled(self):
        attrs = build_attributes({"status": "ok"}, capability="c", arm_id="a")
        for key in ("agent.delegation.provenance",
                    "agent.delegation.stale_after_s",
                    "agent.delegation.produced_at",
                    "agent.delegation.model",
                    "agent.delegation.trace_id"):
            assert key not in attrs, "%s should be absent, not null" % key

    def test_is_stale_is_three_state_and_always_present(self):
        attrs = build_attributes({"status": "ok"}, capability="c", arm_id="a")
        assert "agent.delegation.is_stale" in attrs
        assert attrs["agent.delegation.is_stale"] is None
        assert build_attributes({"status": "ok", "is_stale": True},
                                capability="c", arm_id="a")[
            "agent.delegation.is_stale"] is True
        assert build_attributes({"status": "ok", "is_stale": False},
                                capability="c", arm_id="a")[
            "agent.delegation.is_stale"] is False


try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where, exc=None):
        pass  # ledger missing; the swallow is still named at the call site

    def test_spec_supplies_the_delegation_time_fields(self):
        spec = DelegationSpec(instruction="cap [exploit]", context={"a": 1},
                              tools=["arm1"], model="m", reason="exploit")
        attrs = build_attributes({"status": "ok"}, capability="cap",
                                 arm_id="arm1", spec=spec)
        assert attrs["agent.delegation.instruction"] == "cap [exploit]"
        assert attrs["agent.delegation.model"] == "m"
        assert attrs["agent.delegation.context"] == '{"a": 1}'

    def test_router_outcome_fields_come_through(self):
        flat = {"status": "ok", "is_stale": True,
                "outcome": {"verdict": "unverified", "success": True,
                            "duration_ms": 1.5,
                            "contract_violations": ["stale"]}}
        attrs = build_attributes(flat, capability="c", arm_id="a")
        assert attrs["agent.delegation.verdict"] == "unverified"
        assert attrs["agent.delegation.success"] is True
        assert attrs["agent.delegation.duration_ms"] == 1.5
        assert attrs["agent.delegation.contract_violations"] == "stale"

    def test_an_arm_that_ignores_tracing_is_unaffected(self):
        # The attribute dict is built beside the payload, never inside it.
        payload = {"k": "v"}
        build_attributes({"status": "ok"}, capability="c", arm_id="a")
        assert payload == {"k": "v"}


class TestEmitWritesADurableRow(_Fixture):

    def test_router_writes_a_span_for_a_successful_delegation(self):
        flat = self.router(_FakeArm()).route_task(None, "get_status")
        assert flat["status"] == "ok"
        spans = _rows(self.db)
        assert len(spans) == 1, "expected exactly one span, got %r" % spans

        import json
        attrs = json.loads(spans[0]["attributes"])
        assert attrs["agent.delegation.arm_id"] == "arm1"
        assert attrs["agent.delegation.capability"] == "get_status"
        assert attrs["agent.delegation.status"] == "ok"
        assert attrs["agent.delegation.verdict"] == "accepted"
        assert spans[0]["name"] == "delegation get_status -> arm1"
        assert spans[0]["status_code"] == 1          # OTel STATUS_OK

    def test_the_result_carries_the_ids_back(self):
        flat = self.router(_FakeArm()).route_task(None, "get_status")
        trace = flat["trace"]
        assert len(trace["trace_id"]) == 32
        assert len(trace["span_id"]) == 16
        assert _rows(self.db)[0]["trace_id"] == trace["trace_id"]

    def test_span_width_is_the_router_timer_not_a_second_clock(self):
        flat = self.router(_FakeArm()).route_task(None, "get_status")
        row = _rows(self.db)[0]
        width_ms = (row["end_time"] - row["start_time"]) / 1_000_000.0
        reported = flat["outcome"]["duration_ms"]
        assert abs(width_ms - reported) < 0.001, (width_ms, reported)

    def test_a_failing_arm_is_recorded_too(self):
        arm = _FakeArm(raises=RuntimeError("boom"))
        flat = self.router(arm).route_task(None, "get_status")
        assert flat["status"] == "error"
        spans = _rows(self.db)
        assert len(spans) == 1, "a failed delegation must still leave a span"
        import json
        attrs = json.loads(spans[0]["attributes"])
        assert attrs["agent.delegation.status"] == "error"
        assert attrs["agent.delegation.verdict"] == "rejected"
        assert spans[0]["status_code"] == 2          # OTel STATUS_ERROR

    def test_parent_link_survives_when_the_spec_has_one(self):
        spec = DelegationSpec(instruction="i", trace_id="T" * 32,
                              span_id="S" * 16, parent_span_id="P" * 16)
        emit_delegation({"status": "ok"}, capability="c", arm_id="a",
                        spec=spec)
        row = _rows(self.db)[0]
        assert row["trace_id"] == "T" * 32
        assert row["span_id"] == "S" * 16
        assert row["parent_span_id"] == "P" * 16


class TestNegativeControls(_Fixture):
    """An oracle that cannot go quiet is not an oracle."""

    def test_tracing_off_writes_nothing_and_says_nothing(self):
        os.environ["AZAZAEL_AOA_TRACING"] = "off"
        assert tracing_enabled() is False
        flat = self.router(_FakeArm()).route_task(None, "get_status")
        assert flat["status"] == "ok", "disabling telemetry must not break routing"
        assert "trace" not in flat
        assert _rows(self.db) == []

    def test_the_same_call_with_tracing_on_does_write(self):
        # The pair is the control: off -> 0 rows, on -> 1 row. Without the
        # second half, a broken writer would look like a working switch.
        os.environ["AZAZAEL_AOA_TRACING"] = "off"
        self.router(_FakeArm()).route_task(None, "get_status")
        assert _rows(self.db) == []

        os.environ.pop("AZAZAEL_AOA_TRACING", None)
        flat = self.router(_FakeArm()).route_task(None, "get_status")
        assert "trace" in flat
        assert len(_rows(self.db)) == 1

    def test_an_unknown_trace_id_reads_as_empty(self):
        self.router(_FakeArm()).route_task(None, "get_status")
        assert delegation_outcomes("f" * 32) == []
        assert delegation_outcomes("") == []

    def test_outcome_query_returns_only_its_own_trace(self):
        first = self.router(_FakeArm()).route_task(None, "get_status")
        self.router(_FakeArm(arm_id="arm2")).route_task(None, "get_status")
        assert len(_rows(self.db)) == 2

        outcomes = delegation_outcomes(first["trace"]["trace_id"])
        assert len(outcomes) == 1, outcomes
        assert outcomes[0]["arm_id"] == "arm1"
        assert outcomes[0]["capability"] == "get_status"
        assert outcomes[0]["verdict"] == "accepted"

    def test_a_write_failure_does_not_fail_the_route(self):
        # Point the tracer at a DIRECTORY: sqlite3 cannot open it, so the write
        # fails. First draft of this test used a missing nested directory and
        # passed for the wrong reason -- ``_ensure_db_dir`` mkdirs parents, so
        # the write succeeded. The failure has to be one the code cannot repair.
        cd3._schema_ready.discard(self.db)
        cd3.TELEMETRY_DB_PATH = Path(self._tmp.name)
        flat = self.router(_FakeArm()).route_task(None, "get_status")
        assert flat["status"] == "ok", "a failed span write must not fail the route"
        assert "trace" not in flat


class TestUninstallableWriterIsInert(unittest.TestCase):

    def test_no_writer_means_no_span_and_no_exception(self):
        original = sys.modules["core.delegation_tracer"].record_span
        try:
            sys.modules["core.delegation_tracer"].record_span = None
            assert emit_delegation({"status": "ok"}, capability="c",
                                   arm_id="a") is None
        finally:
            sys.modules["core.delegation_tracer"].record_span = original


if __name__ == "__main__":
    unittest.main()
