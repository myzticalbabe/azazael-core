# coding: utf-8
"""eval_utils.py — P0 eval hardening (2026-09-15): walk-forward + ECE."""
from typing import Callable, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


def walk_forward(series, min_train: int, step: int,
                 forecast_fn: Callable, metric_fn: Callable | None = None):
    """Expanding-window 1-step backtest. Returns list of (i, y, yhat, err)."""
    metric_fn = metric_fn or (lambda y, yh: abs(y - yh))
    out = []
    for i in range(min_train, len(series), step):
        yhat = forecast_fn(series[:i])
        y = series[i]
        out.append((i, y, yhat, metric_fn(y, yhat)))
    return out


def ece_score(y_true, y_prob, n_bins: int = 10) -> float:
    """Expected Calibration Error (OOF-ready)."""
    bins = [[] for _ in range(n_bins)]
    for y, p in zip(y_true, y_prob):
        b = min(n_bins - 1, int(p * n_bins))
        bins[b].append((y, p))
    n = max(1, len(y_true))
    ece = 0.0
    for b in bins:
        if not b:
            continue
        acc = sum(y for y, _ in b) / len(b)
        conf = sum(p for _, p in b) / len(b)
        ece += len(b) / n * abs(acc - conf)
    return ece


def brier_score(y_true, y_prob) -> float:
    n = max(1, len(y_true))
    return sum((p - y) ** 2 for y, p in zip(y_true, y_prob)) / n
