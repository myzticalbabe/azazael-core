# coding: utf-8
"""
ECOSYSTEM EXPANSION — Category: GENOME (Morph)

Morph removes the genome-size ceiling. Today the search space is a fixed 4-tuple
(`GENE_BOUNDS` in `scripts/evolution_cycle.py`). On the 2026-09-11 batches that
space was provably EXHAUSTED: production sat exactly on the `learning_rate`
ceiling (0.5) and within 0.003 of the `discount` floor (0.8), and the QD
archive's global-best cell held the identical pinned genome. Widening the bounds
moved two genes into new territory on cycle 1, which is what a box-boundary
optimum looks like.

The next ceiling is not the bounds, it is the *arity*. A genome here is a
variable-length typed record, and the operators mutate the SEARCH SPACE rather
than a point in it:

    add_gene      introduce a new tunable dimension
    drop_gene     remove one
    rewire_gene   re-point an existing gene at a different trainer kwarg
    nest          turn a scalar into a 3-point schedule

Two disciplines make growth safe:

1. **Viability filter.** Most structural mutations are lethal. A child is
   rejected unless every gene names a kwarg the trainer actually accepts, no
   name is duplicated, and every value lies inside its own spec. Lethality is
   expected, not an error.

2. **Gene economics.** Every gene pays rent. A gene whose variance collapses
   across the archive is pruned and the freed dimension triggers re-exploration.
   This is why the genome can grow without bloating: unused dimensions are
   reclaimed.

Measuring note (the honest risk in the plan): once the genome can grow,
"fitness 25.04" no longer means the same thing between dimensions. Every genome
therefore records its dimension, and `dimension_report` exists so headline
numbers can be reported per dimension instead of silently compared across them.
"""

from __future__ import annotations

import json
import math
import random
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

__all__ = [
    "GeneSpec",
    "Genome",
    "structural_mutate",
    "viability",
    "prune_zero_variance",
    "dimension_report",
    "TRAINER_KWARGS",
    "NON_GENE_KWARGS",
    "GENE_KWARG_ALIASES",
    "resolve_kwarg",
]  

# The kwargs `RLTrainLoop.train_from_memory` actually accepts. `rewire_gene`
# may only point at one of these; anything else is lethal by construction.
TRAINER_KWARGS = (
    "learning_rate",
    "epsilon",
    "epsilon_decay",
    "discount_factor",
    "n_episodes",
    "max_steps",
    "use_schema_features",
    "use_data_split",
    "val_ratio",
    "test_ratio",
    "by_session",
    "persist",
    "exclude_sources",
)

# Kwargs that must never become genes: they are structural, or they would let
# evolution "win" by doing more Q-updates rather than learning better. This is
# the trap `learning_rl.py:335` already documents ("fake selection pressure").
NON_GENE_KWARGS = ("n_episodes", "max_steps", "persist", "records", "rng")

# A gene's NAME is not always the trainer KWARG it feeds. `GENE_BOUNDS` names the
# gene `discount` while `train_from_memory` accepts `discount_factor`. Without
# this map a genome built straight from the production bounds would be rejected
# as `unknown_kwarg:discount` — and because every structural child inherits it,
# EVERY structural mutation would be lethal. Found by the dual-interpreter smoke
# check, not by inspection.
GENE_KWARG_ALIASES = {
    "discount": "discount_factor",
    "discount_rate": "discount_factor",
    "lr": "learning_rate",
    "decay": "epsilon_decay",
}


def resolve_kwarg(name: str) -> str:
    """Map a gene name onto the trainer kwarg it actually feeds."""
    return GENE_KWARG_ALIASES.get(str(name), str(name))


