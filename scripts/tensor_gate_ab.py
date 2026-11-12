#!/usr/bin/env python3
# coding: utf-8
"""Phase-C pre-gate A/B: does the tensor surrogate out-decide the live gate?
(ROADMAP 1.3)

THE QUESTION
------------
`state/tensor_model.json` predicts the margin a genome will earn. The live gate
promotes on a point-estimate margin, then a holdout adjudicates. The roadmap says
the surrogate must run BESIDE that gate, ADVISORY ONLY, and only become the
decider if it measurably improves shipment quality — fewer holdout failures and
no extra false promotions.

This script answers it from history, so the answer does not need N more cycles
before it can be read: every recorded `evolution_result` row carries the genes,
the realized margin AND the holdout verdict, so both deciders can be scored on
the SAME rows.

WHAT IT DOES NOT DO
-------------------
It never writes the live model, never edits a threshold and never promotes the
surrogate to deciding. The verdict is a RECOMMENDATION in an artifact; switching
the gate remains a deliberate, separate act.

ARTIFACT
--------
  state/tensor_gate_ab.json

Usage:
    python scripts/tensor_gate_ab.py
    python scripts/tensor_gate_ab.py --rows 400 --margin 0.75
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, os.path.join(ROOT, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

MODEL_JSON = os.path.join(ROOT, "state", "tensor_model.json")
OUT = os.path.join(ROOT, "state", "tensor_gate_ab.json")
PROMOTE_MARGIN = 0.75


def log(msg):
    print("%s %s" % (time.strftime("%Y-%m-%dT%H:%M:%S"), msg), flush=True)


# --------------------------------------------------------------------------
# pure-python inference (no torch; identical under 3.14t)
# --------------------------------------------------------------------------

def _layers(weights):
    """torch state_dict -> ordered [{weight, bias}], keyed by the numeric prefix."""
    buckets = {}
    for k, v in (weights or {}).items():
        name, _, kind = str(k).partition(".")
        if not name.isdigit():
            continue
        buckets.setdefault(int(name), {})[kind] = v
    return [buckets[n] for n in sorted(buckets)]


def _matvec(mat, vec):
    return [sum(float(m) * float(v) for m, v in zip(row, vec)) for row in mat]


def forward(model, genes):
    """Predict margin for a gene dict. ReLU between layers, linear output."""
    x = [float(genes.get(g, 0.0)) for g in model["genes"]]
    layers = _layers(model.get("weights"))
    if not layers:
        raise ValueError("model carries no layers")
    for i, L in enumerate(layers):
        x = _matvec(L["weight"], x)
        if "bias" in L:
            x = [a + b for a, b in zip(x, L["bias"])]
        if i < len(layers) - 1:
            x = [v if v > 0.0 else 0.0 for v in x]
    return float(x[0])


def _rows(limit):
    try:
        from CORE.local_memory import get_memory_store
        raw = get_memory_store().query_memories(
            event_type="evolution_result", limit=int(limit)) or []
    except Exception as exc:
        log("history read failed: %s" % str(exc)[:140])
        return []
    out = []
    for r in raw:
        d = r.get("data", r) if isinstance(r, dict) else None
        if isinstance(d, str):
            try:
                d = json.loads(d)
            except Exception:
                continue
        if not isinstance(d, dict):
            continue
        g = d.get("best_genes")
        b, t, h = d.get("baseline"), d.get("best_total"), d.get("holdout")
        if not isinstance(g, dict) or not isinstance(h, dict):
            continue
        if not isinstance(b, (int, float)) or not isinstance(t, (int, float)):
            continue
        out.append({"genes": g, "margin": float(t) - float(b),
                    "holdout_passed": bool(h.get("passed")),
                    "promoted": bool(d.get("promoted"))})
    return list(reversed(out))


def _score(rows, decide):
    """Shipment quality for one decider: how often does a promote survive?"""
    promotes = [r for r in rows if decide(r)]
    passed = sum(1 for r in promotes if r["holdout_passed"])
    return {"n_promotions": len(promotes),
            "n_holdout_pass": passed,
            "shipment_quality": (round(passed / float(len(promotes)), 4)
                                 if promotes else None),
            "false_promotions": len(promotes) - passed}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=400)
    ap.add_argument("--margin", type=float, default=PROMOTE_MARGIN)
    ap.add_argument("--min-rows", type=int, default=20)
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    try:
        with open(MODEL_JSON, encoding="utf-8") as fh:
            model = json.load(fh)
    except Exception as exc:
        log("FAILED to load %s: %s" % (MODEL_JSON, str(exc)[:140]))
        return 2

    rows = _rows(args.rows)
    if len(rows) < int(args.min_rows):
        log("SKIP: %d usable rows (need %d)" % (len(rows), args.min_rows))
        return 3

    # Attach the surrogate's prediction once, so both deciders read the same rows.
    pred_err, n_pred = 0.0, 0
    for r in rows:
        try:
            r["pred"] = forward(model, r["genes"])
            pred_err += abs(r["pred"] - r["margin"])
            n_pred += 1
        except Exception:
            r["pred"] = None

    scored = [r for r in rows if isinstance(r.get("pred"), (int, float))]
    gate = _score(scored, lambda r: r["margin"] >= float(args.margin))
    surrogate = _score(scored, lambda r: r["pred"] >= float(args.margin))

    better_quality = (surrogate["shipment_quality"] is not None
                      and gate["shipment_quality"] is not None
                      and surrogate["shipment_quality"] > gate["shipment_quality"])
    no_extra_false = surrogate["false_promotions"] <= gate["false_promotions"]
    decides = bool(better_quality and no_extra_false)

    report = {
        "kind": "tensor_gate_ab",
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "rows": len(scored),
        "margin_threshold": float(args.margin),
        "gate": gate,
        "surrogate": surrogate,
        "surrogate_mae": (round(pred_err / n_pred, 4) if n_pred else None),
        "criterion": {
            "beats_shipment_quality": better_quality,
            "no_extra_false_promotions": no_extra_false,
        },
        # The decision record. `advisory` is the safe default and the ONLY
        # outcome this script can produce about the live gate: it recommends,
        # it does not switch. Promotion to deciding stays a deliberate act.
        "decision": "candidate-to-decide" if decides else "advisory",
        "approved": False,
        "surrogate_is_advisory": not decides,
    }
    try:
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, default=str)
    except Exception as exc:
        log("FAILED to write %s: %s" % (args.out, str(exc)[:140]))
        return 2

    log("gate      : %s" % gate)
    log("surrogate : %s (mae=%s)" % (surrogate, report["surrogate_mae"]))
    log("decision  : %s -> %s" % (report["decision"], args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
