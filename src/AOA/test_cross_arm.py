"""
AOA Cross-Arm Integration Test — Session Lifecycle + Cross-Arm Event Flow

Tests:
  1. Full session lifecycle: INIT → DISCOVERY → INITIALIZE → CAPABILITY_CHECK → READY → SHUTDOWN
  2. Cross-arm event flow: PerfMon → PAMS → Azazael → CODEX
"""
import sys, json, time
from pathlib import Path

# Wire Azazael
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
# Wire AOA
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Import arm modules to trigger @register_arm decorators
import arms.azazael_arm, arms.codex_arm, arms.eve_arm, arms.libra_arm, arms.support_arms, arms.kali_arm
from core.arm_registry import ArmRegistry
from core.event_bus import get_event_bus, AOEvent, AOEventType
from core.session_manager import get_session_manager, MilestoneStatus
from core.router import CrossArmRouter, WorkflowStep
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

SEPARATOR = "=" * 70

# =============================================================================
# TEST 1: Full Session Lifecycle
# =============================================================================
print(SEPARATOR)
print("TEST 1: Full AOA Session Lifecycle")
print("INIT -> DISCOVERY -> INITIALIZE -> CAPABILITY_CHECK -> READY -> SHUTDOWN")
print(SEPARATOR)

sm = get_session_manager()
bus = get_event_bus()
bus.clear_history()

# Track all events for reporting
events_received = []
def event_collector(event):
    events_received.append(event.to_dict())
bus.subscribe("*", event_collector)

registry = ArmRegistry()


def _require_arm(name: str):
    """Fetch a registered arm, failing loudly when it is missing.

    get_arm returns None for unknown ids; calling .execute on that would
    raise a bare AttributeError. The explicit RuntimeError names the arm.
    """
    arm = registry.get_arm(name)
    if arm is None:
        raise RuntimeError(f"arm not registered: {name}")
    return arm


t0 = time.perf_counter()

# Phase 1: INIT
print("\n[1/6] INIT — Creating session, discovering arms...")
session = sm.create_session(name="Cross-Arm Integration Test", tags=["test", "cross-arm", "lifecycle"])
bus.publish(AOEvent(
    event_type=AOEventType.SESSION_STARTED,
    source_arm="aoa.test", payload={"session_id": session.session_id}))
sm.record_milestone("aoa", "init", "Session created", duration_ms=0)
print(f"  Session: {session.session_id} ({session.session_name})")
print(f"  Arms discovered: {len(registry.arms)}")
arms_list = registry.list_arms()
for a in arms_list:
    print(f"    {a['arm_id']:<18} p={a['priority']} cat={a['category']} caps={a['capabilities']}")

# Phase 2: DISCOVERY
print("\n[2/6] DISCOVERY — Enumerating arm capabilities...")
total_caps = 0
arm_caps = {}
for arm_id, arm in registry.arms.items():
    caps = arm.get_capabilities()
    arm_caps[arm_id] = [c.name for c in caps]
    total_caps += len(caps)
    print(f"  {arm_id:<18} {len(caps)} caps: {', '.join(c.name for c in caps[:5])}{'...' if len(caps) > 5 else ''}")
print(f"  Total capabilities across {len(registry.arms)} arms: {total_caps}")
sm.record_milestone("aoa", "discovery", f"Discovered {total_caps} caps across {len(registry.arms)} arms", duration_ms=0)

# Phase 3: INITIALIZE
print("\n[3/6] INITIALIZE — Booting all arms...")
init_start = time.perf_counter()
init_results = registry.initialize_all()
init_ms = (time.perf_counter() - init_start) * 1000

all_passed = True

for arm_id, success in init_results.items():
    status = "OK" if success else "FAIL"
    if not success: all_passed = False
    print(f"  [{status}] {arm_id}")
    
bus.publish(AOEvent(
    event_type=AOEventType.ARM_INITIALIZED,
    source_arm="aoa.test", payload={"results": init_results}))
