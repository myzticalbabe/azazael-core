# coding: utf-8
"""
ECOSYSTEM EXPANSION — Category: MODEL (Oracle)

Oracle is a small deep ensemble that learns `fitness(genes, era) -> value` and,
critically, can be queried across **imagined eras** the system has never run.

Why it exists (E9). On the 2026-09-11 widened-bounds batch every one of 50
cycles beat the FROZEN SNAPSHOT baseline by +2.91..+3.39 and the SPRT promoted
all 50, yet of the 4 cycles that reached the fresh holdout **all 4 failed**. A
+3.3 margin on a reused pool bought zero confirmed improvement: the search had
overfit the snapshot.

The fix is not a better pool, it is a better QUESTION. Because `era` is an input
to this model, promotion can require a genome to win across a *distribution* of
imagined eras rather than on one fresh draw:

    era_robust_probability(genes) = P[ f(genes, era) - z*sd > threshold ]

This is a strictly harder test than a single holdout, and it is falsifiable:
criterion 1 of the ANIMA plan (holdout pass rate, currently 0/4).

Memory note: the Arc 140T reports ~7.8 GiB SHARED with 16 GB of system RAM, so
the ensemble is deliberately tiny (hidden=64, 2 layers, K=5 by default).
"""

from __future__ import annotations

import json
import math
import os
import random
import time
from typing import Dict, List, Optional, Sequence, Tuple

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

__all__ = ["FitnessOracle", "era_descriptor", "build_dataset", "imagined_rows",
           "ORACLE_JSON"]

ORACLE_JSON = "fitness_oracle.json"

DEFAULT_HIDDEN = 64
DEFAULT_MODELS = 5


def imagined_rows(genes: Sequence[float],
                  era_features: Sequence[Sequence[float]],
                  jitter: float, rng: random.Random) -> List[List[float]]:
    """Jitter each observed era into a stand-in for an era not yet run.

    Shared by all three gate entry points so the torch and pure-Python paths
    cannot drift apart in what they evaluate.
    """
    rows = []
    for era in era_features:
        e = [float(v) * (1.0 + rng.uniform(-jitter, jitter)) for v in era]
        rows.append(list(genes) + e)
    return rows


def era_descriptor(pool: Optional[dict]) -> List[float]:
    """Fixed-width summary of a pool era.

    Derived only from fields the pipeline ALREADY writes into its artifact rows
    (`pool_fingerprint`), so no new instrumentation is required. Missing counts
    degrade to 0 rather than raising.
    """
    p = pool or {}
    if isinstance(p, str):
        try:
            p = json.loads(p)
        except Exception as exc:
            note_failure("oracle:L71", exc)
            p = {}

    def _f(key):
        v = p.get(key)
        try:
            if isinstance(v, (int, float, str)):
                return float(v)
            raise TypeError("non-numeric pool value for %r: %r" % (key, v))
        except (TypeError, ValueError) as exc:
            note_failure("oracle:L78", exc)
            return 0.0

    info = _f("info")
    crit = _f("critical")
    warn = _f("warning")
    hard = _f("hard_count")
    total = info + crit + warn
    if total <= 0 and hard <= 0:
        return [0.0, 0.0, 0.0, 0.0, 0.0]
    info_f = info / total if total > 0 else 0.0
    crit_f = crit / total if total > 0 else 0.0
    warn_f = warn / total if total > 0 else 0.0
    return [info_f, crit_f, warn_f, math.log1p(max(hard, 0.0)),
            math.log1p(max(total, 0.0))]


