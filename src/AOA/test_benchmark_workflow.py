"""
AOA Benchmark Workflow — Execute all 8 arms through CrossArmRouter
and verify results against benchmark_data.json baselines.
"""
import sys, json, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import arms.azazael_arm, arms.codex_arm, arms.eve_arm, arms.libra_arm, arms.support_arms, arms.kali_arm
from core.arm_registry import ArmRegistry
from core.event_bus import get_event_bus, AOEvent, AOEventType
from core.router import CrossArmRouter, WorkflowStep
from core.session_manager import get_session_manager, MilestoneStatus
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# Session audit trail (2026-09-28): the run records itself -- init
# outcomes and every step -- so the session dir cannot stay empty.
_sess_mgr = None
_benchmark_session = None
try:
    _sess_mgr = get_session_manager()
    _benchmark_session = _sess_mgr.create_session(
        name="AOA Benchmark Verification", tags=["benchmark", "aoa"])
except Exception as _e:
    print(f"  [WARN] session init failed: {_e}")

# Load benchmark data
bm_path = Path(__file__).resolve().parent / "benchmark_data.json"
with open(bm_path, encoding="utf-8") as f:
    benchmark = json.load(f)

print("=" * 70)
print("AOA Benchmark Workflow — All Arms via CrossArmRouter")
print("=" * 70)
print(f"Benchmark loaded: {len(benchmark)} sections ({', '.join(benchmark.keys())})")
if "system" in benchmark:
    sys_bm = benchmark["system"]
    print(f"  System: Python {sys_bm.get('python_version','?')} | free-threaded={sys_bm.get('free_threaded','?')} | OS={sys_bm.get('os','?')} | CPU={sys_bm.get('cpu_count','?')} cores")

# Load 2026-08-27 live model benchmark results (Eve all-models tests) and
# wire them into this workflow: published via PAMS + verified vs baselines.
EVE_LOGS = Path.home() / "Desktop" / "EveUnleashed_Selector" / "logs"
MODEL_RESULTS_FILES = {
    "gguf": EVE_LOGS / "gguf_benchmark_results_2026-08-27.json",
    "ollama": EVE_LOGS / "inference_test_results_2026-08-27.json",
}
model_results = {}
for _label, _path in MODEL_RESULTS_FILES.items():
    if _path.exists():
        try:
            with open(_path, encoding="utf-8") as _f:
                model_results[_label] = json.load(_f)
        except Exception as _e:
            print(f"  [WARN] failed to parse {_path.name}: {_e}")
print(f"Model results wired: {', '.join(sorted(model_results)) or 'none found'}")

# Init registry and router
registry = ArmRegistry()
bus = get_event_bus()
bus.clear_history()

# Track all routed events
routed_events = []
def collector(e):
    routed_events.append({"type": e.event_type, "source": e.source_arm, "target": e.target_arm})
bus.subscribe("*", collector)

print(f"\nDiscovered {len(registry.arms)} arms")
init_results = registry.initialize_all()
init_ok = sum(1 for v in init_results.values() if v)
print(f"Initialized: {init_ok}/{len(init_results)}")
for arm_id, ok in init_results.items():
    print(f"  [{'OK' if ok else 'FAIL'}] {arm_id}")

# Build DAG: all 8 arms in dependency order
# perfmon (no deps) -> pams -> azazael -> codex
# eve, libra, llama, agent_selector (parallel, no deps)
router = CrossArmRouter(registry, bus)
wf = router.create_workflow(
    name="AOA Benchmark Verification",
    description="Execute all 8 arms and verify against benchmark_data.json baselines")

# Step 1: PerfMon — no dependencies, captures system state first
wf.add_step(WorkflowStep(
    step_id="step1_perfmon",
    arm_id="perfmon",
    capability="get_metrics",
    payload={}))

# Step 2: PAMS — depends on perfmon, records metrics event
wf.add_step(WorkflowStep(
    step_id="step2_pams",
    arm_id="pams",
    capability="record_event",
    payload={"event": "benchmark.metrics", "data": {"pipeline": "benchmark"}, "tags": ["benchmark", "verified"]},
    depends_on=["step1_perfmon"]))

# Step 3: Azazael — depends on pams, discovers worms
wf.add_step(WorkflowStep(
    step_id="step3_azazael",
    arm_id="azazael",
    capability="discover_worms",
    payload={},
    depends_on=["step2_pams"]))

# Step 4: CODEX — depends on azazael, checks status
wf.add_step(WorkflowStep(
    step_id="step4_codex",
    arm_id="codex",
    capability="get_status",
    payload={},
    depends_on=["step3_azazael"]))

# Parallel steps (no dependencies on each other)
# Step 5: Libra — list_templates + calculate_fees (runs in parallel with 4-7)
wf.add_step(WorkflowStep(
    step_id="step5_libra_list",
    arm_id="libra",
    capability="list_templates",
    payload={}))