class GeneSpec:
    """A single typed tunable dimension."""

    __slots__ = ("name", "kind", "lo", "hi", "choices", "kwargs", "points")

    def __init__(self, name: str, kind: str = "float", lo: float = 0.0,
                 hi: float = 1.0, choices: Optional[Sequence[Any]] = None,
                 kwargs: Optional[str] = None, points: int = 3):
        if kind not in ("float", "int", "bool", "choice", "schedule"):
            raise ValueError("unknown gene kind: %r" % (kind,))
        self.name = str(name)
        self.kind = kind
        self.lo = float(lo)
        self.hi = float(hi)
        self.choices = list(choices) if choices else None
        # Normalise at construction so no caller can produce a gene whose kwarg
        # is a production alias the viability filter does not recognise.
        self.kwargs = resolve_kwarg(kwargs) if kwargs else resolve_kwarg(self.name)
        self.points = int(points) if kind == "schedule" else 1

    # -- sampling ---------------------------------------------------------
    def sample(self, rng: random.Random) -> Any:
        k = self.kind
        if k == "float":
            return rng.uniform(self.lo, self.hi)
        if k == "int":
            return rng.randint(int(self.lo), int(self.hi))
        if k == "bool":
            return bool(rng.getrandbits(1))
        if k == "choice":
            return rng.choice(self.choices or [None])
        # schedule: an ordered ride across [lo, hi]
        n = max(2, self.points)
        return [self.lo + (self.hi - self.lo) * (i / (n - 1)) for i in range(n)]

    def clamp(self, value: Any) -> Any:
        k = self.kind
        try:
            if k == "float":
                return min(self.hi, max(self.lo, float(value)))
            if k == "int":
                return int(min(int(self.hi), max(int(self.lo), int(value))))
            if k == "bool":
                return bool(value)
            if k == "choice":
                return value if value in (self.choices or []) else (self.choices or [None])[0]
            if k == "schedule":
                seq = [min(self.hi, max(self.lo, float(v))) for v in value]
                return seq or [self.lo, self.hi]
        except (TypeError, ValueError) as exc:
            note_failure("morph:L151", exc)
            return self.sample(random.Random(0))
        return value

    def contains(self, value: Any) -> bool:
        """Is `value` inside this spec? Used by the viability filter."""
        try:
            if self.kind == "float":
                return self.lo <= float(value) <= self.hi
            if self.kind == "int":
                return int(self.lo) <= int(value) <= int(self.hi)
            if self.kind == "bool":
                return isinstance(value, bool)
            if self.kind == "choice":
                return value in (self.choices or [])
            if self.kind == "schedule":
                return (isinstance(value, (list, tuple)) and len(value) >= 2
                        and all(self.lo <= float(v) <= self.hi for v in value))
        except (TypeError, ValueError) as exc:
            note_failure("morph:L169", exc)
            return False
        return False

    def to_dict(self) -> dict:
        return {"name": self.name, "kind": self.kind, "lo": self.lo, "hi": self.hi,
                "choices": self.choices, "kwargs": self.kwargs, "points": self.points}

    @classmethod
    def from_dict(cls, d: dict) -> "GeneSpec":
        return cls(d.get("name"), d.get("kind", "float"), d.get("lo", 0.0),  # pyright: ignore[reportArgumentType]
                   d.get("hi", 1.0), d.get("choices"), d.get("kwargs"),
                   d.get("points", 3))


class Genome:
    """A variable-length typed record. `dimension` is part of the result."""

    __slots__ = ("specs", "values")

    def __init__(self, specs: Sequence[GeneSpec], values: Sequence[Any]):
        if len(specs) != len(values):
            raise ValueError("specs/values length mismatch")
        self.specs: List[GeneSpec] = list(specs)
        self.values: List[Any] = list(values)

    # -- construction -----------------------------------------------------
    @classmethod
    def random(cls, specs: Sequence[GeneSpec], rng: Optional[random.Random] = None
               ) -> "Genome":
        rng = rng or random.Random()
        return cls(specs, [s.sample(rng) for s in specs])

    @classmethod
    def from_flat(cls, bounds: Dict[str, Tuple[float, float]],
                  values: Dict[str, float]) -> "Genome":
        """Bridge the legacy fixed dict-of-bounds world into Morph."""
        specs, vals = [], []
        for name, (lo, hi) in bounds.items():
            specs.append(GeneSpec(name, "float", lo, hi))
            vals.append(float(values.get(name, lo)))
        return cls(specs, vals)

    # -- views ------------------------------------------------------------
    @property
    def dimension(self) -> int:
        return len(self.specs)

    def names(self) -> List[str]:
        return [s.name for s in self.specs]

    def to_dict(self) -> Dict[str, Any]:
        return {s.name: v for s, v in zip(self.specs, self.values)}

    def spec_dict(self) -> List[dict]:
        return [s.to_dict() for s in self.specs]

    def copy(self) -> "Genome":
        return Genome(list(self.specs), [v for v in self.values])

    def vector(self) -> List[float]:
        """Numeric flattening for arms that require a fixed-width vector."""
        out: List[float] = []
        for s, v in zip(self.specs, self.values):
            if s.kind == "bool":
                out.append(1.0 if v else 0.0)
            elif s.kind == "choice":
                ch = s.choices or [None]
                out.append(float(ch.index(v)) if v in ch else 0.0)
            elif s.kind == "schedule":
                out.extend(float(x) for x in v)
            else:
                try:
                    out.append(float(v))
                except (TypeError, ValueError) as exc:
                    note_failure("morph:L243", exc)
                    out.append(0.0)
        return out

    # -- trainer wiring ---------------------------------------------------
    def to_trainer_kwargs(self, allow: Sequence[str] = TRAINER_KWARGS) -> Dict[str, Any]:
        """Only genes naming an accepted kwarg are emitted.

        `schedule` genes emit their first point as the scalar kwarg and their
        full sequence under `<kwargs>_schedule` (keyed on the TRAINER kwarg, not
        the gene name, because a trainer consumes kwargs), so a downstream
        trainer can opt in without breaking if it ignores the extra key.
        """
        out: Dict[str, Any] = {}
        for s, v in zip(self.specs, self.values):
            if s.kwargs not in allow or s.kwargs in NON_GENE_KWARGS:
                continue
            if s.kind == "schedule":
                seq = list(v)
                out[s.kwargs] = seq[0]
                out["%s_schedule" % s.kwargs] = seq
            else:
                out[s.kwargs] = v
        return out

    def __repr__(self) -> str:
        return "Genome(dim=%d, %s)" % (self.dimension, self.to_dict())


