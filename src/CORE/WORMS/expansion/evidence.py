# coding: utf-8
"""
ECOSYSTEM EXPANSION — Category: EVIDENCE (Cranium)

Cranium replaces the era-PINNED SPRT in `scripts/evolution_cycle.py`. That test
hard-stops at the first change of `(pool_id, baseline_hash)`, which on the
2026-09-11 batch happened 28 times in 51 cycles (~1.8 cycles per era). Since the
test needs 2..6 same-era observations, evidence could essentially never
accumulate: 1 promotion in 51 cycles.

Model. Each era is a random effect::

    margin_ij = mu + e_i + noise,   e_i ~ N(0, tau^2),   noise ~ N(0, sigma^2)

`mu` is the effect that actually matters ("does this genome beat the baseline?")
and it is shared across eras. Evidence is pooled through `mu` rather than
discarded, so a new era ADDS information instead of resetting the count.

Inference is closed-form empirical Bayes — no MCMC, no dependencies beyond the
stdlib, so it runs identically under the free-threaded interpreter:

1. pooled within-era variance  s2_w  = sum_ij (y_ij - ybar_i)^2 / (N - K)
2. between-era variance        tau2  = max(0, var(ybar_i) - s2_w * mean(1/n_i))
3. each era mean is an observation of `mu` with Var = tau2 + s2_w / n_i
4. precision-weighted posterior for `mu`, then P(mu > threshold)

Shrinkage is the point: an era with one noisy observation gets pulled toward the
grand mean, while a well-sampled era speaks for itself.
"""

from __future__ import annotations

import json
import math
from statistics import NormalDist
from typing import Any, Optional, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

__all__ = ["EraEvidence", "evidence_from_rows"]

_NORM = NormalDist()
# Below this the pooled within-era variance is treated as unmeasurable and the
# caller-supplied noise floor is used instead (avoiding a division by zero on a
# single observation per era).
_MIN_DOF = 1


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _var(xs):
    """Unbiased sample variance."""
    n = len(xs)
    if n < 2:
        return 0.0
    m = _mean(xs)
    return sum((x - m) ** 2 for x in xs) / (n - 1)