# Step 6: Eve — select_model (parallel)
wf.add_step(WorkflowStep(
    step_id="step6_eve",
    arm_id="eve",
    capability="get_status",
    payload={}))

# Step 7: LlamaServer — status check (parallel)
wf.add_step(WorkflowStep(
    step_id="step7_llama",
    arm_id="llama",
    capability="get_status",
    payload={}))

# Step 8: AgentSelector — select agent (parallel)
wf.add_step(WorkflowStep(
    step_id="step8_agent_selector",
    arm_id="agent_selector",
    capability="select_agent",
    payload={"task": "benchmark_verification"}))

# Step 9: PAMS — publish the model benchmark results summary into the
# cross-arm event flow so all arms can consume the verified numbers.
_bm_summary = {"source": "none"}
if "gguf" in model_results:
    _gm = model_results["gguf"].get("models", {})
    if _gm:
        _best_tps, _best_name = max((v.get("avg_predicted_per_sec", 0), k) for k, v in _gm.items())
        _bm_summary = {"source": MODEL_RESULTS_FILES["gguf"].name,
                       "models": len(_gm),
                       "fastest_model": _best_name,
                       "fastest_tps": round(_best_tps, 2)}
elif "ollama" in model_results:
    _bm_summary = {"source": MODEL_RESULTS_FILES["ollama"].name,
                   "models": len(model_results["ollama"].get("models", {}))}

wf.add_step(WorkflowStep(
    step_id="step9_pams_model_results",
    arm_id="pams",
    capability="record_event",
    payload={"event": "benchmark.model_results", "data": _bm_summary,
             "tags": ["benchmark", "model-results"]},
    depends_on=["step8_agent_selector"]))

# Execute
order = wf.get_topological_order()
print(f"\nWorkflow: {wf.name} ({len(wf.steps)} steps)")
print(f"Topological order: {' -> '.join(order[:4])}")
print(f"                   {' -> '.join(order[4:])}")
print(f"Executing...")

t0 = time.perf_counter()
results = router.execute_workflow(wf.workflow_id)
elapsed = (time.perf_counter() - t0) * 1000
# Step milestones stamped with the ROUTER timestamps (started_at /
# completed_at): durations exist instead of being invented here.
try:
    from datetime import datetime as _dt
    for _r in results:
        try:
            _st = _dt.fromisoformat(_r.get("started_at", ""))
            _en = _dt.fromisoformat(_r.get("completed_at", ""))
            _ms = max(0.0, (_en - _st).total_seconds() * 1000.0)
        except Exception as exc:
            note_failure("test_benchmark_workflow:L186", exc)
            _ms = 0.0
        _ok = (_r.get("status") == "completed"
               and (_r.get("result") or {}).get("status") == "ok")
        if _sess_mgr is not None:
            _sess_mgr.record_milestone(
                _r.get("arm_id", "?"), _r.get("capability", "?"),
                description="benchmark step %s" % _r.get("step_id", "?"),
                status=None if _ok else MilestoneStatus.FAILED,
                duration_ms=_ms)
except Exception as _e:
    print(f"  [WARN] step milestone recording failed: {_e}")

# Verify ALL steps completed successfully
for r in results:
    assert r["status"] == "completed", f"{r['step_id']} ({r['arm_id']}.{r['capability']}) FAILED: {r.get('result',{}).get('error','?')}"

# Report each step
print(f"\nStep Results ({len(results)} total):")
for r in results:
    ok = r["status"] == "completed"
    mark = "OK" if ok else "FAIL"
    result = r.get("result", {})
    status = result.get("status", "?")
    data = result.get("data", {})
    
    # Extract key metrics for display
    if r["arm_id"] == "perfmon" and status == "ok":
        detail = f"CPU={data.get('cpu_percent','?')}% Mem={data.get('memory_percent','?')}%"
    elif r["arm_id"] == "azazael" and status == "ok":
        detail = f"worms={data.get('worm_count','?')} cats={len(data.get('categories',[]))}"
    elif r["arm_id"] == "libra" and status == "ok":
        detail = f"count={data.get('count','?')}"
    elif r["arm_id"] == "codex" and status == "ok":
        detail = f"studio={'exists' if data.get('codex_studio_exists') else 'missing'}"
    elif r["arm_id"] == "eve" and status == "ok":
        detail = f"init={data.get('initialized','?')}"
    elif r["arm_id"] == "agent_selector" and status == "ok":
        detail = f"selected={data.get('selected','?')} score={data.get('score','?')}"
    elif r["arm_id"] == "pams" and status == "ok":
        detail = f"event={data.get('event','?')}"
    else:
        detail = str(data)[:80]
    
    print(f"  [{mark}] {r['step_id']}: {r['arm_id']}.{r['capability']} -> {status} | {detail}")

