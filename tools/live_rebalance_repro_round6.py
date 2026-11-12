# coding: utf-8
"""Round 6 live repro (Engram #528): name the clamp-exhausted bound and let the
reward see the remediation.

Two changes are under test, and both need production-scale evidence rather than a
small fixture:

  CHANGE 1  `SparseMoE.rebalance` now names a third bound, ``"bias_clamp_exhausted"``
            (cap hit AND a bias sitting ON ``MAX_GATE_BIAS``). The shipped-config
            guarantee is that it changes NOTHING there -- the live batch has no
            clamped bias at cap 256, so `bound` stays None.
  CHANGE 2  `NeuralPipeline` feeds the reward the POST-remediation
            `used_fraction` when it is available. Loss cannot move (the reward is
            computed after `train_parallel` within the same run), so the whole
            live delta must be the reward's load term: 0.25 -> 1.0.

Run it twice: once with default `--cap 256` (byte-identical reproduction) and once
with `--cap 512` (the regime where the new bound fires).

Read-only on source. The REAL DataPipelineWorm runs against the live store; the
interceptions are runtime wrappers that CAPTURE what the run did (the model, the
rows it balanced on, the signals the reward was handed) and, for `--cap` other
than the shipped default, force the loop's iteration cap. Nothing is written
outside C:/Temp and a tempdir DLQ, and no source file is modified.
"""

import argparse
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

# Imported at module level on purpose: plain modules, no import-time side effect
# that matters here, and a function-level import of a BRAND-NEW file would be a
# scanner finding that a suppression would only hide.
import CORE.WORMS.expansion.cutting_edge_model as cem
from CORE.NeuralPipeline import NeuralPipeline
from CORE.WORMS.dead_letter_queue import DeadLetterQueue

WATCHED = {
    "cutting_edge_model.py": REPO / "src/CORE/WORMS/expansion/cutting_edge_model.py",
    "work_stealing.py": REPO / "src/CORE/parallel/work_stealing.py",
    "NeuralPipeline.py": REPO / "src/CORE/NeuralPipeline.py",
}