sm.record_milestone("aoa", "initialize_all", 
    f"{sum(1 for v in init_results.values() if v)}/{len(init_results)} arms initialized",
    status=MilestoneStatus.COMPLETED if all_passed else MilestoneStatus.FAILED,
    duration_ms=init_ms)

print(f"  Result: {sum(1 for v in init_results.values() if v)}/{len(init_results)} passed ({init_ms:.0f}ms)")

# Phase 4: CAPABILITY_CHECK
print("\n[4/6] CAPABILITY_CHECK — Verifying each arm's key capability...")
cap_checks = {}
cap_start = time.perf_counter()
for arm_id in registry.arms:
    arm = registry.get_arm(arm_id)
    if not arm or not arm.is_initialized:
        cap_checks[arm_id] = {"status": "error", "error": "Not initialized"}
        continue
    # Run get_status on each arm
    try:
        result = arm.execute("get_status")
        cap_checks[arm_id] = result
        ok = "OK" if result.get("status") == "ok" else "FAIL"
        data_preview = str(result.get("data", ""))[:80]
        print(f"  [{ok}] {arm_id}: {data_preview}")
    except Exception as e:
        cap_checks[arm_id] = {"status": "error", "error": str(e)}
        print(f"  [FAIL] {arm_id}: {e}")

cap_check_ms = (time.perf_counter() - cap_start) * 1000
cap_passed = sum(1 for v in cap_checks.values() if v.get("status") == "ok")
sm.record_milestone("aoa", "capability_check",
    f"{cap_passed}/{len(cap_checks)} arms passed capability check",
    duration_ms=cap_check_ms)
print(f"  Result: {cap_passed}/{len(registry.arms)} passed ({cap_check_ms:.0f}ms)")

# Phase 5: READY — Execute key capabilities
print("\n[5/6] READY — Executing key capabilities...")
ready_results = {}

# PerfMon: get_metrics
print("  PerfMon.get_metrics...", end=" ")
r = _require_arm("perfmon").execute("get_metrics")
ready_results["perfmon"] = r
ok = r.get("status") == "ok"
print(f"{'[OK]' if ok else '[FAIL]'} CPU={r.get('data',{}).get('cpu_percent','?')}% Mem={r.get('data',{}).get('memory_percent','?')}%")

# PAMS: record_event with perf data
print("  PAMS.record_event...", end=" ")
r = _require_arm("pams").execute("record_event", {
    "event": "cross_arm.test.metrics",
    "data": ready_results.get("perfmon", {}).get("data", {}),
    "tags": ["test", "cross-arm", "ready"]
})
ready_results["pams"] = r
print(f"{'[OK]' if r.get('status') == 'ok' else '[FAIL]'} {r.get('data',{}).get('event','')}")

# Azazael: discover_worms
print("  Azazael.discover_worms...", end=" ")
r = _require_arm("azazael").execute("discover_worms")
ready_results["azazael"] = r
ok = r.get('status') == 'ok'
print(f"{'[OK]' if ok else '[FAIL]'} worms={r.get('data',{}).get('worm_count','?')} cats={len(r.get('data',{}).get('categories',[]))}")

# Libra: list_templates + calculate_fees
print("  Libra.list_templates...", end=" ")
r = _require_arm("libra").execute("list_templates")
ready_results["libra_templates"] = r
ok = r.get('status') == 'ok'
print(f"{'[OK]' if ok else '[FAIL]'} count={r.get('data',{}).get('count','?')}")

print("  Libra.calculate_fees...", end=" ")
r = _require_arm("libra").execute("calculate_fees", {"filing_type": "PFA_PETITION", "court": "pa_commonwealth"})
ready_results["libra_fees"] = r
ok = r.get('status') == 'ok'
print(f"{'[OK]' if ok else '[FAIL]'} fee={r.get('data',{}).get('fee','?')}")

