# coding: utf-8
"""
ECOSYSTEM EXPANSION — Category: ENCODING (Morph)

CPPN — Compositional Pattern-Producing Network.

This is the correct answer to "how many tunable knobs can the genome have?".
A direct encoding (the current 4-gene vector, and PROTEUS's variable-length
vector) needs one gene per parameter, so the search space is capped by the
genome. A CPPN **decouples the two**: the genome is a small network whose INPUT
is a coordinate and whose OUTPUT is the value at that coordinate. Query it over
N coordinates and it emits an N-dimensional phenotype from ~30 genes.

    genome (weights, ~30 floats)  ->  CPPN(coords)  ->  phenotype (any size)

Why this matters for this codebase: `EvolutionEngine` already supports mixed
gene types, but every encoding is direct. With a CPPN the 4-gene ceiling
disappears by construction rather than by adding slots.

Runtime note: torch is optional here. The pure-Python path exists so a CPPN stays
usable wherever torch will not load — the 3.15t runner carries torch (verified
2026-10-19), but the GIL sidecar and any host without that wheel do not, and a
CPPN that required torch would be unusable in the pipeline it is meant to serve.
"""

from __future__ import annotations

import json
import math
import random
from typing import List, Optional, Sequence, Tuple

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

__all__ = ["CPPNGenome", "DEFAULT_HIDDEN"]

DEFAULT_HIDDEN: Tuple[int, ...] = (16, 16)


def _activate(x: float, kind: str) -> float:
    if kind == "tanh":
        return math.tanh(x)
    if kind == "sin":
        return math.sin(x)
    if kind == "sigmoid":
        # branch-free, no overflow
        if x < -60.0:
            return 0.0
        if x > 60.0:
            return 1.0
        return 1.0 / (1.0 + math.exp(-x))
    if kind == "gauss":
        return math.exp(-x * x)
    return math.tanh(x)


