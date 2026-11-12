#!/usr/bin/env python3
# coding: utf-8
"""Train the tensor-genome surrogate MLP and export JSON weights.

WHY THIS EXISTS / WHY A SEPARATE SCRIPT
----------------------------------------
state/tensor_genome.spec.json has declared `trained:false` since the genome
landed: shape without weights. This script is TENSOR_GENOME_PLAN.md step 1 —
the GIL-side trainer (torch 2.14+XPU on the 3.15 GIL sidecar; torch does not
load under the free-threaded 3.15t build, same split as train_fitness_oracle.py).

WHAT IT TRAINS
--------------
Surrogate fitness model: X = champion gene values (PROMOTE_KEYS order),
y = MARGIN over baseline (best_total - baseline), one sample per
evolution_result row with a usable payload. Margin is the pool-invariant
currency: raw totals swing 19-28 across eras and cannot transport
(first training on raw totals scored transport 0.0).
MLP [in, max(8,2w), max(4,w), 1], Huber loss, Adam. Holdout is by ERA
(pool_id groups, last era held out) — never by row, so the reported
metrics measure cross-era transport, not in-era memorization.

ARTIFACTS (state/, alongside the spec — never overwriting it)
--------------------------------------------------------------
  tensor_model.json   {weights, genes, eras, holdout metrics, trained_at}
  tensor_model.marker.json  row counts for freshness checks
  tensor_model.pt     best-effort torch checkpoint (optional)

Exit codes: 0 trained | 3 not enough history (not an error) | 2 failure.

Usage:
    python scripts/train_tensor_model.py
    python scripts/train_tensor_model.py --force --epochs 300
    (Run with the GIL sidecar: torch is unavailable under the free-threaded 3.15t build.)
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

GENE_KEYS = ("learning_rate", "epsilon", "discount", "epsilon_decay")
STATE_DIR = os.path.join(ROOT, "state")
OUT_JSON = os.path.join(STATE_DIR, "tensor_model.json")
OUT_PT = os.path.join(STATE_DIR, "tensor_model.pt")
MARKER = os.path.join(STATE_DIR, "tensor_model.marker.json")


def log(msg):
    print("%s %s" % (time.strftime("%Y-%m-%dT%H:%M:%S"), msg), flush=True)


def collect_rows(limit):
    from CORE.local_memory import get_memory_store
    return get_memory_store().query_memories(
        event_type="evolution_result", limit=int(limit)) or []


def build_dataset(rows):
    """(samples, eras): samples = (genes vec, best_total, pool_id)."""
    samples = []
    for r in rows:
        d = (r.get("data", {}) or {})
        if isinstance(d, str):
            try:
                d = json.loads(d)
            except Exception:
                continue
        g = d.get("best_genes")
        t = d.get("best_total")
        b = d.get("baseline")
        if not isinstance(g, dict) or not isinstance(t, (int, float)):
            continue
        if not isinstance(b, (int, float)):
            continue
        t = float(t) - float(b)
        try:
            vec = [float(g[k]) for k in GENE_KEYS]
        except (KeyError, TypeError, ValueError):
            continue
        pid = d.get("pool_id", "none")
        samples.append((vec, float(t), str(pid)))
    return samples


def split_eras(samples, min_test=10, max_eras=3):
    """Split samples into (test_era_label, train, test).

    Test = most-recent eras walked back until >= min_test rows (a 2-row
    holdout cannot measure transport). Train = older eras.
    """
    eras = []
    for _, _, pid in samples:
        if pid not in eras:
            eras.append(pid)
    test_eras: list = []
    test: list = []
    for pid in reversed(eras):
        if len(test) >= min_test or len(test_eras) >= max_eras:
            break
        test_eras.append(pid)
        test.extend([(v, t) for v, t, p in samples if p == pid])
    train = [(v, t) for v, t, p in samples if p not in test_eras]
    return "+".join(test_eras), train, test


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=600)
    ap.add_argument("--epochs", type=int, default=300)
    ap.add_argument("--min-rows", type=int, default=60)
    ap.add_argument("--out", default=OUT_JSON)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    try:
        import torch
        import torch.nn as nn
    except Exception as exc:
        log("FAILED: torch unavailable in this interpreter (%s)" % str(exc)[:120])
        log("Run with the 3.15 GIL sidecar (torch does not load under 3.15t).")
        return 2

    try:
        rows = collect_rows(args.rows)
    except Exception as exc:
        log("FAILED to read history: %s" % str(exc)[:160])
        return 2

    samples = build_dataset(rows)
    if len(samples) < args.min_rows:
        log("SKIP: %d usable rows (need %d)" % (len(samples), args.min_rows))
        return 3

    test_era, train, test = split_eras(samples)
    if len(train) < args.min_rows or not test:
        log("SKIP: thin split (train=%d test=%d)" % (len(train), len(test)))
        return 3

    if not args.force and os.path.exists(args.out) and os.path.exists(MARKER):
        try:
            with open(MARKER, encoding="utf-8") as fh:
                prev = json.load(fh)
            if int(prev.get("n_rows", 0)) >= len(samples) * 0.80:
                # Fresh-model skip is an honest skip (rc=3, the script's
                # not-an-error class), never a success: the chain carves
                # rc=3 for this job, and a 0 would record training that
                # never ran (2026-09-28 audit).
                log("SKIP: model is fresh (%d rows vs %d now)"
                    % (prev.get("n_rows", 0), len(samples)))
                print(json.dumps({"skipped": "fresh", "n_rows": len(samples)}))
                return 3
        except Exception:
            pass

    dev = "xpu" if hasattr(torch, "xpu") and torch.xpu.is_available() else "cpu"
    log("device=%s train=%d test=%d (held-out era %s)" % (dev, len(train), len(test), test_era))
    w = len(GENE_KEYS)
    net = nn.Sequential(
        nn.Linear(w, max(8, w * 2)), nn.ReLU(),
        nn.Linear(max(8, w * 2), max(4, w)), nn.ReLU(),
        nn.Linear(max(4, w), 1)).to(dev)
    opt = torch.optim.Adam(net.parameters(), lr=0.01)
    loss_fn = nn.HuberLoss()
    Xtr = torch.tensor([v for v, _ in train], dtype=torch.float32).to(dev)
    ytr = torch.tensor([[t] for _, t in train], dtype=torch.float32).to(dev)
    Xte = torch.tensor([v for v, _ in test], dtype=torch.float32).to(dev)
    yte = torch.tensor([[t] for _, t in test], dtype=torch.float32).to(dev)

    net.train()
    for _ in range(max(1, int(args.epochs))):
        opt.zero_grad()
        loss = loss_fn(net(Xtr), ytr)
        loss.backward()
        opt.step()

    net.eval()
    with torch.no_grad():
        train_mae = float(torch.abs(net(Xtr) - ytr).mean())
        test_mae = float(torch.abs(net(Xte) - yte).mean())
        ymean = float(yte.mean()) if len(yte) else 0.0
        # era transport score: 1 - (test_mae / target spread), floored at 0
        spread = float((yte.max() - yte.min())) if len(yte) > 1 else 1.0
        transport = max(0.0, 1.0 - test_mae / max(spread, 1e-9))
        pred_te = net(Xte).flatten().tolist()
        actual_te = yte.flatten().tolist()
    log("train_mae=%.4f test_mae=%.4f transport=%.3f" % (train_mae, test_mae, transport))

    # 1.2 TESTUDO calibration: conformal interval + ECE analogue on the
    # held-out eras, using the epistemic tier's own procedure (imported,
    # not reimplemented — one definition of the math). A surrogate whose
    # interval does not cover is not trustworthy no matter its MAE.
    calibration: dict = {"alpha": 0.1, "n": len(actual_te),
                         "achieved": None, "calibrated": None}
    try:
        sys.path.insert(0, os.path.join(ROOT, "src"))
        from CORE.WORMS.expansion.exmachina import (
            conformal_quantile, coverage_report)
        residuals = [abs(p - a) for p, a in zip(pred_te, actual_te)]
        # splits=2: the finite-sample correction needs n_cal >= 9 for
        # alpha=0.1, which a 3-way split of a small holdout cannot give.
        rep = coverage_report(residuals, alpha=0.1, splits=2)
        calibration.update({"q90": rep.get("q"), "achieved": rep.get("achieved"),
                            "calibrated": rep.get("calibrated"),
                            "n_cal": rep.get("n_cal"), "n_test": rep.get("n_test")})
        # ECE analogue: tertiles of predicted value, |mean pred - mean actual|.
        order = sorted(range(len(pred_te)), key=lambda i: pred_te[i])
        thirds, ece_num, ece_den = [order[i::3] for i in range(3)], 0.0, 0
        ece_bins = []
        for b in thirds:
            if not b:
                continue
            mp = sum(pred_te[i] for i in b) / len(b)
            ma = sum(actual_te[i] for i in b) / len(b)
            ece_bins.append({"n": len(b), "mean_pred": round(mp, 4),
                             "mean_actual": round(ma, 4),
                             "gap": round(abs(mp - ma), 4)})
            ece_num += abs(mp - ma) * len(b)
            ece_den += len(b)
        calibration["ece"] = round(ece_num / max(1, ece_den), 4)
        calibration["ece_bins"] = ece_bins
        log("calibration: q90=%s achieved=%s calibrated=%s ece=%s" % (
            calibration.get("q90"), calibration.get("achieved"),
            calibration.get("calibrated"), calibration.get("ece")))
    except Exception as exc:
        log("calibration failed (non-fatal): %s" % str(exc)[:120])

    weights = {k: v.detach().cpu().tolist() for k, v in net.state_dict().items()}
    doc = {"kind": "tensorgene.mlp.weights", "version": 1,
           "genes": list(GENE_KEYS), "hidden": [max(8, w * 2), max(4, w)],
           "activation": "relu", "output": "margin/linear", "loss": "huber",
           "weights": weights, "device": dev,
           "calibration": calibration,
           "n_rows": len(samples), "n_train": len(train), "n_test": len(test),
           "test_era": test_era, "train_mae": train_mae,
           "test_mae": test_mae, "transport": transport,
           "trained_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    try:
        tmp = args.out + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        os.replace(tmp, args.out)
    except Exception as exc:
        log("FAILED to export JSON: %s" % str(exc)[:200])
        return 2
    try:
        torch.save(net.state_dict(), OUT_PT)
    except Exception as exc:
        log("note: .pt checkpoint not written (%s)" % str(exc)[:100])
    try:
        with open(MARKER, "w", encoding="utf-8") as fh:
            json.dump({"n_rows": len(samples), "test_era": test_era,
                       "test_mae": test_mae, "transport": transport,
                       "at": time.strftime("%Y-%m-%dT%H:%M:%S")}, fh, indent=1)
    except Exception:
        pass
    print(json.dumps({"trained": True, "n_rows": len(samples),
                      "test_mae": round(test_mae, 4),
                      "transport": round(transport, 4), "device": dev}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
