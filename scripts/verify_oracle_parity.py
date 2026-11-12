# coding: utf-8
"""Parity check: pure-Python oracle JSON forward pass vs the torch path.

The free-threaded pipeline can only use `predict_pure`. If the two disagree,
every gate decision made under `python3.15t` is wrong -- so this compares them
on identical inputs and fails loudly on any material difference.

Run under the 3.15 GIL sidecar (torch loads there but not under free-threading):
    C:/Azazael/.venvs/py315/Scripts/python.exe scripts/verify_oracle_parity.py
"""
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from CORE.WORMS.expansion.oracle import FitnessOracle  # noqa: E402

JSON_PATH = os.path.join(ROOT, "state", "fitness_oracle.json")
TOL = 2e-4


def main():
    if not os.path.exists(JSON_PATH):
        print("SKIP: %s not found (run scripts/train_fitness_oracle.py)" % JSON_PATH)
        return 3

    j = FitnessOracle.load_json(JSON_PATH)
    # A torch twin with the same weights: load the .pt if present, otherwise
    # rebuild the torch nets from the JSON so both paths see identical weights.
    twin = FitnessOracle(n_gene=j.n_gene, n_era=j.n_era, hidden=j.hidden,
                         n_models=j.n_models, seed=j.seed)
    twin._mu, twin._sd, twin._fitted = j._mu, j._sd, True
    twin._torch()          # raises clearly if torch is unavailable
    twin._build()
    import torch
    for net, layers in zip(twin._models, j._json_models):
        with torch.no_grad():
            li = 0
            for mod in net:
                if getattr(mod, "weight", None) is None:
                    continue
                L = layers[li]
                mod.weight.copy_(torch.tensor(L["w"], dtype=mod.weight.dtype))
                mod.bias.copy_(torch.tensor(L["b"], dtype=mod.bias.dtype))
                li += 1

    rng = random.Random(0)
    X = [[rng.uniform(0, 1) for _ in range(twin.n_gene)]
         + [rng.uniform(0, 6) for _ in range(twin.n_era)] for _ in range(64)]

    tm, ts = twin.predict(X)
    pm, ps = j.predict_pure(X)

    dm = max(abs(a - b) for a, b in zip(tm, pm))
    ds = max(abs(a - b) for a, b in zip(ts, ps))
    print("models      : %d  features=%d" % (j.n_models, twin.feature_count))
    print("max |dmean| : %.3e" % dm)
    print("max |dsd|   : %.3e" % ds)

    # Era-robust decision parity as well: this is the number the gate reads.
    genes = X[0][:j.n_gene]
    eras = [row[j.n_gene:] for row in X[:6]]
    td = twin.era_robust_decision(genes, eras, threshold=25.0, min_prob=0.75)
    pd = j.era_robust_decision_pure(genes, eras, threshold=25.0, min_prob=0.75)
    print("torch  :", td)
    print("pure   :", pd)
    same = td["passed"] == pd["passed"] and abs(
        td["era_robust_p"] - pd["era_robust_p"]) <= 0.05

    if dm <= TOL and ds <= TOL * 10 and same:
        print("\nPARITY OK")
        return 0
    print("\nPARITY FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
