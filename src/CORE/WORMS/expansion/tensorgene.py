# coding: utf-8
"""Tensor genome — genes that carry their own evidence of usefulness.

The deliverable this exists for
------------------------------
Everything upstream searches a genome that cannot say what it is worth. A gene's
value is known; its *contribution* is not. That is why `morph_trainer_kwargs`
had to exist, why "fitness up" could be bought with description length, and why
the Phase E verdict needed a dedicated per-dimension ledger.

A tensor genome makes each gene a typed tensor that also carries a measured
**usefulness** — the strength and direction of its association with fitness
across the recorded history. The genome then compiles: genes below the
usefulness floor are pruned, and what remains declares the input layer of a
model that could be trained to predict fitness from genes.

Four rules this module enforces, each one paid for elsewhere in this codebase
-----------------------------------------------------------------------------
1. **A gene that is not consumed is worse than no gene.** `TensorGenome.prune`
   exists so an unconsumed coordinate is removable rather than silently inert —
   the exact trap `_eval_salt` set by reading only four keys.

2. **Fitness is only comparable within a dimension.** `usefulness` is computed
   per gene, and genes absent from a row are not imputed; they are skipped. The
   alternative silently treats "not present" as a value.

3. **Re-measurement is not evidence.** The history contains rows from frozen
   pools with bit-identical fitness and genes. Spearman over raw rows would
   treat one measurement as many. `correlate` dedupes on
   (gene vector, fitness) FIRST — the same correction applied to Cranium and to
   the SPRT, because the same pool-freeze produces it.

4. **Correlation is not causation, and this module says so.** `usefulness` is an
   association statistic. It is labelled `direction` separately from `strength`
   precisely so nothing downstream can read it as an effect size.
"""
from __future__ import annotations

import json
import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
import os

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

__all__ = [
    "TensorGene",
    "TensorGenome",
    "spearman",
    "correlate",
    "dedupe_observations",
    "row_payload",
    "USEFULNESS_FLOOR",
]

# Below this |rho| a gene is not carried into a compiled model. Deliberately a
# hard floor rather than a p-value: with repeated frozen-pool rows the effective
# sample size is unknowable, so a significance test would be theatre.
USEFULNESS_FLOOR = 0.15

# Two rows count as the same observation when their gene vector AND fitness
# agree to this tolerance. Matches the 1e-9 used by _sprt_collapse.
DEDUPE_TOL = 1e-9


def row_payload(row: Any) -> Optional[dict]:
    """Normalise a memory-store row into its payload dict.

    The store wraps every payload under a `data` key, so reading
    `row["best_genes"]` directly finds nothing and yields an EMPTY genome.
    That is the silent-inert failure this module exists to prevent, so the
    unwrap lives in exactly one place and the count of unusable rows is
    reported rather than swallowed. Mirrors `proteus_hooks.row_payload`.
    """
    if not isinstance(row, dict):
        return None
    data = row.get("data", row)
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception as exc:
            note_failure("tensorgene:L78", exc)
            return None
    return data if isinstance(data, dict) else None


def _gene_map(data: dict) -> Dict[str, Any]:
    """The gene mapping from a payload, tolerating the older key name."""
    genes = data.get("best_genes") or data.get("champion_genes") or {}
    return genes if isinstance(genes, dict) else {}


