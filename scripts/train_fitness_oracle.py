"""train_fitness_oracle.py — (re)train the PROTEUS imagined-era model (F-P0-3).

Spawned by evolution_cycle._train_oracle_subprocess under the GIL interpreter
(torch cannot load under 3.14t). Missing since forever: the cycle referenced
this path while the file never existed, so fitness_oracle.json froze and the
imagined-era gate evaluated candidates against 5-day-old eras.

Training data: last 200 `evolution_result` memory rows (best_genes,
best_total) — the same rows TPE seeds from. Era features via
oracle.era_descriptor (degrades to 0, never raises). Minimum 8 usable rows
(same bar as _tpe_seeds); fewer -> exit 2 with a clear line, no empty model.

Only JSON crosses back: existing model backed up to fitness_oracle.last.json
before overwrite. Prints one JSON summary line for the cycle log.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

STATE_DIR = os.path.join(ROOT, "state")
ORACLE_JSON = os.path.join(STATE_DIR, "fitness_oracle.json")
ORACLE_LAST = os.path.join(STATE_DIR, "fitness_oracle.last.json")
MIN_ROWS = 8
HISTORY_LIMIT = 200
EPOCHS = 100


def _rows():
    from CORE.local_memory import get_memory_store
    store = get_memory_store()
    return store.query_memories(event_type="evolution_result",
                                limit=HISTORY_LIMIT) or []


def main() -> int:
    from CORE.WORMS.expansion.oracle import FitnessOracle, era_descriptor
    from CORE.WORMS.expansion.rl_gene_bounds import RL_GENE_BOUNDS

    keys = list(RL_GENE_BOUNDS)
    X, y, eras = [], [], []
    for r in _rows():
        d = r.get("data", {}) or {}
        if isinstance(d, str):
            try:
                d = json.loads(d)
            except Exception:
                continue
        g, t = d.get("best_genes"), d.get("best_total")
        if not isinstance(g, dict) or not isinstance(t, (int, float)):
            continue
        try:
            vec = [float(g[k]) for k in keys]
        except Exception:
            continue
        era = era_descriptor(d.get("pool") or d.get("pool_fingerprint")
                             or d.get("pool_id"))
        X.append(vec + [float(v) for v in era])
        y.append(float(t))
        eras.append([float(v) for v in era])
    if len(X) < MIN_ROWS:
        print(json.dumps({"ok": False,
                          "error": f"only {len(X)} usable rows (< {MIN_ROWS})"}))
        return 2
    n_era = len(eras[0])
    oracle = FitnessOracle(n_gene=len(keys), n_era=n_era)
    summary = oracle.fit(X, y, epochs=EPOCHS)
    oracle.meta = {"n_rows": len(X), "loss_mean": summary.get("loss_mean"),
                   "eras": eras, "gene_keys": keys,
                   "trained_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    if os.path.exists(ORACLE_JSON):
        try:
            shutil.copy2(ORACLE_JSON, ORACLE_LAST)
        except OSError:
            pass
    info = oracle.export_json(ORACLE_JSON)
    print(json.dumps({"ok": True, "rows": len(X), "epochs": EPOCHS,
                      "loss_mean": summary.get("loss_mean"), **info}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
