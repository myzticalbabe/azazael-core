# coding: utf-8
"""Round 5 live root-cause repro (Engram #527): does the gate-bias remediation
reach the FULL batch, or only the probe it was fitted to?

Round 4 (#538) measured the gap this driver exists to explain: `rebalance` moved
its 256-row probe from 2 of 8 experts to 8 of 8, while the run's reported
4235-row routing stayed at exactly [0, 4235, 0, 0, 0, 0, 4235, 0].

Two hypotheses, tested here rather than argued:
  H1 (measurement)  the reported routing vector is sampled from the forward that
                    runs BEFORE the loop nudges the gate, so it cannot move;
  H2 (probe size)   the bias is fitted to 256 of 4235 rows and does not
                    generalise to the rest;
  H3 (structural)   the batch is collinear, so top-k pins the used set and no
                    bias can move the full-batch load.

Read-only on source. The REAL DataPipelineWorm runs against the live store; the
only interception is a runtime wrapper around `CuttingEdgeModel.train_parallel`
that CAPTURES the model and the activation rows it balanced on, so the battery
can re-run the loop over the real production rows under a different probe bound.
Nothing is written outside C:/Temp and a tempdir DLQ, and no source is modified.
"""

import hashlib
import json
import math
import sys
import sysconfig
import tempfile
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SRC = REPO / "src"
sys.path.insert(0, str(SRC))

# Imported at module level on purpose. They are plain Python modules with no
# import-time side effect that matters here, and the GIL refusal in `main()` runs
# before any measurement -- so hoisting them costs the refusal nothing, while a
# function-level import would be flagged by the anti-pattern scanner (and a
# suppression on a brand-new file would hide a fixable finding).
import CORE.WORMS.expansion.cutting_edge_model as cem
from CORE.NeuralPipeline import NeuralPipeline
from CORE.WORMS.dead_letter_queue import DeadLetterQueue

WATCHED = {
    "cutting_edge_model.py": REPO / "src/CORE/WORMS/expansion/cutting_edge_model.py",
    "work_stealing.py": REPO / "src/CORE/parallel/work_stealing.py",
    "NeuralPipeline.py": REPO / "src/CORE/NeuralPipeline.py",
}
REPORT_PATH = Path("C:/Temp/round5_report.json")


def sha256_of(path):
    """Digest via read_bytes, so this tool needs no `open()` of its own."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _counts(moe, rows):
    """Per-expert top-k load, measured independently of the module's snapshots."""
    chosen, _weights, _probs = moe.route(rows)
    counts = [0] * moe.n_experts
    for top in chosen:
        for expert_id in top:
            counts[expert_id] += 1
    return counts


def _summary(counts, n_experts):
    """Entropy / skew / starvation from counts, recomputed here on purpose."""
    total = sum(counts)
    if total <= 0 or not counts:
        return {"counts": counts, "entropy": 0.0, "entropy_normalised": 0.0,
                "used_experts": 0, "target": 0.0, "max_skew": 0.0,
                "starved_experts": list(range(n_experts))}
    probs = [c / float(total) for c in counts if c > 0]
    entropy = -sum(p * math.log(p) for p in probs)
    target = total / float(n_experts)
    return {
        "counts": counts,
        "entropy": entropy,
        "entropy_normalised": entropy / math.log(n_experts),
        "used_experts": sum(1 for c in counts if c > 0),
        "target": target,
        "max_skew": (max(counts) / target) if target else 0.0,
        "starved_experts": [i for i, c in enumerate(counts) if c == 0],
    }


def _changed_rows(moe, rows, baseline_bias):
    """How many rows' top-k SET differs between two gate biases."""
    saved = list(moe.gate_bias)
    changed = [0]

    def _topk():
        chosen, _w, _p = moe.route(rows)
        return [frozenset(t) for t in chosen]

    try:
        moe.gate_bias = list(baseline_bias)
        base = _topk()
        moe.gate_bias = list(saved)
        now = _topk()
    finally:
        moe.gate_bias = saved
    for a, b in zip(base, now):
        if a != b:
            changed[0] += 1
    return changed[0]