class EraEvidence:
    """Hierarchical cross-era posterior over the true effect size.

    Usage::

        ev = EraEvidence(threshold=0.75)
        ev.observe("era-41", 2.87)
        ev.observe("era-42", 3.02)
        ev.prob_above()        # P(mu > 0.75)
        ev.should_promote()    # bool at the configured alpha
    """

    def __init__(self, threshold: float = 0.75, alpha: float = 0.05,
                 prior_mean: float = 0.0, prior_var: float = 1.0,
                 noise_floor: float = 1e-6):
        self.threshold = float(threshold)
        self.alpha = float(alpha)
        self.prior_mean = float(prior_mean)
        self.prior_var = max(float(prior_var), 1e-9)
        self.noise_floor = abs(float(noise_floor))
        self._eras: dict[str, list[float]] = {}

    # -- data --------------------------------------------------------------
    def observe(self, era_id: str, margin: float) -> None:
        """Record one margin observation inside a named era."""
        try:
            m = float(margin)
        except (TypeError, ValueError) as exc:
            note_failure("evidence:L87", exc)
            return
        if not math.isfinite(m):
            return
        self._eras.setdefault(str(era_id), []).append(m)

    def extend(self, era_id: str, margins) -> None:
        for m in margins or ():
            self.observe(era_id, m)

    def clear(self) -> None:
        self._eras.clear()

    # -- derived quantities ------------------------------------------------
    def era_means(self) -> dict:
        return {k: _mean(v) for k, v in self._eras.items()}

    def era_counts(self) -> dict:
        return {k: len(v) for k, v in self._eras.items()}

    @property
    def n_eras(self) -> int:
        return len(self._eras)

    @property
    def n_observations(self) -> int:
        return sum(len(v) for v in self._eras.values())

    def _sigma2(self) -> float:
        """Pooled within-era variance, falling back to the noise floor."""
        groups = [v for v in self._eras.values() if v]
        n = sum(len(g) for g in groups)
        k = len(groups)
        dof = n - k
        if dof < _MIN_DOF:
            return self.noise_floor ** 2
        ss = 0.0
        for g in groups:
            m = _mean(g)
            ss += sum((x - m) ** 2 for x in g)
        return max(ss / dof, self.noise_floor ** 2)

    def _tau2(self, s2_w: float) -> float:
        """Method-of-moments between-era variance, floored at 0.

        With one era there is no between-era signal to estimate, so tau2 = 0 and
        the posterior reduces to a plain normal mean — the pre-2026 behaviour,
        which is the correct degenerate case.
        """
        means = [m for m in self.era_means().values()]
        counts = [len(v) for v in self._eras.values() if v]
        if len(means) < 2:
            return 0.0
        obs_var = _var(means)
        shrink = s2_w * _mean([1.0 / c for c in counts if c > 0])
        return max(0.0, obs_var - shrink)

    # -- posterior ---------------------------------------------------------
    def posterior(self) -> dict:
        """Precision-weighted posterior for the global mean `mu`.

        Each era's mean is treated as one observation of `mu` with variance
        `tau2 + sigma^2 / n_i`. This is what lets a single-observation era
        contribute without being able to dominate an era with many.
        """
        groups = [(k, v) for k, v in self._eras.items() if v]
        if not groups:
            return {
                "mean": self.prior_mean, "var": self.prior_var,
                "sd": math.sqrt(self.prior_var), "n_eras": 0,
                "n_obs": 0, "sigma": math.sqrt(self._sigma2()) * 0 + 0.0,
                "tau": 0.0, "shrinkage": {}, "era_means": {},
            }

        s2_w = self._sigma2()
        tau2 = self._tau2(s2_w)

        prec_sum = 0.0
        weighted = 0.0
        shrinkage = {}
        for key, vals in groups:
            n_i = len(vals)
            ybar = _mean(vals)
            var_i = tau2 + s2_w / n_i
            prec_i = 1.0 / max(var_i, 1e-12)
            prec_sum += prec_i
            weighted += prec_i * ybar
            # how much this era's own mean is trusted vs the grand mean
            shrinkage[key] = tau2 / (tau2 + s2_w / n_i) if tau2 > 0 else 0.0

        post_prec = 1.0 / self.prior_var + prec_sum
        post_var = 1.0 / post_prec
        post_mean = (self.prior_mean / self.prior_var + weighted) / post_prec

        return {
            "mean": post_mean,
            "var": post_var,
            "sd": math.sqrt(post_var),
            "n_eras": len(groups),
            "n_obs": sum(len(v) for _, v in groups),
            "sigma": math.sqrt(s2_w),
            "tau": math.sqrt(tau2),
            "shrinkage": shrinkage,
            "era_means": {k: _mean(v) for k, v in groups},
        }

    def prob_above(self, threshold: Optional[float] = None) -> float:
        """P(mu > threshold) under the posterior. 0.5 on no evidence."""
        post = self.posterior()
        if post["n_obs"] == 0:
            return 0.0
        t = self.threshold if threshold is None else float(threshold)
        sd = max(post["sd"], 1e-12)
        return float(1.0 - _NORM.cdf((t - post["mean"]) / sd))

    def should_promote(self, alpha: Optional[float] = None,
                       threshold: Optional[float] = None) -> bool:
        """Promote only when the posterior clears the gate at level alpha."""
        a = self.alpha if alpha is None else float(alpha)
        return self.prob_above(threshold) >= (1.0 - a)

    def reason(self) -> str:
        """One-line explanation for the cycle log."""
        p = self.posterior()
        return ("cranium: P(mu>%.2f)=%.4f n_eras=%d n_obs=%d "
                "posterior=%.4f+/-%.4f sigma=%.4f tau=%.4f" % (
                    self.threshold, self.prob_above(), p["n_eras"], p["n_obs"],
                    p["mean"], p["sd"], p["sigma"], p["tau"]))

    # -- persistence (the two-interpreter bridge) --------------------------
    def to_dict(self) -> dict:
        return {
            "threshold": self.threshold, "alpha": self.alpha,
            "prior_mean": self.prior_mean, "prior_var": self.prior_var,
            "noise_floor": self.noise_floor, "eras": self._eras,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EraEvidence":
        obj = cls(
            threshold=data.get("threshold", 0.75),
            alpha=data.get("alpha", 0.05),
            prior_mean=data.get("prior_mean", 0.0),
            prior_var=data.get("prior_var", 1.0),
            noise_floor=data.get("noise_floor", 1e-6),
        )
        for era, vals in (data.get("eras") or {}).items():
            obj.extend(era, vals)
        return obj

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2)

    @classmethod
    def load(cls, path: str) -> "EraEvidence":
        with open(path, encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))


def evidence_from_rows(rows: Any, margin_key: str = "margin",
                       era_key: str = "pool_id", threshold: float = 0.75,
                       **kwargs) -> EraEvidence:
    """Build a Cranium from `evolution_result` rows.

    Rows are the artifacts the pipeline already writes, so this needs no new
    logging. A row without a usable margin or era is skipped, not fatal.
    """
    ev = EraEvidence(threshold=threshold, **kwargs)
    for row in rows or ():
        data = row
        if hasattr(row, "get") and "data" in row:
            data = row.get("data") or {}
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception as exc:
                note_failure("evidence:L263", exc)
                continue
        if not isinstance(data, dict):
            continue
        margin = data.get(margin_key)
        if margin is None:
            try:
                best_total = data.get("best_total")
                baseline = data.get("baseline")
                if best_total is None or baseline is None:
                    raise TypeError("missing best_total/baseline for margin")
                margin = float(best_total) - float(baseline)
            except (TypeError, ValueError) as exc:
                note_failure("evidence:L271", exc)
                continue
        ev.observe(str(data.get(era_key, "unknown")), margin)
    return ev
