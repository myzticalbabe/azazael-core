# coding: utf-8
"""Canonical search bounds for the RLTrainLoop hyperparameter genome.

WHY THIS MODULE EXISTS
----------------------
These four bounds were written out in three places and the copies drifted apart,
which silently disabled the whole adaptation loop.

On 2026-09-11 the evolution space was widened — `learning_rate` ceiling 0.5 -> 0.9
and `discount` floor 0.8 -> 0.5 — because the old space was provably exhausted:
production sat exactly on the old ceiling and within 0.003 of the old floor (see
`morph.py`, which documents that batch). `scripts/evolution_cycle.py` was updated,
and its own comment records why the literals there had to be replaced by a shared
definition.

Two readers were NOT updated:

  * `ScheduleConfig.apply_evolved_overrides` — the reader PRODUCTION uses
  * `scripts/benchmark_evolution.py` check [7]

Both still range-checked a promoted genome against the PRE-widening numbers
(`learning_rate <= 0.5`, `discount >= 0.8`). The GA then did exactly what the
widening invited: it pushed `learning_rate` to the new 0.9 ceiling and `discount`
to the new 0.5 floor and promoted a champion that lives there
(`state/evolved_rl_params.json`, promoted 2026-09-15). The production reader
rejected precisely those two values and returned `False` without a log line, so
scheduled training kept running the pre-evolution defaults while a promoted
champion sat on disk — for three days, with a red alarm in
`benchmark_evolution.py` that nothing was wired up to look at.

Bounds are a property of the SEARCH SPACE, not of any one consumer. They live
here, in the library layer, and both the writer and the readers import them.

Widening discipline
-------------------
Widening a bound here is a deliberate act: it changes what the GA may explore AND
what production will accept, in one place, atomically. `learning_rate` stays below
1.0 on purpose — tabular Q-learning is only guaranteed to converge for a step size
in (0, 1].
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, NamedTuple, Tuple

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

#: Inclusive bounds, gene name -> (lo, hi). The GA samples inside these and every
#: reader rejects values outside them.
RL_GENE_BOUNDS: Dict[str, Tuple[float, float]] = {
    "learning_rate": (0.01, 0.9),
    "epsilon": (0.0, 0.5),
    "discount": (0.5, 0.999),
    "epsilon_decay": (0.0, 0.3),  # exploration-annealing speed (2026-09-08)
}

#: Genes a promoted genome must carry for the consumer to be able to act on it.
RL_REQUIRED_GENES: Tuple[str, ...] = ("learning_rate", "epsilon", "discount")


class GeneCheck(NamedTuple):
    """Outcome of checking a promoted genome against `RL_GENE_BOUNDS`."""

    genes: Dict[str, float]
    """Genes the consumer can safely apply, coerced to float."""

    problems: List[str]
    """Reasons the genome is unusable. Empty means it is safe to apply."""

    ignored: List[str]
    """Genes present in the file that this consumer does not know how to apply.

    NOT a problem. The structural-mutation machinery (`morph.add_gene`) exists to
    grow the genome past this fixed set, so a promoted file may legitimately carry
    genes a given reader has never heard of. Treating an unknown gene as a failure
    would recreate the exact bug this module was written to fix: a reader vetoing
    a search space that legitimately grew.
    """

    @property
    def ok(self) -> bool:
        return not self.problems


def validate_genes(genes: Any) -> GeneCheck:
    """Check a promoted `genes` payload against the canonical bounds.

    Single implementation of the range predicate, shared by the production reader,
    the benchmark, and the tests, so a wrong bound cannot be fixed in one place and
    left stale in another.

    Unknown genes are returned in `ignored`, never as problems.
    """
    if not isinstance(genes, Mapping):
        return GeneCheck({}, [f"'genes' must be a mapping, got {type(genes).__name__}"], [])

    coerced: Dict[str, float] = {}
    problems: List[str] = []
    ignored: List[str] = []

    for name in RL_REQUIRED_GENES:
        if name not in genes:
            problems.append(f"missing required gene '{name}'")

    for name, raw in genes.items():
        if name not in RL_GENE_BOUNDS:
            ignored.append(str(name))
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            note_failure("rl_gene_bounds:L109", exc)
            problems.append(f"{name}={raw!r} is not numeric")
            continue
        lo, hi = RL_GENE_BOUNDS[name]
        if not lo <= value <= hi:
            problems.append(
                f"{name}={value} is outside the evolved bound ({lo}, {hi})"
            )
            continue
        coerced[name] = value

    return GeneCheck(coerced, problems, ignored)