def main():
    gil = bool(sysconfig.get_config_var("Py_GIL_DISABLED"))
    print("interpreter: %s gil_disabled=%s" % (sys.version.split()[0], gil),
          flush=True)
    if not gil:
        print("REFUSED: run with $env:GIL_DISABLED='1' on py315t", flush=True)
        return 2

    hashes_before = {name: sha256_of(p) for name, p in WATCHED.items()}
    print("hashes-before: %s" % json.dumps(hashes_before), flush=True)

    captured = {}
    original = cem.CuttingEdgeModel.train_parallel

    def _spy(self, *args, **kwargs):
        """Run the real step, then keep the model and the rows it balanced on."""
        out = original(self, *args, **kwargs)
        captured["model"] = self
        captured["rows"] = [list(r) for r in (self.moes[0].last_inputs or [])]
        captured["balancing"] = out.get("balancing")
        return out

    cem.CuttingEdgeModel.train_parallel = _spy

    tmpdir = tempfile.mkdtemp(prefix="round5-dlq-")
    pipeline = NeuralPipeline(max_workers=4, llm_augmentation=False,
                              use_cutting_edge=True)
    pipeline.versioner._store = False
    pipeline._persist_run = lambda metrics: None
    pipeline.dlq = DeadLetterQueue(storage_path=Path(tmpdir) / "dead_letters.json")

    started = time.perf_counter()
    try:
        run = pipeline.run_end_to_end(source="ClusterWORMZ", limit=5000,
                                      register_model=True)
    except Exception as exc:
        print("RUN-FAILED: %s: %s" % (type(exc).__name__, exc), flush=True)
        traceback.print_exc()
        return 3
    elapsed = time.perf_counter() - started
    cem.CuttingEdgeModel.train_parallel = original

    hashes_after = {name: sha256_of(p) for name, p in WATCHED.items()}
    void = hashes_before != hashes_after
    print("hashes-after: %s" % json.dumps(hashes_after), flush=True)
    print("void (source moved mid-run): %s" % void, flush=True)

    model = captured.get("model")
    rows = captured.get("rows") or []
    if model is None or not rows:
        print("CAPTURE-MISSING: no train_parallel call was observed", flush=True)
        return 4

    cfg = model.config
    moe = cem.CuttingEdgeModel(cfg).moes[0]          # same seed => same gate
    n_experts = moe.n_experts
    probe_bound = cem.DEFAULT_BALANCE_TOKENS

    # -- H1: provenance of the run's own routing verdict -------------------
    run_routing = run.get("routing") or {}
    run_after = run.get("routing_after_balance")
    balancing = captured.get("balancing") or {}
    h1 = {
        "run_routing_counts": run_routing.get("counts"),
        "loop_before_full_counts": (balancing.get("before_full") or {}).get("counts"),
        "loop_after_full_counts": (balancing.get("after_full") or {}).get("counts"),
        "run_routing_after_balance": (run_after or {}).get("counts"),
        "routing_equals_pre_loop_snapshot":
            run_routing.get("counts") == (balancing.get("before_full") or {}).get("counts"),
        "reported_after_equals_post_loop_snapshot":
            (run_after or {}).get("counts") == (balancing.get("after_full") or {}).get("counts"),
        "rows_used": balancing.get("rows_used"),
        "rows_available": balancing.get("rows_available"),
    }

    # -- the in-run loop's effect on the FULL batch, from its own snapshots --
    before_full = (balancing.get("before_full") or {})
    after_full = (balancing.get("after_full") or {})
    in_run = {
        "before_full": _summary(before_full.get("counts") or [], n_experts),
        "after_full": _summary(after_full.get("counts") or [], n_experts),
        "converged": balancing.get("converged"),
        "cap_hit": balancing.get("cap_hit"),
        "bound": balancing.get("bound"),
        "iterations": balancing.get("iterations"),
        "max_iters": balancing.get("max_iters"),
        "gate_bias": balancing.get("gate_bias"),
        "full_balanced": balancing.get("full_balanced"),
    }

    # -- H2: probe size. Same real rows, same loop, whole batch as the probe --
    zero_bias = [0.0] * n_experts
    pre = _summary(_counts(moe, rows), n_experts)
    pre_probe = _summary(_counts(moe, rows[:probe_bound]), n_experts)

    sampled = cem.CuttingEdgeModel(cfg).moes[0]
    sampled_report = sampled.rebalance(rows)
    sampled_counts = _counts(sampled, rows)

    full_probe = cem.CuttingEdgeModel(cfg).moes[0]
    full_report = full_probe.rebalance(rows, max_tokens=None)
    full_counts = _counts(full_probe, rows)

    h2 = {
        "rows_available": len(rows),
        "probe_bound": probe_bound,
        "pre_full": pre,
        "pre_probe_first_n": pre_probe,
        "sampled_probe_report": {
            "rows_used": sampled_report["rows_used"], "converged": sampled_report["converged"],
            "cap_hit": sampled_report["cap_hit"], "bound": sampled_report["bound"],
            "iterations": sampled_report["iterations"],
            "probe_before": sampled_report["before"]["counts"],
            "probe_after": sampled_report["after"]["counts"],
        },
        "sampled_post_full": _summary(sampled_counts, n_experts),
        "full_probe_report": {
            "rows_used": full_report["rows_used"], "converged": full_report["converged"],
            "cap_hit": full_report["cap_hit"], "bound": full_report["bound"],
            "iterations": full_report["iterations"],
        },
        "full_probe_post_full": _summary(full_counts, n_experts),
    }

    # -- H3: structural. Can the accumulated bias move the full-batch routing? --
    # `sampled` is the model that ACTUALLY ran the loop, so its gate_bias is the
    # post-remediation state. Measuring against a fresh (zero-bias) model here
    # instead would compare zeros with zeros and report "no rows changed" no
    # matter what happened -- an instrument that cannot fail is not evidence.
    # Positive control for that instrument: an injected MAX_GATE_BIAS skew MUST
    # register as changed rows. If the control reads 0 the instrument is blind and
    # the measurement above is silence, not evidence.
    control_model = cem.CuttingEdgeModel(cfg).moes[0]
    control_model.gate_bias[0] = cem.MAX_GATE_BIAS
    instrument_control = _changed_rows(control_model, rows, zero_bias)

    h3 = {
        "rows": len(rows),
        "instrument_positive_control_rows_changed": instrument_control,
        "rows_whose_topk_set_changed": _changed_rows(sampled, rows, zero_bias),
        "used_experts_pre": pre["used_experts"],
        "used_experts_post_sampled": in_run["after_full"]["used_experts"],
        "top_k": moe.top_k,
        "entropy_floor_log_top_k_over_n": math.log(moe.top_k) / math.log(n_experts),
        "pre_entropy_normalised": pre["entropy_normalised"],
        "post_entropy_normalised": in_run["after_full"]["entropy_normalised"],
        "note": "zero-bias model vs the model that ran the loop, same rows",
    }

    # -- H4: cap adequacy. Is 256 iterations the limit, or the balance itself? --
    # The loop's own stopping rule is evaluated on its probe, so the probe is what
    # the cap has to satisfy; the full-batch load is then measured as a consequence.
    def _cap_entry(cap, max_tokens):
        trial = cem.CuttingEdgeModel(cfg).moes[0]
        rep = trial.rebalance(rows, max_iters=cap, max_tokens=max_tokens)
        bias = list(trial.gate_bias)
        return {
            "cap": cap,
            "max_tokens": max_tokens,
            "converged": rep["converged"],
            "cap_hit": rep["cap_hit"],
            "bound": rep["bound"],
            "iterations": rep["iterations"],
            "probe_after": rep["after"],
            "post_full": _summary(_counts(trial, rows), n_experts),
            "gate_bias": bias,
            "biases_at_clamp": sum(1 for b in bias
                                   if abs(abs(b) - cem.MAX_GATE_BIAS) < 1e-9),
            "max_abs_bias": max(abs(b) for b in bias),
        }

    cap_sweep_probe = [_cap_entry(c, probe_bound) for c in (256, 512, 1024, 2048)]

    # -- H5: is the bias CLAMP the thing that cannot move? --
    # If the experts that are still over/under target sit AT the +/-MAX_GATE_BIAS
    # clamp, the bias has no authority left and the bound is structural rather
    # than a shortage of iterations.
    final = cap_sweep_probe[-1]
    worst = max(range(n_experts),
                key=lambda i: final["post_full"]["counts"][i])
    starved_after = final["post_full"]["starved_experts"]
    h5 = {
        "cap": final["cap"],
        "probe_max_skew_at_cap": final["probe_after"]["max_skew"],
        "full_max_skew_at_cap": final["post_full"]["max_skew"],
        "worst_expert": worst,
        "worst_expert_bias": final["gate_bias"][worst],
        "clamped_experts": final["biases_at_clamp"],
        "max_abs_bias": final["max_abs_bias"],
        "max_gate_bias": cem.MAX_GATE_BIAS,
        "starved_after_sweep": starved_after,
        "iterations_exhausted": not final["converged"],
    }

    serializable = json.loads(json.dumps(run, default=str))
    report = {
        "task": "engram #527",
        "interpreter": sys.version.split()[0],
        "gil_disabled": gil,
        "hashes_before": hashes_before,
        "hashes_after": hashes_after,
        "void": void,
        "wall_seconds": round(elapsed, 2),
        "h1_measurement_provenance": h1,
        "in_run_loop_effect_on_full_batch": in_run,
        "h2_probe_size": h2,
        "h3_structural": h3,
        "h4_cap_sweep_probe": cap_sweep_probe,
        "h5_clamp": h5,
        "run": serializable,
    }
    REPORT_PATH.write_text(json.dumps(report, indent=1), encoding="utf-8")

    print("\n--- H1 measurement provenance ---", flush=True)
    for k, v in h1.items():
        print("  %s: %s" % (k, v), flush=True)
    print("--- in-run loop vs the FULL batch ---", flush=True)
    print("  before_full: %s" % json.dumps(in_run["before_full"]), flush=True)
    print("  after_full : %s" % json.dumps(in_run["after_full"]), flush=True)
    print("  converged=%s cap_hit=%s bound=%s iterations=%s full_balanced=%s"
          % (in_run["converged"], in_run["cap_hit"], in_run["bound"],
             in_run["iterations"], in_run["full_balanced"]), flush=True)
    print("--- H2 probe size ---", flush=True)
    print("  pre_full: %s" % json.dumps(pre), flush=True)
    print("  sampled-post-full: %s" % json.dumps(h2["sampled_post_full"]), flush=True)
    print("  full-probe-post-full: %s" % json.dumps(h2["full_probe_post_full"]), flush=True)
    print("  full_probe converged=%s cap_hit=%s bound=%s iterations=%s"
          % (full_report["converged"], full_report["cap_hit"], full_report["bound"],
             full_report["iterations"]), flush=True)
    print("--- H3 structural ---", flush=True)
    print("  rows whose top-k set changed: %s / %s (instrument control: %s)"
          % (h3["rows_whose_topk_set_changed"], h3["rows"],
             h3["instrument_positive_control_rows_changed"]), flush=True)
    print("  entropy floor log(top_k)/log(n): %.9f" % h3["entropy_floor_log_top_k_over_n"],
          flush=True)
    print("--- H4 cap sweep (probe = %d rows) ---" % probe_bound, flush=True)
    for entry in cap_sweep_probe:
        print("  cap=%-5s converged=%-5s iters=%-5s probe_skew=%.3f "
              "full_used=%s full_skew=%.3f clamped=%s"
              % (entry["cap"], entry["converged"], entry["iterations"],
                 entry["probe_after"]["max_skew"],
                 entry["post_full"]["used_experts"],
                 entry["post_full"]["max_skew"], entry["biases_at_clamp"]),
              flush=True)
    print("--- H5 clamp ---", flush=True)
    print("  worst expert %s, clamped experts %s, max|bias| %.6f of %.4f, "
          "full skew at cap %.3f, starved %s"
          % (h5["worst_expert"], h5["clamped_experts"], h5["max_abs_bias"],
             h5["max_gate_bias"], h5["full_max_skew_at_cap"],
             h5["starved_after_sweep"]), flush=True)
    print("report_written: %s" % REPORT_PATH, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
