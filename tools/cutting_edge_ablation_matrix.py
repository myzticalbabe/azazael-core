# coding: utf-8
"""Full-factorial ablation matrix over the CuttingEdgeModel switches.

engram #511 (DeepSeek Task #509), deliverable 2. #508 shipped the switches and
proved each one composes with pipeline config, one at a time.
``CuttingEdgeModel.ablation_matrix()`` is a one-at-a-time sweep by design (its
docstring says so): baseline plus four single removals, five rows. This asks the
other question -- all sixteen combinations, run through the real pipeline -- which
is the sweep a study needs to attribute an effect to an interaction rather than to
a component.

It reuses the module's own primitives rather than re-deriving them:
``CuttingEdgeConfig.variant(**overrides)`` (validated by construction),
``_USE_FLAG`` for the component->keyword mapping, and ``ablation_switches()`` /
``fingerprint()`` for identity. A second mechanism here would be a second place for
the switch semantics to be wrong.

What each row records, and why each is worth recording:

  * ``config_fingerprint`` -- distinct per row, which is what stops two ablations
    being filed under one version.
  * ``ops`` -- the linear/quadratic split, so "SSM is linear, attention is
    quadratic" is a measurement per configuration rather than a claim about the
    module.
  * ``routing`` -- entropy of the MoE routing distribution. An ablation that moves
    the loss without moving where tokens go is measuring something other than what
    it claims, so the routing is reported beside the loss.
  * ``trained`` / ``trained_reason`` -- a row with no experts must report that it
    did not train, not a loss delta it never computed.

Usage:
    python tools/cutting_edge_ablation_matrix.py [--out PATH] [--json-only]
"""

import argparse
import itertools
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent          # azazael/
sys.path.insert(0, str(ROOT / "src"))

#: The component order the model builds in. Imported rather than re-listed: a
#: local copy of this tuple is exactly how the two would silently drift apart.
try:
    from CORE.WORMS.expansion.cutting_edge_model import COMPONENTS
except ImportError:                                    # keep --help working
    COMPONENTS = ("moe", "ssm", "attention", "ffn")

RECORDS = [
    {"event_type": "cluster_execution_started", "severity": "info",
     "source": "CLUSTERWORMS",
     "data": {"cluster_type": "alpha", "worm_count": 3, "parallel": 1}},
    {"event_type": "cluster_execution_started", "severity": "error",
     "source": "CLUSTERWORMS",
     "data": {"cluster_type": "beta", "worm_count": 7, "parallel": 0}},
    {"event_type": "inference_failed", "severity": "warning", "source": "NETWORXZ",
     "data": {"provider": "groq", "latency_ms": 231}},
] * 4


class _FixedDataPipe:
    """Fixed records; the memory store's contents are not what this measures."""

    def __init__(self, records):
        self.records = list(records)

    def split_memories(self, **kwargs):
        return {"train": list(self.records), "test": list(self.records),
                "stats": {"total": len(self.records)}}

    def extract_from_memory(self, **kwargs):
        return list(self.records)


def combinations(names):
    """Every subset of ``names``, as (label, {component: enabled}) -- 2**n rows.

    Ordered by popcount then by the canonical order, so the empty baseline is
    first and the full model is last. That ordering is what makes the printed
    table readable: each row adds to the one above it.
    """
    rows = []
    for n in range(len(names) + 1):
        for subset in itertools.combinations(names, n):
            label = "full" if n == len(names) else (
                "none" if n == 0 else "+".join(subset))
            rows.append((label, {name: (name in subset) for name in names}))
    return rows


def _one_run(switches: Dict[str, bool], tmpdir: str) -> Dict[str, Any]:
    """Run one combination through the real pipeline and collect the row."""
    from CORE.NeuralPipeline import NeuralPipeline
    from CORE.WORMS.dead_letter_queue import DeadLetterQueue

    kwargs = {("use_" + name): bool(on) for name, on in switches.items()}
    pipeline = NeuralPipeline(max_workers=4, llm_augmentation=False,
                              use_cutting_edge=True,
                              cutting_edge_config=kwargs)
    pipeline.data_pipe = _FixedDataPipe(RECORDS)
    pipeline.dlq = DeadLetterQueue(storage_path=Path(tmpdir) / "dead_letters.json")
    # No durable write: the shared memory database is not this script's to write.
    # The versioner still records the entry in-process, so the version string is
    # observable, which is the part that matters here.
    pipeline.versioner._store = False
    pipeline._persist_run = lambda metrics: None

    started = time.perf_counter()
    run = pipeline.run_end_to_end(source="CLUSTERWORMS", register_model=True)
    elapsed = time.perf_counter() - started

    # The switches this ROW was built for, read back off the built model rather
    # than echoed from `switches`: echoing would make the row agree with the
    # request even if the request never reached the model.
    observed = pipeline.cutting_edge_model.config.ablation_switches()
    versions = pipeline.versioner.list_versions("cutting_edge_model")

    row: Dict[str, Any] = {
        "switches": observed,
        "active_components": list(run.get("active_components", [])),
        "config_fingerprint": run.get("config_fingerprint"),
        "param_count": run.get("param_count"),
        "ops": run.get("ops"),
        "routing": run.get("routing"),
        "aux_loss": run.get("aux_loss"),
        "trained": run.get("trained"),
        "trained_reason": run.get("trained_reason"),
        "experts_updated": run.get("experts_updated"),
        "loss_before": run.get("loss_before"),
        "loss_after": run.get("loss_after"),
        "loss_delta": run.get("loss_delta"),
        "version": versions[0]["version"] if versions else None,
        "dead_letters": len(pipeline.dlq.get_pending()),
        "status": run.get("status"),
        "error": run.get("error"),
        "seconds": round(elapsed, 3),
    }
    return row