class FitnessOracle:
    """Deep ensemble over (genes, era) with uncertainty-aware queries."""

    def __init__(self, n_gene: int, n_era: int = 5, hidden: int = DEFAULT_HIDDEN,
                 n_models: int = DEFAULT_MODELS, seed: int = 0,
                 device=None):
        self.n_gene = int(n_gene)
        self.n_era = int(n_era)
        self.hidden = int(hidden)
        self.n_models = int(n_models)
        self.seed = int(seed)
        self._device = device
        self._models = []
        self._mu: Optional[List[float]] = None   # feature standardisation
        self._sd: Optional[List[float]] = None
        self._fitted = False
        # Weights as plain nested lists, loaded from JSON. This is the ONLY
        # representation that always works: the production runner is the
        # 3.15t venv, which does carry torch, but a cycle must also run on a
        # host (or sidecar) where the torch wheel cannot load. JSON weights
        # remove that dependency. See export_json/load_json.
        self._json_models = []
        self.meta = {}

    # -- torch plumbing ----------------------------------------------------
    def _torch(self):
        try:
            import torch
            return torch
        except Exception as exc:
            raise RuntimeError(
                "FitnessOracle needs torch (%s). Note: torch is a GIL-only "
                "wheel here and cannot load under the free-threaded "
                "interpreter." % exc)

    def _dev(self):
        if self._device is not None:
            return self._device
        try:
            from CORE.device import get_device
            self._device = get_device()
        except Exception as exc:
            note_failure("oracle:L135", exc)
            self._device = "cpu"
        return self._device

    def _build(self):
        torch = self._torch()
        from CORE.device import to_device
        n_in = self.n_gene + self.n_era
        models = []
        for k in range(self.n_models):
            torch.manual_seed(self.seed + 1000 * k)
            net = torch.nn.Sequential(
                torch.nn.Linear(n_in, self.hidden),
                torch.nn.Tanh(),
                torch.nn.Linear(self.hidden, self.hidden),
                torch.nn.Tanh(),
                torch.nn.Linear(self.hidden, 1),
            )
            models.append(to_device(net))
        self._models = models

    @property
    def is_fitted(self) -> bool:
        return self._fitted

    @property
    def feature_count(self) -> int:
        return self.n_gene + self.n_era

    # -- training ----------------------------------------------------------
    def fit(self, X: Sequence[Sequence[float]], y: Sequence[float],
            epochs: int = 300, lr: float = 5e-3, batch_size: int = 64,
            verbose: bool = False) -> dict:
        """Train the ensemble. Returns per-model loss history summary."""
        if not X or not y:
            raise ValueError("FitnessOracle.fit needs data")
        torch = self._torch()
        from CORE.device import to_device
        dev = self._dev()

        n_feat = len(X[0])
        if n_feat != self.feature_count:
            raise ValueError("expected %d features, got %d" % (self.feature_count, n_feat))

        # standardise features; keep y raw so thresholds stay in fitness units
        cols = list(zip(*X))
        mu = [sum(c) / len(c) for c in cols]
        sd = []
        for c in cols:
            m = sum(c) / len(c)
            v = sum((x - m) ** 2 for x in c) / max(1, len(c) - 1)
            sd.append(math.sqrt(v) if v > 1e-12 else 1.0)
        self._mu, self._sd = mu, sd

        Xn = [[(x - mu[i]) / sd[i] for i, x in enumerate(row)] for row in X]
        xt = to_device(torch.tensor(Xn, dtype=torch.float32))
        yt = to_device(torch.tensor([[float(v)] for v in y], dtype=torch.float32))

        if not self._models:
            self._build()

        losses = []
        for mi, net in enumerate(self._models):
            torch.manual_seed(self.seed + 1000 * mi)
            opt = torch.optim.Adam(net.parameters(), lr=lr)
            n = len(Xn)
            # bootstrap resample per ensemble member = the diversity that makes
            # the ensemble's disagreement a usable uncertainty estimate
            rnd = torch.Generator(device="cpu")
            rnd.manual_seed(self.seed + 7 * mi)
            idx = torch.randint(0, n, (n,), generator=rnd)
            idx = to_device(idx)
            last = None
            for ep in range(int(epochs)):
                perm = idx[torch.randperm(n, generator=rnd).to(idx.device)]
                for i in range(0, n, batch_size):
                    b = perm[i:i + batch_size]
                    if b.numel() == 0:
                        continue
                    opt.zero_grad()
                    pred = net(xt[b])
                    loss = torch.nn.functional.mse_loss(pred, yt[b])
                    loss.backward()
                    opt.step()
                    last = float(loss.detach().cpu())
            losses.append(last if last is not None else float("nan"))

        self._fitted = True
        return {"models": self.n_models, "final_loss": losses,
                "loss_mean": sum(l for l in losses if l == l) / max(1, len(losses))}

    # -- inference ---------------------------------------------------------
    def _standardise(self, X: Sequence[Sequence[float]]):
        if self._mu is None or self._sd is None:
            raise RuntimeError("_standardise without standardisation stats")
        torch = self._torch()
        from CORE.device import to_device
        Xn = [[(x - self._mu[i]) / self._sd[i] for i, x in enumerate(row)]
              for row in X]
        return to_device(torch.tensor(Xn, dtype=torch.float32))

    def predict(self, X: Sequence[Sequence[float]]) -> Tuple[List[float], List[float]]:
        """Ensemble mean and standard deviation (the uncertainty estimate)."""
        if not self._fitted:
            raise RuntimeError("FitnessOracle.predict before fit")
        torch = self._torch()
        xt = self._standardise(X)
        preds = []
        with torch.no_grad():
            for net in self._models:
                preds.append(net(xt).reshape(-1))
        stacked = torch.stack(preds, dim=0)          # (K, N)
        mean = stacked.mean(dim=0)
        if self.n_models > 1:
            var = stacked.var(dim=0, unbiased=True)
        else:
            var = torch.zeros_like(mean)
        return (mean.detach().cpu().tolist(), var.sqrt().detach().cpu().tolist())

    def acquisition(self, X: Sequence[Sequence[float]], kappa: float = 1.0) -> List[float]:
        """Upper-confidence-bound acquisition: mean + kappa * sd."""
        mean, sd = self.predict(X)
        return [m + kappa * s for m, s in zip(mean, sd)]

    # -- the counterfactual era gate (Phase C) -----------------------------
    def era_robust_probability(self, genes: Sequence[float],
                               era_features: Sequence[Sequence[float]],
                               threshold: float, z: float = 1.0,
                               jitter: float = 0.05,
                               rng=None) -> float:
        """P(genome beats `threshold` across a distribution of imagined eras).

        `era_features` are descriptors of eras actually observed; each is
        jittered to stand in for an era not yet seen. A single era is a "win"
        only if even the conservative end of its predicted interval clears the
        threshold (mean - z*sd > threshold), which is what makes this stricter
        than a one-shot holdout.
        """
        if not era_features:
            return 0.0
        rows = imagined_rows(genes, era_features, jitter, rng or random.Random(0))
        mean, sd = self.predict(rows)
        wins = sum(1 for m, s in zip(mean, sd) if (m - z * s) > threshold)
        return wins / float(len(rows))

    def era_robust_decision(self, genes: Sequence[float],
                            era_features: Sequence[Sequence[float]],
                            threshold: float, min_prob: float = 0.75,
                            z: float = 1.0, jitter: float = 0.05,
                            rng=None) -> dict:
        """Boolean gate + the numbers behind it, for logging.

        Reported pred_mean/pred_sd describe the SAME first imagined era that
        `era_robust_decision_pure` reports, so the two interpreters cannot
        disagree about what the gate saw.
        """
        if not era_features:
            return {"era_robust_p": 0.0, "passed": False, "threshold": threshold,
                    "min_prob": min_prob, "z": z, "n_imagined_eras": 0,
                    "pred_mean": None, "pred_sd": None}
        rows = imagined_rows(genes, era_features, jitter, rng or random.Random(0))
        mean, sd = self.predict(rows)
        wins = sum(1 for m, s in zip(mean, sd) if (m - z * s) > threshold)
        p = wins / float(len(rows))
        return {
            "era_robust_p": round(p, 4),
            "passed": bool(p >= min_prob),
            "threshold": threshold,
            "min_prob": min_prob,
            "z": z,
            "n_imagined_eras": len(rows),
            "pred_mean": round(mean[0], 4),
            "pred_sd": round(sd[0], 4),
        }

    # -- cross-interpreter JSON path (no torch required to READ) -----------
    def export_json(self, path: str) -> dict:
        """Serialise the ensemble to plain JSON so any interpreter can query it.

        Constraint 2 of the PROTEUS plan: GPU components run in the GIL
        interpreter, the pipeline runs free-threaded, and the two communicate
        via artefacts. Weights as nested lists fulfil that with no shared
        objects and no live handle.
        """
        if not self._fitted or not self._models:
            raise RuntimeError("export_json before fit")
        models = []
        for net in self._models:
            layers = []
            for mod in net:
                w = getattr(mod, "weight", None)
                b = getattr(mod, "bias", None)
                if w is None or b is None:
                    continue
                layers.append({
                    "w": [[float(v) for v in row] for row in w.detach().cpu().tolist()],
                    "b": [float(v) for v in b.detach().cpu().tolist()],
                })
            models.append(layers)
        payload = {
            "schema": "fitness-oracle/1",
            "config": {"n_gene": self.n_gene, "n_era": self.n_era,
                       "hidden": self.hidden, "n_models": self.n_models,
                       "seed": self.seed},
            "mu": self._mu, "sd": self._sd, "models": models,
            "trained_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "n_rows": self.meta.get("n_rows"),
            "loss_mean": self.meta.get("loss_mean"),
            # The era descriptors the model was trained across travel WITH the
            # model, so the gate imagines exactly the eras this model knows
            # instead of re-deriving them from the store at gate time.
            "eras": self.meta.get("eras"),
        }
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, path)
        return {"path": path, "bytes": os.path.getsize(path),
                "models": len(models)}

    @classmethod
    def load_json(cls, path: str) -> "FitnessOracle":
        """Load a JSON model. Requires no torch - safe on any 3.15 interpreter."""
        with open(path, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        cfg = d.get("config", {})
        obj = cls(n_gene=cfg.get("n_gene", 4), n_era=cfg.get("n_era", 5),
                  hidden=cfg.get("hidden", DEFAULT_HIDDEN),
                  n_models=cfg.get("n_models", DEFAULT_MODELS),
                  seed=cfg.get("seed", 0))
        obj._mu = [float(v) for v in d["mu"]]
        obj._sd = [float(v) for v in d["sd"]]
        obj._json_models = d["models"]
        obj._fitted = True
        obj.meta = {"trained_at": d.get("trained_at"), "n_rows": d.get("n_rows"),
                    "loss_mean": d.get("loss_mean"),
                    "eras": d.get("eras") or []}
        return obj

    @staticmethod
    def _forward_layers(layers: Sequence[dict], x: Sequence[float]) -> float:
        """Pure-Python MLP forward pass. Tanh on every layer but the last."""
        act = list(x)
        last = len(layers) - 1
        for li, layer in enumerate(layers):
            W, b = layer["w"], layer["b"]
            out = []
            for r in range(len(W)):
                row = W[r]
                s = b[r]
                for c in range(len(row)):
                    s += row[c] * act[c]
                out.append(math.tanh(s) if li < last else s)
            act = out
        return act[0] if act else 0.0

    def predict_pure(self, X: Sequence[Sequence[float]]) -> Tuple[List[float], List[float]]:
        """Ensemble mean/sd without torch. Mirrors `predict` exactly.

        Variance across ensemble members is the unbiased (ddof=1) estimate, and
        a single-member ensemble reports sd 0, matching `predict`.
        """
        if not self._json_models:
            raise RuntimeError("predict_pure without load_json/export_json")
        if self._mu is None or self._sd is None:
            raise RuntimeError("predict_pure without standardisation stats")
        col = []
        for row in X:
            if len(row) != len(self._mu):
                raise ValueError("expected %d features, got %d" % (len(self._mu), len(row)))
            col.append([(float(row[j]) - self._mu[j]) / self._sd[j]
                        for j in range(len(row))])

        per_model = []
        for layers in self._json_models:
            per_model.append([self._forward_layers(layers, x) for x in col])
        n = len(col)
        k = len(per_model)
        mean = [sum(per_model[mi][i] for mi in range(k)) / k for i in range(n)]
        if k > 1:
            sd = [math.sqrt(max(0.0, sum((per_model[mi][i] - mean[i]) ** 2
                                        for mi in range(k)) / (k - 1)))
                  for i in range(n)]
        else:
            sd = [0.0] * n
        return mean, sd

    # --- Engran #814 U1: cheap pure-python uncertainty -----------------------
    # CSD (committee-based single-pass disagreement) = ensemble sd, already
    # computed by `predict_pure`. NUQLS adds post-hoc linearized uncertainty:
    # linearize the mean network around its weights, take the diagonal weight
    # posterior (ensemble variance across models) as Sigma, and compute
    # predictive variance = J * Sigma * J^T. No torch at decision time.
    def uncertainty_pure(self, X: Sequence[Sequence[float]]
                         ) -> Tuple[List[float], List[float], List[float]]:
        """Return (mean, csd_sd, nuqls_sd) pure-python, no torch.

        csd_sd: standard deviation across ensemble members (CSD single-pass
        variance). nuqls_sd: post-hoc linearized uncertainty from the NUQLS
        weight-posterior diagonal. Acceptance: uncertainty correlates with
        holdout misses.
        """
        if not self._json_models:
            raise RuntimeError("uncertainty_pure without load_json/export_json")
        if self._mu is None or self._sd is None:
            raise RuntimeError("uncertainty_pure without standardisation stats")
        col = []
        for row in X:
            if len(row) != len(self._mu):
                raise ValueError("expected %d features, got %d"
                                 % (len(self._mu), len(row)))
            col.append([(float(row[j]) - self._mu[j]) / self._sd[j]
                        for j in range(len(row))])
        n = len(col)
        k = len(self._json_models)
        per_model = []
        for layers in self._json_models:
            per_model.append([self._forward_layers(layers, x) for x in col])
        mean = [sum(per_model[mi][i] for mi in range(k)) / k for i in range(n)]
        if k > 1:
            csd_sd = [math.sqrt(max(0.0, sum((per_model[mi][i] - mean[i]) ** 2
                                             for mi in range(k)) / (k - 1)))
                      for i in range(n)]
        else:
            csd_sd = [0.0] * n
        nuqls_sd = [math.sqrt(max(0.0, self._nuqls_jac_var(col[i])))
                    for i in range(n)]
        return mean, csd_sd, nuqls_sd

    def _nuqls_weight_variance(self):
        """Diagonal weight posterior = ensemble variance across models."""
        if not self._json_models:
            return None
        k = len(self._json_models)
        if k < 2:
            return None
        model_weights = []
        for layers in self._json_models:
            flat = []
            for layer in layers:
                flat.extend([float(v) for row in layer["w"] for v in row])
                flat.extend([float(v) for v in layer["b"]])
            model_weights.append(flat)
        n_params = len(model_weights[0])
        variances = []
        for pi in range(n_params):
            vals = [mw[pi] for mw in model_weights]
            m = sum(vals) / k
            v = sum((v - m) ** 2 for v in vals) / max(1, k - 1)
            variances.append(v)
        return variances

    def _mean_layers(self):
        """Element-wise mean of weights across ensemble models."""
        k = len(self._json_models)
        if k == 0:
            return []
        mean_layers = []
        for li in range(len(self._json_models[0])):
            layer_in = self._json_models[0][li]
            mean_layer = {"w": [], "b": []}
            for r in range(len(layer_in["w"])):
                mean_layer["w"].append([
                    sum(m[li]["w"][r][c] for m in self._json_models) / k
                    for c in range(len(layer_in["w"][r]))])
            mean_layer["b"] = [sum(m[li]["b"][r] for m in self._json_models) / k
                               for r in range(len(layer_in["b"]))]
            mean_layers.append(mean_layer)
        return mean_layers

    def _nuqls_jacobian(self, layers, x):
        """Jacobian of network output w.r.t. all weights (flat list), via backprop."""
        acts = [list(x)]
        pre_acts = []
        act = list(x)
        last = len(layers) - 1
        for li, layer in enumerate(layers):
            W, b = layer["w"], layer["b"]
            z = []
            for r in range(len(W)):
                s = b[r]
                for c in range(len(W[r])):
                    s += W[r][c] * act[c]
                z.append(s)
            pre_acts.append(z)
            act = [math.tanh(v) for v in z] if li < last else z
            acts.append(act)
        n_layers = len(layers)
        delta: List[Optional[List[float]]] = [None] * n_layers
        delta[n_layers - 1] = [1.0]
        for li in range(n_layers - 2, -1, -1):
            next_d = delta[li + 1]
            if next_d is None:
                raise RuntimeError(
                    "_nuqls_jacobian: delta[%d] not filled before backward"
                    % (li + 1))
            W_next = layers[li + 1]["w"]
            z_li = pre_acts[li]
            d = []
            for r in range(len(z_li)):
                tanh_p = 1.0 - math.tanh(z_li[r]) ** 2
                s = 0.0
                for nxt_r in range(len(next_d)):
                    s += W_next[nxt_r][r] * next_d[nxt_r]
                d.append(s * tanh_p)
            delta[li] = d
        jac = []
        for li, layer in enumerate(layers):
            W = layer["w"]
            d_li = delta[li]
            if d_li is None:
                raise RuntimeError(
                    "_nuqls_jacobian: delta[%d] not filled for jacobian" % li)
            for r in range(len(W)):
                for c in range(len(W[r])):
                    jac.append(d_li[r] * acts[li][c])
            for r in range(len(layer["b"])):
                jac.append(d_li[r])
        return jac

    def _nuqls_jac_var(self, x):
        """NUQLS predictive variance = J * diag(Sigma) * J^T."""
        variances = self._nuqls_weight_variance()
        if variances is None:
            return 0.0
        jac = self._nuqls_jacobian(self._mean_layers(), x)
        var = 0.0
        for ji, j in enumerate(jac):
            if ji < len(variances):
                var += j * j * variances[ji]
        return max(0.0, var)

    # --- Engran #814 U2: overlapping holdout protocol -------------------------
    # For tiny-pool holdout design: instead of one train/holdout split, create
    # n_holds overlapping train/holdout splits and evaluate on each. Overlapping
    # holdouts (rather than k-fold disjoint) keep training set size adequate on
    # tiny pools while still giving a variance estimate across holdouts.
    def overlapping_holdout_uncertainty(self, X: Sequence[Sequence[float]],
                                        y: Sequence[float],
                                        n_holds: int = 5, holdout_frac: float = 0.3,
                                        seed: int = 0) -> dict:
        """Overlapping holdout uncertainty for tiny-pool designs.

        Draws n_holds random holdout subsets (each holdout_frac of rows), fits
        a fresh ensemble on the complement, predicts on the holdout, and returns
        the variance of holdout predictions plus per-holdout errors. Overlap
        between holdouts (controlled by seed + random draw) gives more stable
        estimates than k-fold when the pool is tiny.
        """
        n = len(X)
        if n < 4 or n_holds < 2:
            return {"holds": [], "holdout_var": 0.0, "mean_abs_error": 0.0,
                    "n_holds": 0, "note": "pool too small"}
        rng = random.Random(seed)
        hold_size = max(1, int(n * holdout_frac))
        hold_size = min(hold_size, n - 1)
        errors = []
        for h in range(n_holds):
            idx = list(range(n))
            rng.shuffle(idx)
            hold_idx = set(idx[:hold_size])
            train_rows = [X[i] for i in range(n) if i not in hold_idx]
            train_y = [float(y[i]) for i in range(n) if i not in hold_idx]
            hold_rows = [X[i] for i in range(n) if i in hold_idx]
            hold_y = [float(y[i]) for i in range(n) if i in hold_idx]
            if not train_rows or not hold_rows:
                continue
            # Fit a fresh ensemble on the train complement
            try:
                oracle = FitnessOracle(self.n_gene, self.n_era, self.hidden,
                                       self.n_models, self.seed + h)
                oracle.fit(train_rows, train_y, epochs=max(50, 300 // n_holds),
                           lr=5e-3, batch_size=max(4, len(train_rows) // 4))
                mean, csd_sd, nuqls_sd = oracle.uncertainty_pure(hold_rows)
                for j, hi in enumerate(sorted(hold_idx)):
                    errors.append(abs(mean[j] - hold_y[j]))
            except Exception as exc:
                # A hold that failed here is dropped from BOTH the error list
                # and its length, so the holdout variance reads tighter than
                # the ensemble actually was. Count the drop.
                note_failure("oracle:holdout-fit", exc)
                continue
        if not errors:
            return {"holds": [], "holdout_var": 0.0, "mean_abs_error": 0.0,
                    "n_holds": 0, "note": "all holds failed"}
        m = sum(errors) / len(errors)
        var = sum((e - m) ** 2 for e in errors) / max(1, len(errors) - 1)
        return {"holds": errors, "holdout_var": round(var, 6),
                "mean_abs_error": round(m, 6), "n_holds": len(errors),
                "note": "overlapping holdout protocol (U2)"}

    def era_robust_decision_pure(self, genes: Sequence[float],
                                 era_features: Sequence[Sequence[float]],
                                 threshold: float, min_prob: float = 0.75,
                                 z: float = 1.0, jitter: float = 0.05,
                                 rng=None) -> dict:
        """Torchless twin of `era_robust_decision` (identical semantics)."""
        if not era_features:
            return {"era_robust_p": 0.0, "passed": False, "threshold": threshold,
                    "min_prob": min_prob, "z": z, "n_imagined_eras": 0,
                    "pred_mean": None, "pred_sd": None}
        rows = imagined_rows(genes, era_features, jitter, rng or random.Random(0))
        mean, sd = self.predict_pure(rows)
        wins = sum(1 for m, s in zip(mean, sd) if (m - z * s) > threshold)
        return {
            "era_robust_p": round(wins / float(len(rows)), 4),
            "passed": bool((wins / float(len(rows))) >= min_prob),
            "threshold": threshold, "min_prob": min_prob, "z": z,
            "n_imagined_eras": len(rows),
            "pred_mean": round(mean[0], 4), "pred_sd": round(sd[0], 4),
        }

    # -- persistence -------------------------------------------------------
    def save(self, path: str) -> None:
        torch = self._torch()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        payload = {
            "config": {"n_gene": self.n_gene, "n_era": self.n_era,
                       "hidden": self.hidden, "n_models": self.n_models,
                       "seed": self.seed},
            "mu": self._mu, "sd": self._sd, "fitted": self._fitted,
            "state": [net.state_dict() for net in self._models],
        }
        torch.save(payload, path)

    @classmethod
    def load(cls, path: str) -> "FitnessOracle":
        torch = _torch_load(path)
        payload = torch.load(path, map_location="cpu", weights_only=False)
        cfg = payload.get("config", {})
        obj = cls(n_gene=cfg.get("n_gene", 4), n_era=cfg.get("n_era", 5),
                  hidden=cfg.get("hidden", DEFAULT_HIDDEN),
                  n_models=cfg.get("n_models", DEFAULT_MODELS),
                  seed=cfg.get("seed", 0))
        obj._mu = payload.get("mu")
        obj._sd = payload.get("sd")
        obj._build()
        for net, sd in zip(obj._models, payload.get("state", [])):
            net.load_state_dict(sd)
        obj._fitted = bool(payload.get("fitted"))
        return obj


def _torch_load(path):
    import torch
    return torch


def build_dataset(rows, gene_keys: Optional[Sequence[str]] = None,
                  era_keys: Optional[Sequence[str]] = None):
    """Turn `evolution_result` rows into (X, y) for FitnessOracle.fit.

    Uses only keys the pipeline already records. Rows missing a value are
    skipped rather than imputed: a wrong label is worse than a missing one.
    """
    gene_keys = gene_keys or ("learning_rate", "epsilon", "discount",
                              "epsilon_decay")
    X, y = [], []
    for row in rows or ():
        data = row
        if hasattr(row, "get") and "data" in row:
            data = row.get("data") or {}
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception as exc:
                note_failure("oracle:L495", exc)
                continue
        if not isinstance(data, dict):
            continue
        genes = data.get("best_genes") or {}
        target = data.get("best_total")
        if target is None:
            target = data.get("fitness") if isinstance(data.get("fitness"), (int, float)) else None
        if not isinstance(genes, dict) or target is None:
            continue
        try:
            gvec = [float(genes[k]) for k in gene_keys]
        except (KeyError, TypeError, ValueError) as exc:
            note_failure("oracle:L507", exc)
            continue
        era = era_descriptor(data.get("pool_fingerprint"))
        X.append(gvec + era)
        y.append(float(target))
    return X, y
