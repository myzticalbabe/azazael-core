"""test_boxfit_adopt_887.py — Hermetic measurements for the 6 box-fit ADOPT slices (#887).

Every test is hermetic: fabricated inputs, no store reads/writes, no inference,
no model. The one exception (RCPD calls record_backtrack, which reaches the
audit ledger) is neutralized by stubbing the module-global record_backtrack
for the duration of the test — documented, restored via addCleanup.

Each test prints one MEASURE line: BEFORE vs AFTER numbers, not adjectives.
Run: python3.15t.exe -m pytest tests/test_boxfit_adopt_887.py -q -s -p no:cacheprovider
"""
import os
import random
import sqlite3
import sys
import threading
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, os.path.join(_ROOT, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from CORE.WORMS.expansion import rcpd_early_exit as rcpd_mod  # noqa: E402
from CORE.WORMS.expansion.rcpd_early_exit import RCPDEarlyExit  # noqa: E402
from CORE.WORMS.expansion.schema_instruction_channel import (  # noqa: E402
    InstructionChannel, InstructionSchema, InstructionSchemaError, InstructionShape,
)
from CORE.WORMS.expansion.hybrid_rewards import (  # noqa: E402
    HybridRewardComputer, RewardConfig, RewardSource,
)
from CORE.WORMS.expansion.lazy_replan import (  # noqa: E402
    LazyReplanner, ReplanTrigger,
)
from CORE.WORMS.expansion.utility_guided_orchestration import (  # noqa: E402
    OrchestrationAction, OrchestrationContext, UtilityEngine, UtilityGuidedOrchestrator,
)
from CORE.WORMS.expansion.xmemory_decouple_aggregate import (  # noqa: E402
    MemoryBank, XMemoryDecoupler,
)


def _schema():
    return InstructionSchema(
        shape=InstructionShape.EXECUTE,
        required_fields={"tool", "params"},
        optional_fields={"trace_id"},
        field_types={"tool": str, "params": dict},
    )


def test_schema_channel_enforcement():
    """BEFORE: raw dict passthrough enforces 0/20. AFTER: channel rejects 20/20."""
    malformed = []
    for i in range(5):
        malformed.append({"tool": "grep"})  # missing params
    for i in range(5):
        malformed.append({"tool": "grep", "params": "not-a-dict"})  # wrong type
    for i in range(5):
        malformed.append({"tool": "grep", "params": {}, "evil": 1})  # extra key
    for i in range(5):
        malformed.append({})  # empty
    assert len(malformed) == 20

    # BEFORE: no enforcement layer — every malformed payload passes through.
    unenforced = sum(1 for p in malformed if isinstance(p, dict))
    assert unenforced == 20

    # AFTER: typed channel rejects all 20, accepts 6/6 well-formed.
    ch = InstructionChannel(send_schema=_schema())
    rejected = 0
    for p in malformed:
        try:
            ch.send(dict(p))
        except InstructionSchemaError:
            rejected += 1
    assert rejected == 20, rejected
    accepted = 0
    for i in range(6):
        out = ch.send({"tool": "grep", "params": {"q": str(i)}})
        assert out["_schema_version"] == "execute"
        accepted += 1
    assert accepted == 6
    print(f"MEASURE schema enforcement_before={unenforced - rejected + 0}/20 "
          f"enforcement_after={rejected}/20 wellformed_accepted={accepted}/6")


def test_schema_optional_field_accepted_and_validated():
    # Kills Or@44 (optional_fields or set -> and: drops the declared key)
    # and const True@72 (validate True -> False): the declared optional
    # key must validate, and validate must report True.
    sch = _schema()
    assert sch.validate({"tool": "grep", "params": {},
                         "trace_id": "t-1"}) is True
    print("MEASURE schema optional_field_accepted=1/1 validate_true=1/1")


class _StubBacktrack(unittest.TestCase):
    def runTest(self):
        pass


def test_rcpd_overthinking_exits_at_flip_3():
    """Overthinking run (3 flips) exits saving 225/512 tokens; calm run saves 0."""
    real = rcpd_mod.record_backtrack
    rcpd_mod.record_backtrack = lambda b, l, **kw: "stub-citation"
    try:
        # AFTER: overthinking generation — 3 backtrack flips cross threshold 3.
        r = RCPDEarlyExit(flip_threshold=3, max_tokens=512)
        for i in range(3):
            r.evaluate_flip(f"branch-{i}", f"lesson-{i}")
        assert r.should_early_exit() is True
        assert r.flip_count == 3
        assert r.tokens_saved == int(512 * 0.44) == 225
        # BEFORE-equivalent: calm generation, zero flips — no exit, nothing saved.
        c = RCPDEarlyExit(flip_threshold=3, max_tokens=512)
        out = c.wrap_generation(lambda prompt, **kw: "short answer", "q")
        assert out["early_exit"] is False
        assert out["tokens_saved"] == 0
        print(f"MEASURE rcpd flips_to_exit=3 tokens_saved={r.tokens_saved}/512 "
              f"calm_saved={out['tokens_saved']}")
    finally:
        rcpd_mod.record_backtrack = real


def test_xmemory_bank_isolation():
    """Same key in 4 banks: version vectors advance independently (4/4 isolated)."""
    xm = XMemoryDecoupler()
    written = [xm.write(b, "shared-key", f"v-{b.value}") for b in MemoryBank]
    assert all(w is True for w in written), written
    isolated = 0
    for b in MemoryBank:
        vv = xm._version_vectors[b]
        own = vv.banks.get(b, 0)
        others = [vv.banks.get(o, 0) for o in MemoryBank if o is not b]
        if own == 1 and all(o == 0 for o in others):
            isolated += 1
    assert isolated == 4, isolated
    # Read fidelity: data layer unconnected (_do_read returns None) — named gap.
    fidelity = sum(1 for b in MemoryBank if xm.read(b, "shared-key") is not None)
    second = xm.write(MemoryBank.SHORT_TERM, "shared-key", "v2")
    assert isinstance(second, bool)
    print(f"MEASURE xmemory bank_isolation={isolated}/4 read_fidelity={fidelity}/4 "
          f"repeat_write_returns={second} ops_logged={len(xm._ops)}")


def test_lazy_replan_skips_steady_state():
    """20-step scenario, 2 real changes: naive=20 full replans, lazy=2 (90% fewer)."""
    rp = LazyReplanner()
    steady = "state-" + "A" * 64
    changed1 = "state-" + "B" * 64
    changed2 = "state-" + "C" * 64
    states = [steady] * 18 + [changed1, changed2]
    assert len(states) == 20
    fired = sum(1 for s in states
                if rp.should_replan(s, trigger=ReplanTrigger.STATE_DELTA))
    assert fired == 2, fired
    # Tool-failure trigger: 3 consecutive failures trip exactly once then reset.
    rp2 = LazyReplanner()
    for _ in range(3):
        rp2.record_failure()
    assert rp2.should_replan("x", trigger=ReplanTrigger.TOOL_FAILURE) is True
    assert rp2.should_replan("x", trigger=ReplanTrigger.TOOL_FAILURE) is False
    print(f"MEASURE lazyreplan naive_full_replans=20 lazy_full_replans={fired} "
          f"reduction_pct={(20 - fired) * 100 // 20}")


def test_hybrid_reward_differs_from_outcome_only():
    """Fixed inputs: outcome-only O vs hybrid H (both in [0,1], H != O)."""
    outcome = HybridRewardComputer(RewardConfig(
        source_weights={RewardSource.ENVIRONMENT: 1.0}))
    for _ in range(3):
        outcome.update(RewardSource.ENVIRONMENT, 0.8)
    hybrid = HybridRewardComputer(RewardConfig(source_weights={
        RewardSource.ENVIRONMENT: 0.5, RewardSource.USER_FEEDBACK: 0.2,
        RewardSource.TOOL_OUTPUT: 0.2, RewardSource.PREDICTIVE: 0.1}))
    for _ in range(3):
        hybrid.update(RewardSource.ENVIRONMENT, 0.8)
    for _ in range(2):
        hybrid.update(RewardSource.USER_FEEDBACK, 0.6)
    hybrid.update(RewardSource.TOOL_OUTPUT, 0.4)
    hybrid.update(RewardSource.PREDICTIVE, 0.9)
    o = outcome.compute_hybrid()
    h = hybrid.compute_hybrid()
    assert 0.0 <= o <= 1.0 and 0.0 <= h <= 1.0
    assert h != o, (h, o)
    print(f"MEASURE hybrid outcome_only={o:.4f} hybrid={h:.4f} "
          f"lambda_trap_untestable_no_param=True")


def test_utility_orchestrator_explicit_trade():
    """10 steps default thresholds: 10/10 PROCEED recorded; strict threshold: DEFER."""
    eng = UtilityEngine()
    orch = UtilityGuidedOrchestrator(eng)
    ctx = OrchestrationContext(step=0, available_tools=("grep", "pytest", "git", "rg"),
                               user_intent="measure")
    chosen = [orch.orchestrate(
        [OrchestrationAction.PROCEED, OrchestrationAction.DEFER,
         OrchestrationAction.TERMINATE], ctx)[0] for _ in range(10)]
    assert all(c == OrchestrationAction.PROCEED for c in chosen), chosen
    assert len(orch._history) == 10
    strict = eng.decide(
        [OrchestrationAction.PROCEED, OrchestrationAction.TERMINATE], ctx,
        utility_threshold=0.99)
    assert strict[0] == OrchestrationAction.DEFER
    print(f"MEASURE utility default_proceed={sum(1 for c in chosen if c.value == 'proceed')}/10 "
          f"history={len(orch._history)} strict_fallback={strict[0].value}")


def _mem_db(rows):
    """In-memory memories DB; never touches the live store."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                 " session_id TEXT, source TEXT, event_type TEXT, data TEXT,"
                 " severity TEXT, tags TEXT, timestamp TEXT)")
    for et in rows:
        conn.execute("INSERT INTO memories(session_id,source,event_type,data,"
                     "severity,tags,timestamp) VALUES(?,?,?,?,?,?,?)",
                     ("s", "Codebuff", et, "{}", "info", "[]", "t"))
    conn.commit()
    return conn


def test_xmemory_tracker_recall_integration(monkeypatch):
    """Producer _decouple_rows <- caller _DirectStore.recall_recent.

    Consumer: CodebuffTracker/local_memory recall readers (additive keys).
    """
    from CORE.codebuff_tracker import _DirectStore
    conn = _mem_db(["define_api", "file_edit", "heartbeat_tick"])
    inst = object.__new__(_DirectStore)
    inst._get_conn = lambda: conn
    rows = inst.recall_recent(limit=10)
    assert len(rows) == 3
    kinds = {r["_xkind"] for r in rows}
    assert kinds == {"fact", "update", "detail"}, kinds
    assert all(r["_bank"] == "short_term" for r in rows)
    print(f"MEASURE xrecall tagged=3/3 kinds={sorted(kinds)}")


def test_xmemory_directmemory_recall_integration(monkeypatch):
    """Same tags on the LIVE DirectMemory.recall_recent path (inline, no import)."""
    from CORE.direct_memory import DirectMemory
    conn = _mem_db(["define_api", "file_edit", "heartbeat_tick"])
    inst = object.__new__(DirectMemory)
    inst._lock = threading.Lock()
    inst._get_conn = lambda: conn
    rows = inst.recall_recent(limit=10)
    assert len(rows) == 3
    kinds = {r["_xkind"] for r in rows}
    assert kinds == {"fact", "update", "detail"}, kinds
    print(f"MEASURE dm_recall tagged=3/3 kinds={sorted(kinds)}")


def test_hybrid_train_history_integration():
    """Producer hybrid_reward <- caller train loop; consumer: ops log line."""
    from AZAZAEL_TOOLKIT.QL_ANALYSIS import QLearningAnalyzer
    random.seed(0)
    qa = QLearningAnalyzer()
    qa.train(episodes=2)
    assert len(qa.hybrid_history) == 20, len(qa.hybrid_history)
    assert all(0.0 <= h <= 1.0 for h in qa.hybrid_history)
    mean_h = sum(qa.hybrid_history) / len(qa.hybrid_history)
    print(f"MEASURE hybrid_train episodes=2 steps=20 mean_hybrid={mean_h:.4f}")


def test_utility_propose_metadata_integration():
    """Producer _attach_utility <- caller propose; consumer audit_trail/pending."""
    from eve_unleashed.decision_engine import AutonomousDecisionEngine

    class _FakeRecorder:
        def __init__(self):
            self.rows = []

        def record_event(self, **kw):
            self.rows.append(kw)
            return 1

    fake = _FakeRecorder()
    eng = AutonomousDecisionEngine(recorder=fake)
    ds = [eng.propose(f"act-{i}", "r", 0.5, risk="low") for i in range(3)]
    assert all("utility" in d.metadata for d in ds)
    assert all(d.metadata["utility"]["action"] == "proceed" for d in ds)
    trail = eng.audit_trail()
    assert len(trail) == 3
    assert all("utility" in t["metadata"] for t in trail)
    print(f"MEASURE utility_propose tagged=3/3 audit={len(trail)} "
          f"u0={ds[0].metadata['utility']['expected_utility']:.4f}")