def _fmt(value: Any, width: int) -> str:
    if value is None:
        text = "-"
    elif isinstance(value, float):
        text = "%.4f" % value
    elif isinstance(value, bool):
        text = "yes" if value else "no"
    elif isinstance(value, (dict, list)):
        text = json.dumps(value, separators=(",", ":"))
    else:
        text = str(value)
    return text[:width].ljust(width)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", default=str(
        ROOT / "state" / "cutting_edge_ablation_matrix.json"))
    parser.add_argument("--json-only", action="store_true",
                        help="write the artifact without the printed table")
    args = parser.parse_args(argv)

    rows = combinations(COMPONENTS)
    print("full-factorial sweep: %d combinations over %s"
          % (len(rows), ", ".join(COMPONENTS)))
    print()

    results: List[Dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="ce_matrix_") as td:
        for index, (label, switches) in enumerate(rows, 1):
            row = _one_run(switches, td)
            row["name"] = label
            row["index"] = index
            results.append(row)
            print("  [%2d/%d] %-24s %s" % (
                index, len(rows), label,
                "ok" if row["status"] == "success" else "FAILED: %s" % row["error"]))

    if not args.json_only:
        print()
        header = ("name", "moe", "ssm", "att", "ffn", "params", "lin_ops",
                  "quad_ops", "entropy", "used", "aux", "trained",
                  "loss_before", "loss_after", "delta")
        widths = (24, 3, 3, 3, 3, 8, 10, 10, 8, 5, 8, 7, 12, 11, 10)
        print("  ".join(h.ljust(w) for h, w in zip(header, widths)))
        print("  ".join("-" * w for w in widths))
        for row in results:
            ops = row["ops"] or {}
            routing = row["routing"] or {}
            cells = (
                row["name"],
                row["switches"].get("moe"), row["switches"].get("ssm"),
                row["switches"].get("attention"), row["switches"].get("ffn"),
                row["param_count"],
                ops.get("linear"), ops.get("quadratic"),
                routing.get("entropy_normalised"),
                "%s/%s" % (routing.get("used_experts", "-"), routing.get("n_experts", "-")),
                row["aux_loss"], row["trained"],
                row["loss_before"], row["loss_after"], row["loss_delta"],
            )
            print("  ".join(_fmt(c, w) for c, w in zip(cells, widths)))

    # The invariants this sweep is worth running for. Checked rather than
    # eyeballed, and a failure here is a failure of the run, not a footnote.
    problems = []
    if len(results) != 2 ** len(COMPONENTS):
        problems.append("expected %d rows, got %d"
                        % (2 ** len(COMPONENTS), len(results)))
    fingerprints = [r["config_fingerprint"] for r in results]
    if len(set(fingerprints)) != len(fingerprints):
        problems.append("config fingerprints are not distinct across combinations")
    for row in results:
        if row["status"] != "success":
            problems.append("%s: status=%s %s" % (row["name"], row["status"], row["error"]))
        if row["dead_letters"]:
            problems.append("%s: %d dead letters" % (row["name"], row["dead_letters"]))
        expected = tuple(n for n in COMPONENTS if row["switches"][n])
        if tuple(row["active_components"]) != expected:
            problems.append("%s: active_components %s != switches %s"
                            % (row["name"], row["active_components"], expected))
        if row["switches"]["moe"]:
            if row["trained"] is not True:
                problems.append("%s: moe on but trained=%s" % (row["name"], row["trained"]))
            if row["experts_updated"] != 8:
                problems.append("%s: experts_updated=%s" % (row["name"], row["experts_updated"]))
            if row["loss_delta"] is None or row["loss_delta"] >= 0:
                problems.append("%s: no loss improvement (%s)" % (row["name"], row["loss_delta"]))
            routing = row["routing"] or {}
            if not routing.get("counts"):
                problems.append("%s: moe on but no routing counts recorded" % row["name"])
        else:
            if row["trained"] is not False:
                problems.append("%s: moe off but trained=%s" % (row["name"], row["trained"]))
            if "moe" not in str(row["trained_reason"]):
                problems.append("%s: moe off without a reason naming moe (%s)"
                                % (row["name"], row["trained_reason"]))
        # Registration is tied to training, and the check runs in BOTH
        # directions. A trained model must be filed under its own fingerprint;
        # an UNTRAINED one must not be filed at all. With no MoE there is no
        # trainable component -- the experts' output bias is the model's only
        # learning surface -- so half of these configurations are inference-only,
        # and registering one would claim a model state that was never produced.
        if row["trained"]:
            if row["version"] is None:
                problems.append("%s: trained but no version recorded" % row["name"])
            elif row["config_fingerprint"] not in row["version"]:
                problems.append("%s: version %s lacks the fingerprint %s"
                                % (row["name"], row["version"], row["config_fingerprint"]))
        elif row["version"] is not None:
            problems.append("%s: did not train but was registered as %s"
                            % (row["name"], row["version"]))

    artifact = {
        "task": "engram #511 / DeepSeek #509 -- deliverable 2",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "components": list(COMPONENTS),
        "combinations": len(results),
        "records_per_run": len(RECORDS),
        # Measured, not assumed: the MoE is the only component with a learning
        # surface, so every combination without it is inference-only.
        "trainable_combinations": sum(1 for r in results if r["trained"]),
        "inference_only_combinations": sum(1 for r in results if not r["trained"]),
        "invariant_problems": problems,
        "rows": results,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact, indent=2, default=str), encoding="utf-8")
    print()
    print("wrote %s" % out)

    if problems:
        print()
        print("INVARIANT FAILURES (%d):" % len(problems))
        for problem in problems:
            print("  -", problem)
        return 1
    print("all %d rows satisfied every checked invariant" % len(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