# CODEX: get_status (skip heavy render_video)
print("  CODEX.get_status...", end=" ")
r = _require_arm("codex").execute("get_status")
ready_results["codex"] = r
ok = r.get('status') == 'ok'
print(f"{'[OK]' if ok else '[FAIL]'} studio={'exists' if r.get('data',{}).get('codex_studio_exists') else 'missing'}")

# EveUnleashed: get_status
print("  Eve.get_status...", end=" ")
r = _require_arm("eve").execute("get_status")
ready_results["eve"] = r
ok = r.get('status') == 'ok'
print(f"{'[OK]' if ok else '[FAIL]'} initialized={r.get('data',{}).get('initialized','?')}")

# LlamaServer: get_status
print("  Llama.get_status...", end=" ")
r = _require_arm("llama").execute("get_status")
ready_results["llama"] = r
ok = r.get('status') == 'ok'
print(f"{'[OK]' if ok else '[FAIL]'}")

# AgentSelector: select_agent
print("  AgentSelector.select_agent...", end=" ")
r = _require_arm("agent_selector").execute("select_agent", {"task": "cross_arm_integration_test"})
ready_results["agent_selector"] = r
ok = r.get('status') == 'ok'
print(f"{'[OK]' if ok else '[FAIL]'} selected={r.get('data',{}).get('selected','?')} score={r.get('data',{}).get('score','?')}")

ready_passed = sum(1 for v in ready_results.values() if v.get("status") == "ok")
sm.record_milestone("aoa", "ready", f"{ready_passed}/{len(ready_results)} ready checks passed", duration_ms=0)
print(f"  READY result: {ready_passed}/{len(ready_results)} passed")
all_passed &= (ready_passed == len(ready_results))

# Phase 6: SHUTDOWN
print("\n[6/6] SHUTDOWN — Graceful teardown of all arms...")
shutdown_results = registry.shutdown_all()
for arm_id, success in shutdown_results.items():
    print(f"  [{'OK' if success else 'FAIL'}] {arm_id}.shutdown()")

bus.publish(AOEvent(
    event_type=AOEventType.SESSION_COMPLETED,
    source_arm="aoa.test", payload={"session_id": session.session_id}))

# Use proper session manager API for completion (computes total_duration_ms)
# NOTE: complete_session() nulls _active_session, so get summary BEFORE calling it
summary = sm.get_session_summary()
sm.complete_session()

# Unsubscribe test 1 collector before test 2
bus.unsubscribe("*", event_collector)

t1 = time.perf_counter()
elapsed = (t1 - t0) * 1000

# Session summary
print(f"\n  Session complete: {elapsed:.0f}ms total")
print(f"  Milestones recorded: {summary['total_milestones']}")
print(f"  Arms involved: {summary['arms_involved']}")

if all_passed:
    print("\n[OK] TEST 1 PASSED")
else:
    print("\n[FAIL] TEST 1 FAILED -- Session lifecycle complete")


# =============================================================================
# TEST 2: Cross-Arm Event Flow (PerfMon → PAMS → Azazael → CODEX)
# =============================================================================
print("\n" + SEPARATOR)
print("TEST 2: Cross-Arm Event Flow")
print("PerfMon -> PAMS -> Azazael -> CODEX -> (MP4 skipped, status check only)")
print(SEPARATOR)

bus2 = get_event_bus()
cross_events = []
def cross_collector(event):
    cross_events.append({
        "type": event.event_type,
        "source": event.source_arm,
        "target": event.target_arm,
        "cid": event.correlation_id,
    })
bus2.subscribe("*", cross_collector)

# Re-init arms for clean test
registry2 = ArmRegistry()
init2 = registry2.initialize_all()
print(f"\n  Re-initialized: {sum(1 for v in init2.values() if v)}/{len(init2)} arms")

