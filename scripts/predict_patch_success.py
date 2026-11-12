#!/usr/bin/env python3
# coding: utf-8
"""predict_patch_success.py — surrogate over proposal outcomes (ML angles).

Trains a logistic regression on state/proposal_outcomes.jsonl to predict
P(retained | proposal features). Honest gate: refuses with exit 3 below
MIN_ROWS (default 30) — a surrogate fit on a handful of rows is numerology,
not ML. Features are all pre-decision observables (detector one-hots would
explode dimensionality; use severity rank, pattern size, attempt count,
agreement flag, model identity).

Usage:
    python scripts/predict_patch_success.py            # train + report
    python scripts/predict_patch_success.py --predict '{"severity":"HIGH","pattern_sites":3,"n_attempts":2,"agreement":"unanimous","model":"debate"}'
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTCOMES = ROOT / "state" / "proposal_outcomes.jsonl"
MODEL_JSON = ROOT / "state" / "patch_success_model.json"
MIN_ROWS = 30
SEV_RANK = {"LOW": 0, "MED": 1, "HIGH": 2, "CRITICAL": 3}


def load_rows() -> list[dict]:
    rows = []
    try:
        for line in OUTCOMES.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return rows


def featurize(r: dict) -> list[float]:
    models = r.get("models") or []
    return [
        float(SEV_RANK.get(str(r.get("severity", "")).upper(), 1)),
        float(r.get("pattern_sites") or 0),
        float(r.get("n_attempts") or 1),
        1.0 if r.get("agreement") == "unanimous" else 0.0,
        1.0 if "debate" in models else 0.0,
    ]


FEATURES = ["severity_rank", "pattern_sites", "n_attempts",
            "unanimous", "via_debate"]


def train(rows: list[dict]) -> dict:
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import cross_val_score
    import numpy as np
    X = np.array([featurize(r) for r in rows])
    y = np.array([1 if r.get("retained") else 0 for r in rows])
    if len(set(y.tolist())) < 2:
        return {"ok": False, "reason": "single-class outcomes (no contrast yet)"}
    clf = LogisticRegression(max_iter=1000)
    scores = cross_val_score(clf, X, y, cv=min(5, len(X) // 2 + 1))
    clf.fit(X, y)
    return {"ok": True, "cv_accuracy": round(float(scores.mean()), 4),
            "cv_std": round(float(scores.std()), 4),
            "weights": {f: round(float(w), 4)
                        for f, w in zip(FEATURES, clf.coef_[0])},
            "intercept": round(float(clf.intercept_[0]), 4),
            "n_rows": len(rows),
            "base_rate": round(float(y.mean()), 4)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="predict_patch_success")
    ap.add_argument("--predict", default="",
                    help="JSON feature row (uses saved model)")
    ap.add_argument("--min-rows", type=int, default=MIN_ROWS)
    args = ap.parse_args(argv)

    if args.predict:
        try:
            saved = json.loads(MODEL_JSON.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            print("no saved model — train first")
            return 3
        r = json.loads(args.predict)
        import math
        z = saved["intercept"] + sum(
            saved["weights"][f] * v
            for f, v in zip(FEATURES, featurize(r)))
        print(f"P(retained) = {1.0 / (1.0 + math.exp(-z)):.3f}")
        return 0

    rows = load_rows()
    print(f"outcome rows: {len(rows)} (need {args.min_rows})")
    if len(rows) < args.min_rows:
        print("insufficient data — surrogate refused (collect more cycles)")
        return 3
    rep = train(rows)
    if not rep.get("ok"):
        print(f"refused: {rep.get('reason')}")
        return 3
    MODEL_JSON.write_text(json.dumps(rep, indent=1), encoding="utf-8")
    print(f"CV accuracy {rep['cv_accuracy']} ± {rep['cv_std']} "
          f"(base rate {rep['base_rate']})")
    for f, w in rep["weights"].items():
        print(f"  {f:<14} {w:+.4f}")
    print(f"saved: {MODEL_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