# --------------------------------------------------------------------------
# Structural mutation operators: these change the SPACE, not the point.
# --------------------------------------------------------------------------

def _op_add_gene(g: Genome, rng: random.Random, pool: Sequence[GeneSpec]) -> Optional[Genome]:
    taken = set(g.names())
    cand = [s for s in pool if s.name not in taken]
    if not cand:
        return None
    spec = rng.choice(cand)
    child = g.copy()
    child.specs.append(spec)
    child.values.append(spec.sample(rng))
    return child


def _op_drop_gene(g: Genome, rng: random.Random, pool: Sequence[GeneSpec]) -> Optional[Genome]:
    if g.dimension <= 1:
        return None
    i = rng.randrange(g.dimension)
    child = g.copy()
    del child.specs[i]
    del child.values[i]
    return child


def _op_rewire_gene(g: Genome, rng: random.Random, pool: Sequence[GeneSpec]) -> Optional[Genome]:
    """Re-point a gene at a different trainer kwarg (spec is rebuilt)."""
    i = rng.randrange(g.dimension)
    spec = g.specs[i]
    taken = {s.kwargs for s in g.specs}
    open_kw = [k for k in TRAINER_KWARGS if k not in taken and k not in NON_GENE_KWARGS]
    if not open_kw:
        return None
    child = g.copy()
    new = GeneSpec(spec.name, spec.kind, spec.lo, spec.hi, spec.choices,
                   rng.choice(open_kw), spec.points)
    child.specs[i] = new
    child.values[i] = new.clamp(child.values[i])
    return child


def _op_nest(g: Genome, rng: random.Random, pool: Sequence[GeneSpec]) -> Optional[Genome]:
    """Turn a scalar gene into a 3-point schedule over the same range."""
    idx = [i for i, s in enumerate(g.specs) if s.kind == "float" and s.kwargs not in NON_GENE_KWARGS]
    if not idx:
        return None
    i = rng.choice(idx)
    s = g.specs[i]
    child = g.copy()
    child.specs[i] = GeneSpec(s.name, "schedule", s.lo, s.hi, None, s.kwargs, 3)
    child.values[i] = [s.lo + (s.hi - s.lo) * k / 2.0 for k in range(3)]
    return child


STRUCTURAL_OPS = {
    "add_gene": _op_add_gene,
    "drop_gene": _op_drop_gene,
    "rewire_gene": _op_rewire_gene,
    "nest": _op_nest,
}


def structural_mutate(g: Genome, pool: Sequence[GeneSpec],
                      rng: Optional[random.Random] = None,
                      attempts: int = 4) -> Tuple[Genome, str]:
    """Apply one structural operator, retrying past lethal (None) results.

    Returns `(child, op_name)`. If every attempt is lethal the parent is
    returned unchanged with op `"none"` — structural mutation is expected to
    fail often and must never raise into the outer loop.
    """
    rng = rng or random.Random()
    ops = list(STRUCTURAL_OPS)
    for _ in range(max(1, attempts)):
        op = rng.choice(ops)
        try:
            child = STRUCTURAL_OPS[op](g, rng, pool)
        except Exception as exc:
            note_failure("morph:L350", exc)
            child = None
        if child is not None:
            return child, op
    return g, "none"


# --------------------------------------------------------------------------
# Viability: most structural mutations are lethal, and that is expected.
# --------------------------------------------------------------------------

def viability(g: Genome, allow: Sequence[str] = TRAINER_KWARGS) -> Tuple[bool, str]:
    """Return `(ok, reason)`. Reason is empty on success."""
    names = g.names()
    if not names:
        return False, "empty"
    if len(set(names)) != len(names):
        return False, "duplicate_gene_name"
    for s, v in zip(g.specs, g.values):
        if s.kwargs not in allow:
            return False, "unknown_kwarg:%s" % s.kwargs
        if s.kwargs in NON_GENE_KWARGS:
            return False, "non_gene_kwarg:%s" % s.kwargs
        # Finiteness is checked BEFORE the range test on purpose: `nan` fails
        # every comparison, so a range-first order reports the uninformative
        # "out_of_range" for what is actually a non-finite value.
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return False, "non_finite:%s" % s.name
        if isinstance(v, (list, tuple)):
            for x in v:
                if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
                    return False, "non_finite:%s" % s.name
        if not s.contains(v):
            return False, "out_of_range:%s" % s.name
    return True, ""