class CPPNGenome:
    """A CPPN whose flattened weights are the genome.

    Layers: n_in -> hidden... -> n_out, each with a bias. Weights are initialised
    in [-bound, bound]; the bound doubles as the mutation clamp.
    """

    def __init__(self, n_in: int = 3, hidden: Sequence[int] = DEFAULT_HIDDEN,
                 n_out: int = 1, activation: str = "tanh", bound: float = 3.0):
        self.n_in = int(n_in)
        self.hidden = tuple(int(h) for h in hidden)
        self.n_out = int(n_out)
        self.activation = activation
        self.bound = float(bound)
        self._shapes = self._layer_shapes()

    # -- structure ---------------------------------------------------------
    def _layer_shapes(self) -> List[Tuple[int, int]]:
        sizes = [self.n_in] + list(self.hidden) + [self.n_out]
        return [(sizes[i], sizes[i + 1]) for i in range(len(sizes) - 1)]

    @property
    def gene_count(self) -> int:
        """Number of genes — independent of how large a phenotype is produced."""
        total = 0
        for fan_in, fan_out in self._shapes:
            total += fan_in * fan_out + fan_out   # weights + biases
        return total

    def describe(self) -> dict:
        return {
            "n_in": self.n_in, "hidden": list(self.hidden), "n_out": self.n_out,
            "activation": self.activation, "bound": self.bound,
            "gene_count": self.gene_count,
        }

    # -- genes -------------------------------------------------------------
    def sample(self, rng: Optional[random.Random] = None) -> List[float]:
        r = rng or random.Random()
        return [r.uniform(-self.bound, self.bound) for _ in range(self.gene_count)]

    def mutate(self, vec: Sequence[float], rate: float = 0.2,
               sigma: float = 0.3, rng: Optional[random.Random] = None,
               levy_alpha: Optional[float] = None) -> List[float]:
        """Gaussian (or Levy) mutation, clamped to the gene bound.

        `levy_alpha` switches to a heavy-tailed step. Heavy tails are what let a
        search escape a local optimum that Gaussian noise cannot leave - the
        signature of the 42/50 flat trajectories seen on 2026-09-11.
        """
        r = rng or random.Random()
        out = []
        for g in vec:
            v = float(g)
            if r.random() < rate:
                if levy_alpha:
                    u = max(r.random(), 1e-12)
                    step = sigma * (u ** (-1.0 / float(levy_alpha)))
                    step *= -1.0 if r.random() < 0.5 else 1.0
                    v += step
                else:
                    v += r.gauss(0.0, sigma)
                v = max(-self.bound, min(self.bound, v))
            out.append(v)
        return out

    def _unpack(self, vec: Sequence[float]):
        mats = []
        i = 0
        for fan_in, fan_out in self._shapes:
            w = [list(vec[i + r * fan_out:(i + r * fan_out) + fan_out])
                 for r in range(fan_in)]
            i += fan_in * fan_out
            b = list(vec[i:i + fan_out])
            i += fan_out
            mats.append((w, b))
        return mats

    # -- phenotype ---------------------------------------------------------
    def _forward_python(self, x: Sequence[float], mats) -> List[float]:
        h = list(x)
        for li, (w, b) in enumerate(mats):
            last = (li == len(mats) - 1)
            out = []
            for j in range(len(b)):
                acc = b[j]
                for i in range(len(h)):
                    acc += w[i][j] * h[i]
                # output layer stays linear so the phenotype is not squashed
                out.append(acc if last else _activate(acc, self.activation))
            h = out
        return h

    def _forward_torch(self, coords, mats):
        import torch  # local: only when actually available
        from CORE.device import get_device, to_device
        dev = get_device()
        x = coords
        if not isinstance(x, torch.Tensor):
            x = torch.tensor(coords, dtype=torch.float32)
        x = to_device(x, dtype=torch.float32)
        for li, (w, b) in enumerate(mats):
            last = (li == len(mats) - 1)
            wt = to_device(torch.tensor(w, dtype=torch.float32))
            bt = to_device(torch.tensor(b, dtype=torch.float32))
            x = x @ wt + bt
            if not last:
                if self.activation == "sin":
                    x = torch.sin(x)
                elif self.activation == "sigmoid":
                    x = torch.sigmoid(x)
                elif self.activation == "gauss":
                    x = torch.exp(-x * x)
                else:
                    x = torch.tanh(x)
        return x

    def phenotype(self, vec: Sequence[float], n_params: int,
                  coords: Optional[Sequence[Sequence[float]]] = None,
                  use_torch: bool = True) -> List[float]:
        """Query the CPPN at `n_params` coordinates -> a phenotype of that size.

        Default coordinates are a deterministic low-discrepancy sweep of the
        unit square plus a radial term, so the same genome always yields the
        same phenotype (reproducibility matters more than aesthetics here).
        """
        n = int(n_params)
        if n <= 0:
            return []
        if coords is None:
            coords = self.default_coords(n)
        mats = self._unpack(vec)

        if use_torch and self.n_in <= 3:
            try:
                out = self._forward_torch(coords, mats)
                flat = out.reshape(-1).tolist() if hasattr(out, "reshape") else list(out)
                return [float(v) for v in flat[:n]]
            except Exception as exc:
                note_failure("cppn:L193", exc) # fall through to pure Python

        vals = []
        for c in coords[:n]:
            vals.append(self._forward_python(list(c), mats)[0])
        return vals

    def default_coords(self, n: int) -> List[List[float]]:
        """Deterministic golden-ratio sweep of [0,1)^2 (+ radius if n_in >= 3)."""
        phi = 0.6180339887498949
        pts = []
        for i in range(n):
            a = (i * phi) % 1.0
            b = (i * phi * phi) % 1.0
            x = 2.0 * a - 1.0
            y = 2.0 * b - 1.0
            if self.n_in >= 3:
                pts.append([x, y, math.sqrt(x * x + y * y)])
            elif self.n_in == 2:
                pts.append([x, y])
            else:
                pts.append([x])
        return pts

    # -- persistence -------------------------------------------------------
    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.describe(), fh, indent=2)
