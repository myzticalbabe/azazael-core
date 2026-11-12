# coding: utf-8
"""layerwise.py — P0 layer-wise memory savers (2026-09-15).

LISA sampling + BAdam block-coordinate + MeZO forward-only + GaLore
low-rank grad projection. Stdlib-first, torch-optional, CPU-tested.
"""
import random
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


def lisa_sample(n_layers: int, gamma: int = 2, rng=None):
    """Always keep layer 0 + head (N-1); sample gamma mids. Returns sorted ids."""
    rng = rng or random.Random(0)
    mids = list(range(1, max(n_layers - 1, 1)))
    rng.shuffle(mids)
    keep = {0, n_layers - 1} | set(mids[:max(0, gamma)])
    return sorted(keep)


def badam_blocks(n_layers: int, n_blocks: int = 4):
    """Partition layers into n_blocks contiguous blocks for BAdam cycling."""
    n_blocks = max(1, min(n_blocks, n_layers))
    base, rem = divmod(n_layers, n_blocks)
    out, s = [], 0
    for i in range(n_blocks):
        e = s + base + (1 if i < rem else 0)
        out.append(list(range(s, e)))
        s = e
    return out


def badam_memory_gb(m_params_b: float, n_blocks: int) -> float:
    """BAdam mixed-precision peak: 2M + 16M/D GB."""
    return 2 * m_params_b + 16 * m_params_b / max(1, n_blocks)


def mezo_perturb(params, eps: float = 1e-3, seed: int = 0):
    """SPSA perturb direction (regenerable from seed — no z storage)."""
    rng = random.Random(seed)
    return [rng.gauss(0, 1) * eps for _ in params]


def mezo_two_point_loss(loss_fn, params, z, eps: float = 1e-3):
    """Return (l+, l-) for gradient estimate g = (l+ - l-)/2eps * z."""
    pp = [p + zi for p, zi in zip(params, z)]
    pm = [p - zi for p, zi in zip(params, z)]
    return loss_fn(pp), loss_fn(pm)


def galore_project(grad_rows, rank: int = 8):
    """Toy low-rank projection: keep top-rank rows by L2 (real: SVD P^T G Q).

    Returns (projected, scale) where projected is rank x cols.
    Memory: optimizer tracks rank rows, not full matrix.
    """
    scored = sorted(enumerate(grad_rows),
                    key=lambda t: sum(v * v for v in t[1]), reverse=True)
    return [row[:] for _, row in scored[:rank]]


def ows_probs(outlier_counts, gamma: int = 2):
    """Outlier-weighed sampling: p_l propto count_l, E[unfrozen]=gamma."""
    tot = sum(outlier_counts) or 1.0
    return [c / tot * gamma for c in outlier_counts]
