"""MCML -- MellifluousML: lego-style native learning blocks.

Small composable primitives (Dense, activations, losses, SGD) in stdlib
Python that assemble -- Lego-style -- into perfectly-formed models. No
numpy, no torch, no sklearn: the old paradigm (heavy deps, opaque bytes)
is bypassed completely. What the blocks produce, the Well can verify and
the foundry can ship:

- deterministic: every weight comes from one seeded stream, so a model
  is bit-reproducible from (seed, architecture) alone;
- perfectly-formed: Sequential refuses dim mismatches at assembly with
  both sides named -- a malformed model cannot be built, only reported;
- golden-anchored: digest() is sha256 over packed f32 params, the same
  identity scheme as well.golden_digest;
- Aether-verified: mcml_proof() shapes a training result for
  well.aether_verify, so the real auditors grade it.

Pure Python lists. Slow by design, exact by construction.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import struct
from typing import Callable, Dict, List, Sequence

Vec = List[float]
Mat = List[Vec]


def _xavier(rng: random.Random, fan_in: int, fan_out: int) -> float:
    bound = math.sqrt(6.0 / (fan_in + fan_out))
    return rng.uniform(-bound, bound)


class Dense:
    """Fully-connected block. Owns W (out x in), b (out), and its grads."""

    def __init__(self, in_dim: int, out_dim: int, seed: int = 7) -> None:
        if in_dim < 1 or out_dim < 1:
            raise ValueError("dense dims must be >= 1, got %d -> %d"
                             % (in_dim, out_dim))
        rng = random.Random(seed)
        self.in_dim = in_dim
        self.out_dim = out_dim
        self.seed = seed
        self.W: Mat = [[_xavier(rng, in_dim, out_dim) for _ in range(in_dim)]
                       for _ in range(out_dim)]
        self.b: Vec = [_xavier(rng, in_dim, out_dim) for _ in range(out_dim)]
        self.gW: Mat = [[0.0] * in_dim for _ in range(out_dim)]
        self.gb: Vec = [0.0] * out_dim

    def forward(self, x: Vec) -> Vec:
        if len(x) != self.in_dim:
            raise ValueError("dense %d->%d got input len %d"
                             % (self.in_dim, self.out_dim, len(x)))
        return [sum(w * v for w, v in zip(row, x)) + b
                for row, b in zip(self.W, self.b)]

    def backward(self, x: Vec, grad_y: Vec) -> Vec:
        # Accumulate into gW/gb (zero_grad starts the batch): the loop in
        # Sequential.backward over samples is a true batch gradient.
        for o, gy in enumerate(grad_y):
            for i, xv in enumerate(x):
                self.gW[o][i] += gy * xv
            self.gb[o] += gy
        return [sum(self.W[o][i] * grad_y[o] for o in range(self.out_dim))
                for i in range(self.in_dim)]

    def params(self) -> Vec:
        return [v for row in self.W for v in row] + list(self.b)

    def grads(self) -> Vec:
        return [v for row in self.gW for v in row] + list(self.gb)


# -- activations: (name, f, df) triples; df takes the ACTIVATED value --
def _relu(x: float) -> float:
    return x if x > 0.0 else 0.0


def _d_relu(y: float) -> float:
    return 1.0 if y > 0.0 else 0.0


def _tanh(x: float) -> float:
    return math.tanh(x)


def _d_tanh(y: float) -> float:
    return 1.0 - y * y


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x)) if x >= 0 else (
        math.exp(x) / (1.0 + math.exp(x)))


def _d_sigmoid(y: float) -> float:
    return y * (1.0 - y)


ACTIVATIONS: Dict[str, tuple] = {
    "relu": (_relu, _d_relu),
    "tanh": (_tanh, _d_tanh),
    "sigmoid": (_sigmoid, _d_sigmoid),
}


class Activate:
    """Elementwise nonlinearity block."""

    def __init__(self, kind: str = "tanh") -> None:
        if kind not in ACTIVATIONS:
            raise ValueError("activations: %s (got %r)"
                             % (sorted(ACTIVATIONS), kind))
        self.kind = kind
        self.f, self.df = ACTIVATIONS[kind]

    def forward(self, x: Vec) -> Vec:
        return [self.f(v) for v in x]

    def backward(self, y: Vec, grad_y: Vec) -> Vec:
        return [dy * self.df(v) for v, dy in zip(y, grad_y)]

    def params(self) -> Vec:
        return []

    def grads(self) -> Vec:
        return []


# -- losses (stable, combined where it matters) --
def mse(pred: Vec, target: Vec) -> tuple:
    n = len(pred)
    diff = [p - t for p, t in zip(pred, target)]
    return sum(d * d for d in diff) / n, [2.0 * d / n for d in diff]


def xent_logits(logits: Vec, target_idx: int) -> tuple:
    m = max(logits)
    exps = [math.exp(z - m) for z in logits]
    s = sum(exps)
    probs = [e / s for e in exps]
    grad = list(probs)
    grad[target_idx] -= 1.0
    return -math.log(max(probs[target_idx], 1e-300)), grad


class Sequential:
    """Lego assembly. Refuses malformed stacks at build time."""

    def __init__(self, blocks: Sequence) -> None:
        blocks = list(blocks)
        if not blocks:
            raise ValueError("sequential needs >= 1 block")
        for a, b in zip(
                [d for b in blocks if isinstance(b, Dense)
                 for d in [b.out_dim]][:-1],
                [b.in_dim for b in blocks if isinstance(b, Dense)][1:]):
            if a != b:
                raise ValueError("dim mismatch at assembly: %d != %d" % (a, b))
        self.blocks = blocks
        self.in_dim = next(b.in_dim for b in blocks if isinstance(b, Dense))
        self.out_dim = next(b.out_dim for b in reversed(blocks)
                            if isinstance(b, Dense))

    def forward(self, x: Vec) -> Vec:
        cache = [list(x)]
        for b in self.blocks:
            if isinstance(b, Dense):
                cache.append(b.forward(cache[-1]))
            else:
                cache.append(b.forward(cache[-1]))
        self._cache = cache
        return cache[-1]

    def backward(self, grad: Vec) -> None:
        g = list(grad)
        for b, inp, out in zip(reversed(self.blocks),
                               reversed(self._cache[:-1]),
                               reversed(self._cache[1:])):
            if isinstance(b, Dense):
                g = b.backward(inp, g)
            else:
                g = b.backward(out, g)

    def zero_grad(self) -> None:
        for b in self.blocks:
            if isinstance(b, Dense):
                b.gW = [[0.0] * b.in_dim for _ in range(b.out_dim)]
                b.gb = [0.0] * b.out_dim

    def params(self) -> Vec:
        return [v for b in self.blocks for v in b.params()]

    def step(self, lr: float) -> None:
        for b in self.blocks:
            if isinstance(b, Dense):
                b.W = [[w - lr * gw for w, gw in zip(row, grow)]
                       for row, grow in zip(b.W, b.gW)]
                b.b = [x - lr * gb for x, gb in zip(b.b, b.gb)]


def digest(model: Sequential) -> str:
    """sha256 over packed f32 params -- the model's golden identity."""
    vals = model.params()
    return hashlib.sha256(struct.pack("<%df" % len(vals), *vals)).hexdigest()