def sha256_of(path):
    """Digest via read_bytes, so this tool needs no `open()` of its own."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cap", type=int, default=cem.DEFAULT_BALANCE_MAX_ITERS,
                        help="iteration cap forced onto the loop (default: shipped)")
    parser.add_argument("--report", default=None, help="report path override")
    args = parser.parse_args()
    cap = int(args.cap)
    shipped_cap = cap == cem.DEFAULT_BALANCE_MAX_ITERS

    gil = bool(sysconfig.get_config_var("Py_GIL_DISABLED"))
    print("interpreter: %s gil_disabled=%s cap=%s shipped=%s"
          % (sys.version.split()[0], gil, cap, shipped_cap), flush=True)
    if not gil:
        print("REFUSED: run with $env:GIL_DISABLED='1' on py315t", flush=True)
        return 2

    hashes_before = {name: sha256_of(p) for name, p in WATCHED.items()}
    print("hashes-before: %s" % json.dumps(hashes_before), flush=True)

    captured = {}

    # -- Change 1 under test at a raised cap: the in-run loop uses the module
    # default (`rebalance(balance_rows)` with no max_iters), and a Python default
    # is bound at def time, so the cap is forced by WRAPPING the method rather
    # than by rebinding the constant (which would silently do nothing).
    if not shipped_cap:
        original_rebalance = cem.SparseMoE.rebalance

        def _capped(self, tokens, gamma=cem.DEFAULT_BALANCE_GAMMA,
                    max_iters=None, max_tokens=cem.DEFAULT_BALANCE_TOKENS):
            return original_rebalance(self, tokens, gamma=gamma,
                                      max_iters=cap, max_tokens=max_tokens)

        cem.SparseMoE.rebalance = _capped

    # -- Change 2 under test: the load term the reward actually receives.
    original_reward = cem.CuttingEdgeModel.reward

    def _reward_spy(self, signals, *a, **kw):
        captured["reward_signals"] = [float(s) for s in signals]
        return original_reward(self, signals, *a, **kw)

    cem.CuttingEdgeModel.reward = _reward_spy

    original_step = cem.CuttingEdgeModel.train_parallel

    def _spy(self, *a, **kw):
        """Run the real step, then keep the model and the rows it balanced on."""
        out = original_step(self, *a, **kw)
        captured["model"] = self
        captured["rows"] = [list(r) for r in (self.moes[0].last_inputs or [])]
        captured["balancing"] = out.get("balancing")
        return out

    cem.CuttingEdgeModel.train_parallel = _spy

    tmpdir = tempfile.mkdtemp(prefix="round6-dlq-")
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
    cem.CuttingEdgeModel.train_parallel = original_step
    cem.CuttingEdgeModel.reward = original_reward
    if not shipped_cap:
        cem.SparseMoE.rebalance = original_rebalance

    hashes_after = {name: sha256_of(p) for name, p in WATCHED.items()}
    void = hashes_before != hashes_after
    print("hashes-after: %s" % json.dumps(hashes_after), flush=True)
    print("void (source moved mid-run): %s" % void, flush=True)

    balancing = captured.get("balancing") or {}
    before_full = balancing.get("before_full") or {}
    after_full = balancing.get("after_full") or {}
    model = captured.get("model")
    n_experts = int(model.config.n_experts) if model is not None else 8
    bias = list(balancing.get("gate_bias") or (model.moes[0].gate_bias
                                               if model is not None else []))

    routing = run.get("routing") or {}
    routing_after = run.get("routing_after_balance") or {}
    signals = captured.get("reward_signals")

    print("\n--- Change 1: the bound ---", flush=True)
    print("  cap=%s bound=%r cap_hit=%s converged=%s iterations=%s full_balanced=%s"
          % (cap, balancing.get("bound"), balancing.get("cap_hit"),
             balancing.get("converged"), balancing.get("iterations"),
             balancing.get("full_balanced")), flush=True)
    print("  biases at clamp: %s  max|bias|=%.6f of %.4f"
          % (sum(1 for b in bias if abs(b) >= cem.MAX_GATE_BIAS - 1e-12),
             max(abs(b) for b in bias) if bias else 0.0, cem.MAX_GATE_BIAS),
          flush=True)
    print("  probe  before -> after: %s -> %s"
          % ((balancing.get("before") or {}).get("counts"),
             (balancing.get("after") or {}).get("counts")), flush=True)
    print("  full   before -> after: %s -> %s"
          % (before_full.get("counts"), after_full.get("counts")), flush=True)
    print("--- Change 2: the reward input ---", flush=True)
    print("  routing used_fraction (pre-loop) : %s" % routing.get("used_fraction"),
          flush=True)
    print("  routing_after_balance (post-loop): %s"
          % routing_after.get("used_fraction"), flush=True)
    print("  reward signals [progress, coverage, load]: %s" % signals, flush=True)
    print("--- run ---", flush=True)
    print("  loss_before=%r loss_after=%r delta=%r trained=%s experts_updated=%s"
          % (run.get("loss_before"), run.get("loss_after"), run.get("loss_delta"),
             run.get("trained"), run.get("experts_updated")), flush=True)
    print("  routing counts: %s" % routing.get("counts"), flush=True)
    print("  wall_seconds=%.2f  status=%s" % (elapsed, run.get("status")), flush=True)

    report = {
        "task": "engram #528",
        "cap": cap,
        "shipped_cap": shipped_cap,
        "interpreter": sys.version.split()[0],
        "gil_disabled": gil,
        "hashes_before": hashes_before,
        "hashes_after": hashes_after,
        "void": void,
        "wall_seconds": round(elapsed, 2),
        "bound": balancing.get("bound"),
        "cap_hit": balancing.get("cap_hit"),
        "converged": balancing.get("converged"),
        "iterations": balancing.get("iterations"),
        "max_iters": balancing.get("max_iters"),
        "full_balanced": balancing.get("full_balanced"),
        "probe_before_counts": (balancing.get("before") or {}).get("counts"),
        "probe_after_counts": (balancing.get("after") or {}).get("counts"),
        "before_full": _summary(before_full.get("counts") or [], n_experts),
        "after_full": _summary(after_full.get("counts") or [], n_experts),
        "gate_bias": bias,
        "biases_at_clamp": sum(1 for b in bias
                               if abs(b) >= cem.MAX_GATE_BIAS - 1e-12),
        "max_abs_bias": max(abs(b) for b in bias) if bias else 0.0,
        "max_gate_bias": cem.MAX_GATE_BIAS,
        "routing_used_fraction": routing.get("used_fraction"),
        "routing_after_balance_used_fraction": routing_after.get("used_fraction"),
        "reward_signals": signals,
        "loss_before": run.get("loss_before"),
        "loss_after": run.get("loss_after"),
        "loss_delta": run.get("loss_delta"),
        "trained": run.get("trained"),
        "experts_updated": run.get("experts_updated"),
        "routing_counts": routing.get("counts"),
        "routing_after_balance_counts": routing_after.get("counts"),
        "status": run.get("status"),
        "run": json.loads(json.dumps(run, default=str)),
    }
    out = Path(args.report) if args.report else Path(
        "C:/Temp/round6_report_cap%d.json" % cap)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print("report_written: %s" % out, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