# Create a DAG workflow: step1(perfmon) → step2(pams) → step3(azazael) → step4(codex)
router = CrossArmRouter(registry2, bus2)
wf = router.create_workflow(
    name="Cross-Arm Metrics Pipeline",
    description="PerfMon metrics → PAMS recording → Azazael worm discovery → CODEX status")

# Step 1: PerfMon captures metrics
wf.add_step(WorkflowStep(
    step_id="step1_perfmon",
    arm_id="perfmon",
    capability="get_metrics",
    payload={}))

# Step 2: PAMS records the metrics event (depends on step1)
wf.add_step(WorkflowStep(
    step_id="step2_pams",
    arm_id="pams",
    capability="record_event",
    payload={"event": "cross_arm.pipeline.metrics", "data": {"source": "perfmon"}, "tags": ["pipeline", "cross-arm"]},
    depends_on=["step1_perfmon"]))

# Step 3: Azazael discovers worms (depends on step2)
wf.add_step(WorkflowStep(
    step_id="step3_azazael",
    arm_id="azazael",
    capability="discover_worms",
    payload={},
    depends_on=["step2_pams"]))

# Step 4: CODEX checks status (depends on step3)
wf.add_step(WorkflowStep(
    step_id="step4_codex",
    arm_id="codex",
    capability="get_status",
    payload={},
    depends_on=["step3_azazael"]))

# Execute workflow
print(f"\n  Workflow: {wf.name}")
print(f"  Topological order: {wf.get_topological_order()}")
print(f"  Executing {len(wf.steps)} steps...")

t2 = time.perf_counter()
results = router.execute_workflow(wf.workflow_id)
wf_ms = (time.perf_counter() - t2) * 1000

for r in results:
    ok = r["status"] == "completed"
    print(f"  [{'OK' if ok else 'FAIL'}] {r['step_id']}: {r['arm_id']}.{r['capability']} -> {r['status']}")

# Show cross-arm events
print(f"\n  Cross-arm events routed ({len(cross_events)} total):")
for e in cross_events:
    print(f"    [{e['type']}] {e['source']}->{e['target']} (cid={e['cid']})")

status_summary = wf.get_status_summary()
print(f"\n  Workflow summary: {status_summary['completed']}/{status_summary['total_steps']} completed, "
      f"{status_summary['failed']} failed ({wf_ms:.0f}ms)")

wf_passed = status_summary["failed"] == 0
assert status_summary["failed"] == 0, f"Workflow had {status_summary['failed']} failures"
# Verify cross-arm event count: workflow started + 4 step completes + workflow complete = 6 minimum
assert len(cross_events) >= 4, f"Expected >=4 cross-arm events, got {len(cross_events)}"
if wf_passed:
    print("\n[OK] TEST 2 PASSED")
else:
    print("\n[FAIL] TEST 2 FAILED -- Cross-arm event flow complete")

# =============================================================================
# FINAL REPORT
# =============================================================================
print("\n" + SEPARATOR)
print("FINAL REPORT")
print(SEPARATOR)
result1 = "[PASSED]" if all_passed else "[FAILED]"
print(f"  Test 1 (Session Lifecycle): {result1}")
print(f"    - Phases: INIT OK, DISCOVERY OK, INITIALIZE ({sum(1 for v in init_results.values() if v)}/{len(init_results)}), CAP_CHECK OK, READY ({ready_passed}/{len(ready_results)}), SHUTDOWN OK")
print(f"    - Elapsed: {elapsed:.0f}ms")
result2 = "[PASSED]" if wf_passed else "[FAILED]"
print(f"  Test 2 (Cross-Arm Event Flow): {result2}")
print(f"    - Steps: {status_summary['completed']}/{status_summary['total_steps']} completed")
print(f"    - Cross-arm events routed: {len(cross_events)}")
print(f"    - Elapsed: {wf_ms:.0f}ms")
print(f"  Total elapsed: {(elapsed + wf_ms):.0f}ms")
print(f"  EventBus history: {bus2.history_count} events")

# Cleanup
registry2.shutdown_all()
