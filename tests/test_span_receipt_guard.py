# coding: utf-8
"""A routed delegation must leave a span row, and the guard must say so.

The guard exists because ``emit_delegation`` fails silently in three ways:
tracing off, writer not importable, write failed. Each is exercised here through
the REAL router -- not by hand-building a receipt -- because the failure this
guards against is the router reporting success while the row is absent.

The positive control matters as much as the refusals: a guard that refuses every
route would pass all the negative cases and be useless.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_project_root, os.path.join(_project_root, "src"),
           os.path.join(_project_root, "src", "AOA"),
           os.path.join(_project_root, "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import CORE.cd3_exporter as cd3  # noqa: E402
import span_receipt_guard as guard  # noqa: E402
from core import router as router_mod  # noqa: E402
from core import delegation_tracer as tracer  # noqa: E402


class _Cap:
    def __init__(self, name):
        self.name = name


class _Arm:
    def __init__(self, arm_id="arm1"):
        self._arm_id = arm_id
        self.is_initialized = False
        self.metadata = type("M", (), {"priority": 5, "arm_id": arm_id})()

    def initialize(self):
        self.is_initialized = True
        return True

    def execute(self, capability, payload=None):
        return {"status": "ok", "data": {"arm": self._arm_id}}

    def get_capabilities(self):
        return [_Cap("find")]


class _Registry:
    def __init__(self, arms):
        self._arms = {a._arm_id: a for a in arms}

    @property
    def arms(self):
        return self._arms

    def get_arm(self, arm_id):
        return self._arms.get(arm_id)

    def list_arms(self):
        return [{"arm_id": a, "category": "q", "priority": 5} for a in self._arms]


class _Spine:
    def get_q_weight(self, nature, worm):
        return 0.0

    def get_q_updates(self, nature, worm):
        return 0

    def record_outcome(self, nature, worm, success, duration_ms, censored=False):
        return True


class _RouterFixture(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self._tmp.name) / "telemetry_store.db")
        self._orig_path = cd3.TELEMETRY_DB_PATH
        self._orig_env = os.environ.get("AZAZAEL_AOA_TRACING")
        self._orig_record = tracer.record_span
        cd3.TELEMETRY_DB_PATH = Path(self.db)
        cd3._schema_ready.discard(self.db)
        self._had_spine = hasattr(router_mod, "_spine")
        self._orig_spine = getattr(router_mod, "_spine", None)
        router_mod._spine = lambda: _Spine()
        os.environ.pop("AZAZAEL_AOA_TRACING", None)

    def tearDown(self):
        tracer.record_span = self._orig_record
        cd3.TELEMETRY_DB_PATH = self._orig_path
        cd3._schema_ready.discard(self.db)
        if self._orig_env is None:
            os.environ.pop("AZAZAEL_AOA_TRACING", None)
        else:
            os.environ["AZAZAEL_AOA_TRACING"] = self._orig_env
        if self._had_spine:
            router_mod._spine = self._orig_spine
        else:
            # pop, not `try: del / except: pass`: the fixture must restore the
            # module whether or not the attribute survived the test, and a
            # swallowed AttributeError would hide a fixture that had already
            # leaked.
            router_mod.__dict__.pop("_spine", None)
        self._tmp.cleanup()

    def route(self):
        from core.router import CrossArmRouter
        return CrossArmRouter(registry=_Registry([_Arm()])).route_task(None, "find")


class TestATracedRouteIsAccepted(_RouterFixture):

    def test_positive_control(self):
        flat = self.route()
        assert flat["status"] == "ok"
        assert flat["trace"]["trace_id"]
        receipt = guard.receipt_for(flat, db_path=self.db)
        assert guard.validate(receipt) == []
        assert receipt["found"] is True
        assert receipt["span_id"] == flat["trace"]["span_id"]

    def test_the_row_is_the_one_the_result_names(self):
        flat = self.route()
        receipt = guard.receipt_for(flat, db_path=self.db)
        # The name carries the pair, so a sibling span cannot satisfy the check.
        assert receipt["name"] == "delegation find -> arm1"

    def test_direct_ids_agree_with_the_result(self):
        flat = self.route()
        by_ids = guard.lookup(flat["trace"]["trace_id"],
                              flat["trace"]["span_id"], db_path=self.db)
        assert by_ids["found"] is True


class TestAnUntracedRouteIsRefused(_RouterFixture):

    def test_tracing_off_is_refused_and_names_the_environment(self):
        os.environ["AZAZAEL_AOA_TRACING"] = "off"
        flat = self.route()
        # The route still worked -- telemetry must not fail a route.
        assert flat["status"] == "ok"
        assert "trace" not in flat
        problems = guard.validate(guard.receipt_for(flat, db_path=self.db))
        assert [f for f, _ in problems] == ["trace"]
        assert "AZAZAEL_AOA_TRACING" in problems[0][1], problems[0][1]

    def test_a_failed_write_is_refused_even_though_the_router_says_ok(self):
        # The write reports True; guard proves the row is not there. This is the
        # case a receipt built from the writer's own return value cannot catch.
        # The database is real first, so the refusal is "no row", not "no file"
        # -- otherwise the test would pass for the wrong reason.
        cd3.record_span(trace_id="f" * 32, span_id="e" * 16,
                        name="delegation earlier -> arm0",
                        start_time=1, end_time=2, db_path=self.db)
        tracer.record_span = lambda **kwargs: True   # lies
        flat = self.route()
        assert flat["status"] == "ok"
        problems = guard.validate(guard.receipt_for(flat, db_path=self.db))
        assert [f for f, _ in problems] == ["found"], problems
        assert "no row in otel_spans" in problems[0][1], problems
        assert "0 row(s) exist under this trace_id" in problems[0][1], problems

    def test_writer_unavailable_is_refused(self):
        tracer.record_span = None
        flat = self.route()
        problems = guard.validate(guard.receipt_for(flat, db_path=self.db))
        assert [f for f, _ in problems] == ["trace"], problems

    def test_missing_database_is_reported_as_missing_not_as_zero_rows(self):
        os.environ["AZAZAEL_AOA_TRACING"] = "off"
        flat = self.route()
        os.environ.pop("AZAZAEL_AOA_TRACING", None)
        flat["trace"] = {"trace_id": "a" * 32, "span_id": "b" * 16}
        Path(self.db).unlink(missing_ok=True)
        receipt = guard.receipt_for(flat, db_path=self.db)
        assert receipt["found"] is False
        assert "does not exist" in receipt["reason"], receipt
        # And the check did not create it.
        assert not Path(self.db).exists()


class TestShapeRefusals(unittest.TestCase):

    def test_no_trace_key(self):
        assert [f for f, _ in guard.validate(guard.receipt_for({"status": "ok"}))] \
            == ["trace"]

    def test_a_trace_without_a_span_is_not_evidence(self):
        problems = guard.validate(
            {"trace_id": "a" * 32, "span_id": "", "found": True})
        assert [f for f, _ in problems] == ["span_id"]

    def test_found_must_be_a_bool(self):
        assert [f for f, _ in guard.validate(
            {"trace_id": "a" * 32, "span_id": "b" * 16, "found": 1})] == ["found"]

    def test_result_json_roundtrip(self):
        flat = {"status": "ok", "trace": {"trace_id": "a" * 32, "span_id": "b" * 16}}
        again = json.loads(json.dumps(flat))
        assert guard.receipt_for(again) is not None


if __name__ == "__main__":
    unittest.main()
