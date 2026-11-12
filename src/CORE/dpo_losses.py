# coding: utf-8
"""dpo_losses.py — DPO family losses on raw logps. Torch-only, model-free.

Vanilla DPO + TI-DPO (token-importance) + InSPO (symmetric cross-condition)
+ RAPPO margin filter. All operate on precomputed logp tensors so they
unit-test without a model. CPU-safe.
"""
import math
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


def _logsigmoid(x: float) -> float:
    return -math.log1p(math.exp(-x)) if x >= 0 else x - math.log1p(math.exp(x))


def dpo_loss(logp_w: list[float], logp_l: list[float],
             ref_w: list[float], ref_l: list[float],
             beta: float = 0.1) -> float:
    """Vanilla DPO: -mean(log sigmoid(beta * margin))."""
    tot, n = 0.0, 0
    for lw, ll, rw, rl in zip(logp_w, logp_l, ref_w, ref_l):
        margin = (lw - rw) - (ll - rl)
        tot += -_logsigmoid(beta * margin)
        n += 1
    return tot / max(1, n)


def ti_dpo_loss(logp_w: list[float], logp_l: list[float],
                ref_w: list[float], ref_l: list[float],
                imp_w: list[float] | None = None,
                imp_l: list[float] | None = None,
                beta: float = 0.1) -> float:
    """TI-DPO: token-importance-weighted margins (grad-attrib proxy weights)."""
    iw = imp_w or [1.0] * len(logp_w)
    il = imp_l or [1.0] * len(logp_l)
    tot, n = 0.0, 0
    for lw, ll, rw, rl, a, b in zip(logp_w, logp_l, ref_w, ref_l, iw, il):
        margin = a * (lw - rw) - b * (ll - rl)
        tot += -_logsigmoid(beta * margin)
        n += 1
    return tot / max(1, n)


def inspo_loss(logp_w: list[float], logp_l: list[float],
               ref_w: list[float], ref_l: list[float],
               beta: float = 0.1, gamma: float = 0.5) -> float:
    """InSPO: symmetric cross-condition DPO + distillation pull to chosen."""
    fwd = dpo_loss(logp_w, logp_l, ref_w, ref_l, beta)
    rev = dpo_loss(logp_l, logp_w, ref_l, ref_w, beta)
    # distillation: keep mass on chosen (mean NLL proxy via -logp)
    nll = -sum(logp_w) / max(1, len(logp_w))
    return fwd + gamma * rev + 0.1 * gamma * nll


def rappo_filter(margins: list[float], drop_frac: float = 0.2) -> list[int]:
    """Keep indices of top-(1-drop_frac) margins; drop ambiguous tail."""
    if not margins:
        return []
    order = sorted(range(len(margins)), key=lambda i: margins[i], reverse=True)
    keep = max(1, int(len(margins) * (1.0 - drop_frac)))
    return sorted(order[:keep])