def replicate(model_fn: Callable[[], Sequential]) -> Dict:
    """Rebuild from (seed, architecture) and prove bit-identity."""
    a, b = model_fn(), model_fn()
    da, db = digest(a), digest(b)
    return {"match": da == db, "digest": da, "n_params": len(a.params())}


def describe(model: Sequential) -> Dict:
    """Architecture record: everything needed to rebuild, nothing learned."""
    spec = []
    for b in model.blocks:
        if isinstance(b, Dense):
            spec.append({"dense": [b.in_dim, b.out_dim], "seed": b.seed})
        else:
            spec.append({"activate": b.kind})
    return {"in_dim": model.in_dim, "out_dim": model.out_dim,
            "blocks": spec}


def rebuild(spec: Dict) -> Sequential:
    blocks = []
    for s in spec["blocks"]:
        if "dense" in s:
            blocks.append(Dense(s["dense"][0], s["dense"][1],
                                seed=s["seed"]))
        else:
            blocks.append(Activate(s["activate"]))
    return Sequential(blocks)


def ship(model: Sequential, loss: float, path: str) -> Dict:
    """Write the shippable artifact: architecture + learned params + digest."""
    artifact = {"spec": describe(model), "params": model.params(),
                "loss": loss, "digest": digest(model)}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(artifact, f)
    return artifact


def load_ship(path: str) -> Sequential:
    """Rebuild from a shipped artifact; refuse tampered shipments by name."""
    with open(path, encoding="utf-8") as f:
        artifact = json.load(f)
    model = rebuild(artifact["spec"])
    flat = list(artifact["params"])
    for b in model.blocks:
        if isinstance(b, Dense):
            n = b.in_dim * b.out_dim
            b.W = [flat[i * b.in_dim:(i + 1) * b.in_dim]
                   for i in range(b.out_dim)]
            flat = flat[n:]
            b.b = flat[:b.out_dim]
            flat = flat[b.out_dim:]
    if digest(model) != artifact["digest"]:
        raise ValueError("shipment refused: digest mismatch (have %s)"
                         % digest(model)[:12])
    return model


def mcml_proof(model: Sequential, loss: float, bound: float) -> Dict:
    """Shape a training result for well.aether_verify (the real auditors)."""
    return {"within_bound": loss <= bound, "n": len(model.params()),
            "loss": round(loss, 6), "bound": bound,
            "digest": digest(model)[:12]}