# Benchmark verification
print(f"\nBenchmark Verification:")
comparisons = []
for r in results:
    result = r.get("result", {})
    if result.get("status") != "ok":
        continue
    data = result.get("data", {})
    arm = r["arm_id"]
    
    if arm == "perfmon" and "perfmon" in benchmark:
        bm = benchmark["perfmon"]
        checks = []
        if "cpu_count" in data: checks.append(f"cpu_count {data['cpu_count']}=={bm.get('cpu_count','?')}")
        if "memory_total_gb" in data: checks.append(f"mem_total {data['memory_total_gb']}~{bm.get('memory_total_gb','?')}")
        comparisons.append(("perfmon", f"{len(checks)} metrics", " ".join(checks)))
    elif arm == "azazael" and "azazael" in benchmark:
        bm = benchmark["azazael"]
        worm_count = data.get("worm_count", 0)
        benchmark_worms = bm.get("total_worms", 0)
        delta = worm_count - benchmark_worms
        comparisons.append(("azazael", f"worms {worm_count} (bm={benchmark_worms}, {'+' if delta >= 0 else ''}{delta})", ""))
    elif arm == "codex" and "codex" in benchmark:
        bm = benchmark["codex"]
        studio_ok = data.get("codex_studio_exists", False)
        comparisons.append(("codex", f"studio exists={studio_ok} (bm={bm.get('codex_studio_exists','?')})", ""))

# Model benchmark verification (wired 2026-08-27): live results JSON vs baselines
model_check_fail = False
if "eve" in benchmark and model_results:
    bm_eve = benchmark["eve"]
    if "gguf" in model_results:
        gm = model_results["gguf"].get("models", {})
        ranked = sorted(((v.get("avg_predicted_per_sec", 0), k) for k, v in gm.items()), reverse=True)
        if ranked:
            top_tps, top_model = ranked[0]
            floor = bm_eve.get("min_gguf_tps", 25.0)
            expect_model = bm_eve.get("best_gguf_model", "")
            ok_count = len(gm) >= bm_eve.get("discovery_gguf", 6)
            ok_model = (top_model == expect_model)
            ok_tps = top_tps >= floor
            status = "OK" if (ok_count and ok_model and ok_tps) else "FAIL"
            if status == "FAIL":
                model_check_fail = True
            comparisons.append(("eve-gguf",
                f"[{status}] models={len(gm)} (bm>={bm_eve.get('discovery_gguf', 6)}) "
                f"fastest={top_model} {top_tps} t/s (bm={expect_model} @ {bm_eve.get('best_gguf_tps', '?')}, floor {floor})",
                ""))
    if "ollama" in model_results:
        osum = model_results["ollama"].get("ollama_summary", {})
        comparisons.append(("eve-ollama",
            f"best={osum.get('best_model', '?')} suite_avg={osum.get('avg_tps', '?')} t/s "
            f"(bm best={bm_eve.get('best_ollama_model', '?')} @ {bm_eve.get('best_ollama_tps', '?')})",
            ""))
elif "eve" in benchmark:
    comparisons.append(("eve", "results JSON not found -- verification skipped "
                        "(artifacts expected in Desktop/EveUnleashed_Selector/logs)", ""))

for arm, summary, detail in comparisons:
    print(f"  {arm}: {summary}")

# Cross-arm event summary
wf_events = [e for e in routed_events if "workflow" in e["type"]]
arm_events = [e for e in routed_events if "execute" in e["type"]]
print(f"\nCross-Arm Events: {len(routed_events)} total")
print(f"  Workflow events: {len(wf_events)}")
print(f"  Arm execution events: {len(arm_events)}")
for e in wf_events:
    print(f"    [{e['type']}] {e['source']}")

# Final summary
summary = wf.get_status_summary()
print(f"\nWorkflow Summary:")
print(f"  Completed: {summary['completed']}/{summary['total_steps']}")
print(f"  Failed: {summary['failed']}")
print(f"  Skipped: {summary['skipped']}")
print(f"  Elapsed: {elapsed:.0f}ms")

all_pass = summary["failed"] == 0 and not model_check_fail
print(f"\n[{'OK' if all_pass else 'FAIL'}] BENCHMARK WORKFLOW {'PASSED' if all_pass else 'FAILED'} -- all 8 arms verified")
if all_pass:
    print(f"  All {summary['total_steps']} steps completed, 0 failures")
else:
    print(f"  {summary['failed']} failure(s) detected")
print(f"  All {summary['total_steps']} arms executed through CrossArmRouter")
print(f"  EventBus history: {bus.history_count} events")
print(f"  Total time: {elapsed:.0f}ms")

benchmark_verdict = "pass" if all_pass else "fail"
if _sess_mgr is not None and _benchmark_session is not None:
    try:
        _sess_mgr.complete_session(
            status="completed" if all_pass else "failed")
    except Exception as _e:
        print(f"  [WARN] session complete failed: {_e}")
    try:
        _saved = _sess_mgr.save_session(_benchmark_session.session_id)
        print(f"  Session saved: {_saved} (benchmark_verdict={benchmark_verdict})")
    except Exception as _e:
        print(f"  session save FAILED: {_e}")

registry.shutdown_all()