def boruta_relevance(history: Sequence[dict], fitness_key: str = "best_total",
                     min_rows: int = 20, max_iter: int = 30,
                     random_state: int = 7) -> Dict[str, Any]:
    """All-relevant gene ranking via Boruta (shadow-feature bake-off).

    Complements per-gene Spearman `usefulness`: correlation ranks genes one
    at a time and can crown a passenger that rides a driver's signal, while
    Boruta keeps EVERY gene that beats its own shuffled shadow — the right
    question for "which genes actually matter" on a small genome.

    Uses the same row conventions as `correlate` (payload unwrap, skip rows
    missing genes, dedupe identical vectors). Only genes present in >= half
    the usable rows enter the matrix (a gene trialled twice cannot beat
    noise honestly). Fail-open: Boruta missing, too few rows, or any error
    returns ok=False with a reason — never raises into the refresh cycle.
    """
    try:
        rows = []
        for row in history or ():
            data = row_payload(row)
            if data is None:
                continue
            genes = _gene_map(data)
            if not genes:
                continue
            try:
                fitness_raw = data.get(fitness_key)
                if fitness_raw is None:
                    raise TypeError(f"missing {fitness_key}")
                fv = float(fitness_raw)
            except (TypeError, ValueError) as exc:
                note_failure("tensorgene:L116", exc)
                continue
            rows.append((genes, fv))
        if len(rows) < min_rows:
            return {"ok": False,
                    "reason": f"only {len(rows)} usable rows (< {min_rows})"}
        from collections import Counter
        present = Counter()
        for genes, _ in rows:
            present.update(k for k, v in genes.items()
                           if isinstance(v, (int, float)))
        keep = sorted(k for k, n in present.items() if n >= len(rows) // 2)
        if len(keep) < 2:
            return {"ok": False,
                    "reason": "fewer than 2 genes with majority coverage"}
        import numpy as np
        Xd_raw, yd_raw = [], []
        for genes, fv in rows:
            if all(isinstance(genes.get(k), (int, float)) for k in keep):
                Xd_raw.append([float(genes[k]) for k in keep])
                yd_raw.append(float(fv))
        if len(Xd_raw) < min_rows:
            return {"ok": False,
                    "reason": f"only {len(Xd_raw)} complete rows (< {min_rows})"}
        # Dedupe identical vectors (frozen-pool repeats are one measurement).
        seen, Xd, yd = set(), [], []
        for vec, fv in zip(Xd_raw, yd_raw):
            key = (tuple(round(v, 9) for v in vec), round(fv, 9))
            if key not in seen:
                seen.add(key)
                Xd.append(vec)
                yd.append(fv)
        if len(Xd) < min_rows:
            return {"ok": False,
                    "reason": f"only {len(Xd)} distinct rows (< {min_rows})"}
        from sklearn.ensemble import RandomForestRegressor
        from boruta import BorutaPy
        #: BorutaPy takes n_estimators='auto' (auto-tuned forest size; its
        #: own docs say "int or string" and its example uses 'auto'), but
        #: the checker infers int from the library's default of 1000.
        boruta_estimators: Any = "auto"
        est = RandomForestRegressor(n_estimators=100, max_depth=5,
                                    random_state=random_state, n_jobs=1)
        selector = BorutaPy(est, n_estimators=boruta_estimators, max_iter=max_iter,
                            random_state=random_state, verbose=0)
        selector.fit(np.array(Xd), np.array(yd))
        verdict = {}
        for k, rank, support, weak in zip(
                keep, selector.ranking_, selector.support_,
                selector.support_weak_):
            verdict[k] = ("confirmed" if support
                          else "tentative" if weak else "rejected")
        return {"ok": True, "genes": verdict, "n_rows": len(Xd),
                "n_estimators": int(getattr(selector, "n_estimators_", 0) or 0),
                "max_iter": int(max_iter)}
    except ImportError as exc:
        note_failure("tensorgene:L167", exc)
        return {"ok": False, "reason": f"missing dependency: {exc}"}
    except Exception as exc:
        note_failure("tensorgene:L169", exc)
        return {"ok": False, "reason": str(exc)[:160]}


def _ranks(xs: Sequence[float]) -> List[float]:
    """Average ranks (1-based), ties sharing the mean rank."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        mean_rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            out[order[k]] = mean_rank
        i = j + 1
    return out


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx <= 0.0 or dy <= 0.0:
        # A constant column carries no information. Returning 0.0 here would
        # claim "measured, no effect"; None says "not identifiable", which is
        # what a constant actually is.
        return None
    return num / (dx * dy)


def spearman(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    """Spearman rho. None when either side is constant or n < 3."""
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    return _pearson(_ranks(xs), _ranks(ys))


def dedupe_observations(obs: Iterable[tuple]) -> List[tuple]:
    """Drop rows that repeat an earlier (gene vector, fitness) exactly.

    `obs` is an iterable of `(gene_vector_tuple, fitness)`. Order is preserved
    so the caller keeps chronology, but only the FIRST occurrence is kept.

    Why: the pool snapshot is reused for up to 1800s, so consecutive cycles
    record bit-identical genes and fitness. Twenty of those rows are one
    measurement. Correlating over the raw set inflates every statistic, which is
    the same defect already fixed in `cranium_decide` and `_sprt_collapse`.
    """
    seen = set()
    out = []
    for vec, fit in obs:
        try:
            key = (tuple(round(float(v), 9) for v in vec), round(float(fit), 9))
        except (TypeError, ValueError) as exc:
            note_failure("tensorgene:L228", exc)
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append((vec, fit))
    return out


def correlate(history: Sequence[dict], gene: str,
              fitness_key: str = "best_total") -> Dict[str, Any]:
    """Association between one gene's value and fitness, over real history rows.

    `history` is a sequence of payload dicts each carrying a genes mapping and a
    numeric fitness. Rows lacking the gene are SKIPPED, never imputed: a gene
    absent from a row was not searched in that row, so it has no observation to
    contribute.
    """
    obs: List[tuple] = []
    seen_vecs = set()
    no_payload = 0
    missing_gene = 0
    for row in history or ():
        data = row_payload(row)
        if data is None:
            no_payload += 1
            continue
        genes = _gene_map(data)
        if gene not in genes:
            missing_gene += 1
            continue
        try:
            fitness_raw = data.get(fitness_key)
            if fitness_raw is None:
                raise TypeError(f"missing {fitness_key}")
            fv = float(fitness_raw)
        except (TypeError, ValueError) as exc:
            note_failure("tensorgene:L261", exc)
            continue
        vec = []
        for k in sorted(genes):
            try:
                vec.append(float(genes[k]))
            except (TypeError, ValueError) as exc:
                note_failure("tensorgene:L267", exc)
                vec.append(0.0)
        # Dedupe on the full gene vector + fitness: identical rows are the same
        # measurement, and the frozen pool makes them common.
        key = (tuple(round(v, 9) for v in vec), round(fv, 9))
        if key in seen_vecs:
            continue
        seen_vecs.add(key)
        try:
            obs.append((float(genes[gene]), fv))
        except (TypeError, ValueError) as exc:
            note_failure("tensorgene:L277", exc)
            continue

    diag = {"rows_without_payload": no_payload, "rows_missing_gene": missing_gene}
    if len(obs) < 3:
        return dict({"gene": gene, "usefulness": 0.0, "samples": len(obs),
                     "direction": "unknown", "identifiable": False,
                     "note": "fewer than 3 distinct observations"}, **diag)
    xs = [o[0] for o in obs]
    ys = [o[1] for o in obs]
    rho = spearman(xs, ys)
    if rho is None:
        return dict({"gene": gene, "usefulness": 0.0, "samples": len(obs),
                     "direction": "unknown", "identifiable": False,
                     "note": "gene value constant across observations"}, **diag)
    direction = "flat"
    if rho > DEDUPE_TOL:
        direction = "up"
    elif rho < -DEDUPE_TOL:
        direction = "down"
    return dict({"gene": gene, "usefulness": abs(float(rho)),
                 "rho": round(float(rho), 4), "samples": len(obs),
                 "direction": direction, "identifiable": True}, **diag)


class TensorGene:
    """One gene as a typed value plus the evidence for carrying it."""

    __slots__ = ("name", "kind", "value", "usefulness", "direction", "samples",
                 "bounds", "provenance", "identifiable", "rho")

    def __init__(self, name: str, kind: str, value: Any,
                 usefulness: float = 0.0, direction: str = "unknown",
                 samples: int = 0, bounds: Optional[Tuple[float, float]] = None,
                 provenance: str = "", identifiable: bool = False,
                 rho: Optional[float] = None):
        self.name = str(name)
        self.kind = str(kind)
        self.value = value
        # `usefulness` is |rho|: magnitude only. The SIGNED coefficient is kept
        # separately so the artifact states the direction directly instead of
        # forcing a reader to reconstruct it from `direction`. Both are
        # association statistics; neither is an effect size.
        self.usefulness = float(usefulness)
        self.rho = (float(rho) if rho is not None
                    else (self.usefulness if direction == "up"
                          else (-self.usefulness if direction == "down" else 0.0)))
        self.direction = str(direction)
        self.samples = int(samples)
        self.bounds = bounds
        self.provenance = str(provenance)
        self.identifiable = bool(identifiable)

    def to_dict(self) -> dict:
        return {"name": self.name, "kind": self.kind, "value": self.value,
                "usefulness": round(self.usefulness, 4),
                "rho": round(self.rho, 4),
                "direction": self.direction, "samples": self.samples,
                "bounds": list(self.bounds) if self.bounds else None,
                "provenance": self.provenance,
                "identifiable": self.identifiable}

    @classmethod
    def from_dict(cls, d: dict) -> "TensorGene":
        b = d.get("bounds")
        return cls(d.get("name", ""), d.get("kind", "float"), d.get("value"),
                   d.get("usefulness", 0.0), d.get("direction", "unknown"),
                   d.get("samples", 0), tuple(b) if b else None,
                   d.get("provenance", ""), d.get("identifiable", False),
                   d.get("rho"))

    def as_float(self) -> float:
        """Numeric encoding of the value, for the compiled vector."""
        v = self.value
        if isinstance(v, bool):
            return 1.0 if v else 0.0
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            # Deterministic string encoding: stable across runs, unlike hash().
            return float(sum((i + 1) * ord(c) for i, c in enumerate(v)) % 997)
        return 0.0

    def to_tensor_row(self) -> List[float]:
        """[value, usefulness, direction_sign, identifiable] for a tensor."""
        sign = 1.0 if self.direction == "up" else (-1.0 if self.direction == "down" else 0.0)
        return [self.as_float(), self.usefulness, sign, 1.0 if self.identifiable else 0.0]


class TensorGenome:
    """An ordered, self-describing genome that can be compiled.

    `chromosomes` are grouped by the organ that contributed them, which is what
    makes "declare your usefulness" legible: a gene's provenance is the part of
    the system that argues for it.
    """

    WIDTH_PER_GENE = 4     # see TensorGene.to_tensor_row

    def __init__(self, genes: Optional[Iterable[TensorGene]] = None,
                 meta: Optional[dict] = None):
        self.genes: Dict[str, TensorGene] = {}
        self.meta: Dict[str, Any] = dict(meta or {})
        for g in genes or ():
            self.add(g)

    # -- construction ------------------------------------------------------
    def add(self, gene: TensorGene) -> "TensorGenome":
        self.genes[gene.name] = gene
        return self

    def declare(self, history: Sequence[dict], kind_map: Optional[dict] = None,
                bounds_map: Optional[dict] = None,
                provenance_map: Optional[dict] = None,
                value_map: Optional[dict] = None) -> "TensorGenome":
        """Attach measured usefulness to every named gene.

        `value_map` supplies the current champion's values; when absent the most
        recent history row is used, so the genome describes the live champion
        rather than a stale one.
        """
        latest: Dict[str, Any] = {}
        n_rows = 0
        for row in history or ():
            n_rows += 1
            data = row_payload(row)
            if data is None:
                continue
            g = _gene_map(data)
            if g:
                latest = g
                break
        # Meta records WHY the genome may be empty. An empty genome that does
        # not say "history had no payloads" is indistinguishable from a bug.
        self.meta["history_rows"] = n_rows
        self.meta["payload_genes_found"] = bool(latest)
        # Production caller for boruta_relevance: the per-gene Spearman
        # `usefulness` above ranks one gene at a time; the all-relevant
        # shadow bake-off rides alongside in meta (additive, fail-open —
        # thin history returns ok=False inside the callee, never raises).
        try:
            self.meta["boruta"] = boruta_relevance(history)
        except Exception as exc:
            note_failure("tensorgene:L432", exc)
            self.meta["boruta"] = {"ok": False, "reason": "declare-hook"}
        values = dict(value_map if value_map is not None else latest)
        for name in sorted(values.keys()):
            stats = correlate(history, name)
            self.add(TensorGene(
                name=name,
                kind=(kind_map or {}).get(name, "float"),
                value=values[name],
                usefulness=stats["usefulness"],
                direction=stats["direction"],
                samples=stats["samples"],
                bounds=(bounds_map or {}).get(name),
                provenance=(provenance_map or {}).get(name, "unknown"),
                identifiable=stats.get("identifiable", False),
                rho=stats.get("rho")))
        return self

    # -- compiled view ----------------------------------------------------
    def order(self) -> List[str]:
        """Deterministic gene order: descending usefulness, then name.

        Sorted by usefulness rather than declaration order so the compiled
        vector's leading coordinates are the ones with evidence. A model that
        truncates its input at a width then keeps the most useful genes.
        """
        return [g.name for g in sorted(
            self.genes.values(), key=lambda g: (-g.usefulness, g.name))]

    def prune(self, floor: float = USEFULNESS_FLOOR,
              keep: Optional[Sequence[str]] = None) -> List[str]:
        """Remove genes whose usefulness is undeclared. Returns their names.

        `keep` protects named genes from pruning (structural parameters that are
        not free to vary, e.g. a bound the trainer requires).
        """
        protected = set(keep or ())
        dropped = []
        for name in self.order():
            g = self.genes[name]
            if name in protected:
                continue
            # Unidentifiable is NOT the same as useless: it means the data could
            # not answer. Those are dropped too, but they are reported, so the
            # reason is visible rather than averaged into the same number.
            if g.usefulness < float(floor):
                dropped.append(name)
                del self.genes[name]
        return dropped

    def prune_floor_names(self, floor: float = USEFULNESS_FLOOR,
                          keep: Optional[Sequence[str]] = None) -> List[str]:
        """Names that WOULD be pruned, without removing them.

        Exists so a spec can report the candidates while the genome stays
        searchable. `prune` is the destructive version; this is the query a
        caller uses when it wants to record "this gene is on probation".
        """
        protected = set(keep or ())
        return [n for n in self.order()
                if n not in protected and self.genes[n].usefulness < float(floor)]

    def to_tensor(self) -> List[List[float]]:
        """The compiled matrix: one row per retained gene."""
        return [self.genes[n].to_tensor_row() for n in self.order()]

    @property
    def width(self) -> int:
        return len(self.genes) * self.WIDTH_PER_GENE

    def compile_spec(self) -> Dict[str, Any]:
        """Declare the model this genome compiles to.

        This is a SPEC, not a trained model, and it is labelled as such. What it
        fixes is the shape: which genes are inputs, in what order, with what
        declared usefulness, and what the target is. Training is a separate,
        GIL-side step — the same interpreter split the Oracle already needed,
        because torch cannot load under python3.14t.
        """
        kept = [self.genes[n].to_dict() for n in self.order()]
        return {
            "kind": "tensorgene.mlp.spec",
            "version": 1,
            "input": {
                "genes": [g["name"] for g in kept],
                "rows": len(kept),
                "features_per_gene": self.WIDTH_PER_GENE,
                "width": self.width,
                "feature_order": ["value", "usefulness",
                                  "direction_sign", "identifiable"],
            },
            "hidden": [max(8, self.width * 2), max(4, self.width)],
            "output": {"name": "best_total", "activation": "linear"},
            "loss": "huber",
            # Candidates still present in the genome whose usefulness is below
            # the floor. Named `below_floor` rather than `pruned` because these
            # genes are STILL HERE — calling them pruned would report an action
            # that has not happened. `prune()` performs the removal.
            "below_floor": self.prune_floor_names(),
            "trained": False,
            "note": ("Shape declaration only. Training requires a torch-capable "
                     "interpreter (the 3.15t runner carries torch; the 3.15 GIL "
                     "sidecar is the fallback) and an XGBoost-free CPU budget; "
                     "see TENSOR_GENOME_PLAN.md."),
            "genes": kept,
        }

    # -- persistence ------------------------------------------------------
    def to_dict(self) -> dict:
        return {"genes": [self.genes[n].to_dict() for n in self.order()],
                "meta": self.meta, "usefulness_floor": USEFULNESS_FLOOR}

    def to_json(self, indent: Optional[int] = None) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)

    @classmethod
    def from_dict(cls, d: dict) -> "TensorGenome":
        return cls([TensorGene.from_dict(g) for g in (d.get("genes") or [])],
                   meta=d.get("meta") or {})

    @classmethod
    def load_json(cls, path: str) -> "TensorGenome":
        with open(path, encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))

    def save_json(self, path: str) -> None:
        d = os.path.dirname(os.path.abspath(path))
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(self.to_json(indent=2))
        os.replace(tmp, path)

    def summary(self) -> Dict[str, Any]:
        identifiable = [g for g in self.genes.values() if g.identifiable]
        return {
            "n_genes": len(self.genes),
            "width": self.width,
            "identifiable": len(identifiable),
            "above_floor": sum(1 for g in self.genes.values()
                               if g.usefulness >= USEFULNESS_FLOOR),
            "most_useful": (max(self.genes.values(),
                                key=lambda g: g.usefulness).name
                            if self.genes else None),
            "direction_counts": {
                d: sum(1 for g in self.genes.values() if g.direction == d)
                for d in ("up", "down", "flat", "unknown")},
        }