# --------------------------------------------------------------------------
# Gene economics: every gene must pay rent.
# --------------------------------------------------------------------------

def prune_zero_variance(genomes: Sequence[Genome], min_spread: float = 1e-4
                        ) -> Tuple[List[str], List[str]]:
    """Find genes whose value never varies across the archive.

    Returns `(prune, keep)` gene names. A gene collapsed to a single value
    contributes no search dimension and is reclaimed by the caller; the freed
    slot is what triggers re-exploration. Genes missing from some genomes are
    treated as varying (they are still being explored).
    """
    if not genomes:
        return [], []
    n = len(genomes)
    seen: Dict[str, int] = {}
    buckets: Dict[str, List[float]] = {}

    for g in genomes:
        for s, v in zip(g.specs, g.values):
            seen[s.name] = seen.get(s.name, 0) + 1
            try:
                num = float(v[0]) if isinstance(v, (list, tuple)) and v else float(v)  # pyright: ignore[reportArgumentType]
            except (TypeError, ValueError) as exc:
                note_failure("morph:L411", exc)
                num = float(bool(v))  # pyright: ignore[reportArgumentType]
            buckets.setdefault(s.name, []).append(num)

    prune, keep = [], []
    for name, vals in buckets.items():
        if seen.get(name, 0) < n or len(vals) < 2:
            keep.append(name)
            continue
        spread = max(vals) - min(vals)
        (prune if spread <= min_spread else keep).append(name)
    return sorted(prune), sorted(keep)


def dimension_report(rows: Iterable[dict]) -> dict:
    """Group results by genome dimension — the discipline the plan demands.

    `rows` are artifact dicts carrying `dimension` and `fitness`. Headline
    fitness must never be compared across dimensions without this grouping.
    """
    by_dim: Dict[int, List[float]] = {}
    for r in rows:
        # An explicit dimension is required. Defaulting a missing one to 0 would
        # silently merge legacy rows into a bucket labelled "dimension 0", which
        # is exactly the cross-dimensional comparison this function exists to
        # prevent.
        if not isinstance(r, dict) or "dimension" not in r:
            continue
        try:
            d = int(r.get("dimension") or 0)
            f = float(r.get("fitness") or 0.0)
        except (TypeError, ValueError) as exc:
            note_failure("morph:L442", exc)
            continue
        if math.isnan(f) or math.isinf(f):
            continue
        by_dim.setdefault(d, []).append(f)

    out: Dict[str, Any] = {"by_dimension": {}}
    best_overall: Optional[float] = None
    for d in sorted(by_dim):
        fs = by_dim[d]
        mean = sum(fs) / len(fs)
        var = sum((x - mean) ** 2 for x in fs) / len(fs)
        entry = {"n": len(fs), "best": max(fs), "mean": mean,
                 "sd": math.sqrt(var), "worst": min(fs)}
        out["by_dimension"][str(d)] = entry
        if best_overall is None or entry["best"] > best_overall:
            best_overall = entry["best"]
            out["best_dimension"] = d
    out["best_fitness"] = best_overall
    return out


def seed_pool_from_bounds(bounds: Dict[str, Tuple[float, float]]) -> List[GeneSpec]:
    """The candidate gene pool: today's bounds plus genuinely new dimensions.

    Only switch-like, genuinely-free kwargs are offered as additions.
    `n_episodes`/`max_steps` are deliberately withheld (fake selection
    pressure, per `learning_rl.py:335`).
    """
    pool = [GeneSpec(name, "float", lo, hi) for name, (lo, hi) in bounds.items()]
    pool.append(GeneSpec("use_schema_features", "bool", 0, 1))
    pool.append(GeneSpec("use_data_split", "bool", 0, 1))
    pool.append(GeneSpec("by_session", "bool", 0, 1))
    pool.append(GeneSpec("val_ratio", "float", 0.05, 0.35))
    pool.append(GeneSpec("test_ratio", "float", 0.05, 0.35))
    return pool


def save_genome(path: str, g: Genome, extra: Optional[dict] = None) -> None:
    payload = {"specs": g.spec_dict(), "values": g.values, "dimension": g.dimension}
    if extra:
        payload.update(extra)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, default=str)


def load_genome(path: str) -> Optional[Genome]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        specs = [GeneSpec.from_dict(x) for x in d["specs"]]
        return Genome(specs, d["values"])
    except Exception as exc:
        note_failure("morph:L494", exc)
        return None
