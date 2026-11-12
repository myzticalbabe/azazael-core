# coding: utf-8
"""qd_bridge.py — wire MAP-Elites + IslandModel into one callable step.

One call = one island generation (with ring migration) + archive each
island's best into the QD grid by behavior descriptor. Returns stats.
"""
from typing import Callable, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


def qd_step(map_elites, island, fitness_fn: Callable,
            behavior_fn: Callable) -> dict:
    """Run island.step(fitness_fn), archive island bests by behavior.

    behavior_fn(genes) -> tuple[float, ...]. Returns
    {"island": step_stats, "archived": n_new, "qd": map_elites.stats()}.
    """
    step_stats = island.step(fitness_fn)
    archived = 0
    for e in island.islands:
        best = e.get_best_genome()
        if best is None:
            continue
        try:
            behavior = tuple(behavior_fn(dict(best.genes)))
        except Exception as exc:
            note_failure("qd_bridge:L25", exc)
            continue
        if map_elites.add(dict(best.genes), float(best.fitness), behavior):
            archived += 1
    return {"island": step_stats, "archived": archived,
            "qd": map_elites.stats()}


def qd_suggest(map_elites, tpe=None):
    """Suggest next genome: MAP-Elites elite perturb, TPE fallback optional."""
    s = map_elites.suggest()
    if s:
        return {"source": "map-elites", "genes": s}
    if tpe is not None:
        return {"source": "tpe", "genes": tpe.suggest()}
    return {"source": "none", "genes": {}}
