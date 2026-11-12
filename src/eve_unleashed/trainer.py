"""`eve train`: the developed instrument for test/evolve population cycling.

Ports the proven /tmp harness (gens 1-4: best 0.6793 -> 0.7316, dream
0.375 -> 0.448) into the repo with one addition: the trainer tunes ITSELF.

LILLITH IS THE TUNER. `LillithTuner` watches the best-fitness trajectory and
widens mutation when the lineage stalls, narrows it while it climbs. Her
every decision is logged to the live log as a `tuner` event (width, reason,
history window) so the tuning itself is graded by numbers, never by prose.

Boundary (RULE: who grades the grader): Lillith may tune trainer PARAMETERS
(mutation width). She may not rewrite trainer code or the fitness definition.
Those stay human.

INSPIRATION (landed 2026-11-07): selection is not teaching. Three mechanisms
move ideas between genomes: (1) teacher bias -- the rest of the population
leans toward the best genome's genes; (2) crossover of intuitions -- children
splice both parents' hunch lists (see splice_hunches); (3) dream-to-gene flow
-- the dream log's recurring winning rungs seed fresh genomes, and ancestral
hunches boost live decisions via effective_confidence. The dreams write DNA.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from . import intuition as _i
from . import live_log as _l
from . import websearch as _w
from .honey import sha_file
from .self_modifying_worms import (
    GeneticAlgorithm,
    HUNCH_CAP,
    WormDNA,
    WormFitness,
)
from tools.defaults_profile import read_default

DEFAULT_URLS = [
    "https://example.com",
    "https://www.python.org",
    "https://en.wikipedia.org/wiki/Python_(programming_language)",
    "https://httpbin.org/status/404",
    "http://unseen-train.invalid/",
    "https://www.ietf.org/rfc/rfc2616.txt",
]
DEFAULT_LOG = "C:/tmp/eve-train.jsonl"
DEFAULT_POP = "C:/tmp/eve-pop.json"
DEFAULT_CAP_S = 40.0  # training-harness bound; the product ladder is untouched
TEACH_STRENGTH = 0.25  # teacher-bias pull per generation (fraction of gap)
HUNCH_BOOST = 0.05  # ancestral-rung confidence boost, capped at 0.99
DREAM_HIT_MIN = 2  # a rung must hit this often to enter the seed pool
DREAM_SEED_TOP = 4  # ...and only the top-N winners seed fresh genomes
#: Numeric genes the teacher may pull. mutation_rate is Lillith's, never his.
TEACH_GENES = ("aggressiveness", "min_confidence", "cooldown_s",
               "scan_interval_s", "resource_budget_mb")

# Read evolution config from defaults
EVOLUTION_MODE = read_default("evolution.mode", "live")
EVOLUTION_POPULATION_SIZE = read_default("evolution.population_size", 6)
EVOLUTION_GENERATIONS = read_default("evolution.generations", 3)
EVOLUTION_CONVERGENCE_EPS = read_default("evolution.convergence_eps", 0.005)
EVOLUTION_CONVERGENCE_WINDOW = read_default("evolution.convergence_window", 3)
EVOLUTION_LILLITH_ENABLED = read_default("evolution.lillith_enabled", True)
EVOLUTION_LILLITH_WARMUP_GENS = read_default("evolution.lillith_warmup_gens", 3)
EVOLUTION_LILLITH_STALL_EPS = read_default("evolution.lillith_stall_eps", 0.001)
EVOLUTION_ADVERSARY_SATURATION = read_default("evolution.adversary_saturation", True)
EVOLUTION_DREAM_HIT_TARGET = read_default("evolution.dream_hit_target", 0.97)
EVOLUTION_INHERITANCE_MODE = read_default("evolution.inheritance_mode", "persist")
EVOLUTION_PARSER_AUTOPATCH = read_default("evolution.parser_autopatch", False)


@dataclass
class TrainConfig:
    """Everything `eve train` needs, with the proven defaults baked in.
    
    Defaults evolved via live training (E2R-1..9) on py316t:
    - dream hit-rate 0.97 (1,758 pairs, 1,706 hits)
    - best genome: 3141d73e (gen 69, min_confidence=0.835)
    - cap_s=10.0 (per-fetch cap), pop=6, gens=3
    """

    gens: int = 3
    pop: int = 6
    seed: int = 316
    fresh: bool = False
    calibrate: bool = True  # H-09: trust measured rung hit-rates over priors
    urls: List[str] = field(default_factory=lambda: list(DEFAULT_URLS))
    log_path: str = DEFAULT_LOG
    pop_path: str = DEFAULT_POP
    cap_s: float = 10.0
    fixture: Optional[str] = None  # replay path; live network untouched
    fixture_data: Optional[Dict[str, Dict[str, Any]]] = field(
        default=None, repr=False)  # url -> {state, rung, kind}


class LillithTuner:
    """The tuner. Watches best-fitness; tunes mutation width; logs every call.

    Rule (pure, tested):
      - fewer than 3 points     -> hold (not enough evidence)
      - last-3 window flat      -> widen  x1.5 (stall: force exploration)
      - last point climbs       -> narrow x0.8 (climb: protect the lineage)
      - otherwise               -> hold
    Width is clamped to [0.01, 0.5], the same bounds WormDNA enforces.
    
    Defaults evolved: width=0.15 (from live training), MIN_W=0.01, MAX_W=0.5
    """

    STALL_EPS = 1e-3
    WIDEN = 1.5
    NARROW = 0.8
    MIN_W = 0.01
    MAX_W = 0.5

    def __init__(self, width: float = 0.15, window: int = 3) -> None:
        self.width = max(self.MIN_W, min(self.MAX_W, width))
        self.window = max(2, window)
        self.history: List[float] = []

    def observe(self, best: float) -> Dict[str, Any]:
        """Feed one generation's best fitness; returns the tuning decision."""
        self.history.append(float(best))
        w = self.width
        reason = "hold: warming up"
        pts = self.history[-self.window:]
        if len(pts) >= self.window:
            span = max(pts) - min(pts)
            if span < self.STALL_EPS:
                w = min(self.MAX_W, w * self.WIDEN)
                reason = f"stall: span {span:.5f} < eps, widen to {w:.3f}"
            elif pts[-1] > pts[0]:
                w = max(self.MIN_W, w * self.NARROW)
                reason = f"climb: {pts[0]:.4f} -> {pts[-1]:.4f}, narrow to {w:.3f}"
            else:
                reason = f"hold: span {span:.4f}, no climb"
        self.width = w
        return {"width": w, "reason": reason,
                "history": list(self.history[-self.window:])}

    def apply(self, ga: GeneticAlgorithm) -> None:
        """Set the whole population's mutation rate to the tuned width."""
        for d in ga.population:
            d.mutation_rate = max(self.MIN_W, min(self.MAX_W, self.width))


def effective_confidence(base: float, rung: Optional[str],
                         hunches: List[str]) -> float:
    """Ancestral trust: a guessed rung that won before gets +HUNCH_BOOST.

    Pure, tested. This is inspiration with a behavioral edge -- the lineage
    acts bolder where its ancestors were right."""
    if rung and rung in (hunches or []):
        return min(0.99, float(base) + HUNCH_BOOST)
    return float(base)


def dream_seed_hunches(log_path: str) -> List[str]:
    """Dream-to-gene flow: recurring winning rungs become seed material.

    Reads the dream log (missing log -> no seeds, never an error) and
    returns the top winning rungs. Fresh genomes seeded with these carry
    ancestral intuitions from generation zero."""
    dr = _i.dream(log_path=log_path)
    ranked = sorted(dr.get("by_rung", {}).items(),
                    key=lambda kv: kv[1].get("hits", 0), reverse=True)
    return [r for r, c in ranked[:DREAM_SEED_TOP]
            if r and r != "None" and c.get("hits", 0) >= DREAM_HIT_MIN]


def teacher_bias(ga: GeneticAlgorithm, teacher: WormDNA,
                 strength: float = TEACH_STRENGTH) -> int:
    """Pull every genome except the teacher a fraction toward his genes.

    In place, re-clamped via __post_init__. Returns the inspired count.
    Pure mechanics, tested -- the muse, not the thermostat."""
    n = 0
    for d in ga.population:
        if d is teacher:
            continue
        for gene in TEACH_GENES:
            setattr(d, gene, getattr(d, gene)
                    + strength * (getattr(teacher, gene) - getattr(d, gene)))
        d.__post_init__()
        n += 1
    return n


def _capped(fn: Callable, cap_s: float, *a):
    box: Dict[str, Any] = {}
    t = threading.Thread(target=lambda: box.update(r=fn(*a)), daemon=True)
    t.start()
    t.join(timeout=cap_s)
    if "r" in box:
        return box["r"]
    return {"state": "blocked", "rung": "train-cap",
            "kind": "per-fetch cap %ss" % cap_s}


def _memory_rungs(url: str) -> Dict[str, Any]:
    body, _snap, aerr = _w._archive_fallback(url)
    if not aerr:
        return {"state": "ok", "rung": "archive", "text": body[:20000]}
    cbody, _cid, cerr = _w._commoncrawl_fetch(url)
    if not cerr:
        return {"state": "ok", "rung": "commoncrawl",
                "text": (cbody or "")[:20000]}
    return {"state": "blocked", "rung": "commoncrawl", "detail": "wall held"}


def _load_fixture(path: str) -> Dict[str, Dict[str, Any]]:
    """Read a frozen oracle file. Returns url -> {state, rung, kind}."""
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    return dict(doc.get("outcomes", {}))


def freeze_fixture(urls: List[str], path: str,
                   cap_s: float = DEFAULT_CAP_S) -> Dict[str, Any]:
    """Pin the oracle: fetch each URL live once, record the outcome.

    Replay (fixture_data) substitutes these pinned outcomes for every
    fetch, so fitness is deterministic given the file. Re-freeze to
    measure live drift; never edit the file by hand (sha-checked)."""
    outcomes = {}
    for u in urls:
        res = _capped(_w.fetch_visible, cap_s, u)
        outcomes[u] = {"state": res.get("state"),
                       "rung": res.get("rung"), "kind": res.get("kind")}
    doc = {"urls": list(urls), "frozen_at": time.time(), "cap_s": cap_s,
           "outcomes": outcomes}
    return _write_fixture_doc(doc, path)


def _write_fixture_doc(doc: Dict[str, Any], path: str) -> Dict[str, Any]:
    """Write the fixture so the bytes on disk are the bytes hashed.

    Windows text-mode translates LF to CRLF after the hash was computed,
    so a sha of the in-memory blob never matches the file (measured
    2026-11-07: a92dbf7 vs cc3ed11d on identical content). newline=""
    disables translation; the sha is taken over the file itself, so the
    pin and the bytes cannot disagree again on any platform."""
    blob = json.dumps(doc, indent=1, sort_keys=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(blob)
    doc = dict(doc)
    doc["sha256"] = sha_file(path)
    doc["path"] = path
    return doc


def run_url(dna: WormDNA, url: str, log_path: str,
            cap_s: float = DEFAULT_CAP_S,
            calib: Dict[str, float] = None,
            fixture: Dict[str, Dict[str, Any]] = None):
    """One scored fetch: intuition guesses, policy decides live-vs-memory.

    calib maps rung -> measured hit-rate (H-09): a rung the dream log has
    graded outranks the intuition prior for the skip-live decision."""
    g = _i.intuit(url, log_path=log_path)
    _l.emit("fetch", "intuition", path=log_path, url=url,
            guess=g["state"], confidence=g["confidence"],
            predicted=g.get("predicted_rung"))
    base = g["confidence"]
    if calib and g.get("predicted_rung") in calib:
        base = calib[g.get("predicted_rung")]
    conf = effective_confidence(base, g.get("predicted_rung"), dna.hunches)
    skip_live = (g["state"] == "blocked"
                 and conf >= dna.min_confidence
                 and dna.aggressiveness < 0.5)
    t0 = time.perf_counter()
    replay = False
    if fixture is not None and url in fixture:
        res = dict(fixture[url])
        replay = True
    else:
        res = (_capped(_memory_rungs, cap_s, url) if skip_live
               else _capped(_w.fetch_visible, cap_s, url))
    if res["state"] != "ok" and dna.aggressiveness >= 0.75 and not skip_live:
        res = _capped(_w.fetch_visible, cap_s, url)
    dt = time.perf_counter() - t0
    _l.emit("fetch", "answer", path=log_path, url=url, rung=res.get("rung"),
            state=res["state"], kind=res.get("kind"), replay=replay)
    actual_ok = res["state"] == "ok"
    hit = ((g["state"] == "guess" and actual_ok)
           or (g["state"] == "blocked" and not actual_ok))
    hunch = g.get("predicted_rung") if (hit and g.get("predicted_rung")) else None
    return actual_ok, hit, dt, hunch


def save_pop(ga: GeneticAlgorithm, path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"generation": ga.generation,
                   "genomes": [d.to_dict() for d in ga.population]}, fh)


def load_pop(ga: GeneticAlgorithm, path: str) -> None:
    with open(path, encoding="utf-8") as fh:
        saved = json.load(fh)
    if isinstance(saved, dict):  # current format: generation + genomes
        ga.seed([WormDNA.from_dict(d) for d in saved["genomes"]])
        ga.generation = int(saved.get("generation", 1))
    else:  # legacy: bare genome list, generation unknown
        ga.seed([WormDNA.from_dict(d) for d in saved])


def load_or_seed(ga: GeneticAlgorithm, cfg: TrainConfig,
                 rng: random.Random) -> int:
    """Seed a fresh population or resume from the pop file. Returns start gen."""
    if not cfg.fresh and os.path.exists(cfg.pop_path):
        load_pop(ga, cfg.pop_path)
        return ga.generation
    seeds = dream_seed_hunches(cfg.log_path)
    ga.seed([WormDNA(worm_id=f"t1-{i}", worm_type="fetcher",
                     aggressiveness=rng.uniform(0.1, 0.9),
                     min_confidence=rng.uniform(0.5, 0.95),
                     cooldown_s=rng.uniform(0.1, 3.0),
                     mutation_rate=0.15, hunches=list(seeds))
             for i in range(ga.population_size)])
    return 1


def score_genome(dna: WormDNA, cfg: TrainConfig,
                 calib: Dict[str, float] = None) -> WormFitness:
    """Score one genome over every URL. No selection, no tuning, no breed.

    The shared scoring core: one_generation and null_control both call
    it, so the baseline and the lineage are graded by the same ruler."""
    ok = hits = wall = 0.0
    for u in cfg.urls:
        o, h, dt, hunch = run_url(dna, u, cfg.log_path, cfg.cap_s,
                                 calib=calib if cfg.calibrate else None,
                                 fixture=cfg.fixture_data)
        ok += o
        hits += h
        wall += dt
        if hunch:
            dna.hunches.append(hunch)
            dna.hunches = dna.hunches[-HUNCH_CAP:]
        time.sleep(min(dna.cooldown_s, 1.0))
    n = len(cfg.urls)
    return WormFitness(
        worm_id=dna.worm_id, worm_type=dna.worm_type, generation=dna.generation,
        resource_usage=min(1.0, wall / (n * 2 * 12.0)),
        insight_quality=hits / n, error_rate=(n - ok) / n,
        uptime_s=wall, actions_taken=n, actions_succeeded=int(ok))


def null_control(cfg: TrainConfig, budget: int) -> Dict[str, Any]:
    """Random-search baseline: N fresh genomes, scored once each, no evolution.

    No dream seeds, no teacher, no Lillith, no calibrate -- pure chance
    on the same ruler. If evolution cannot beat this per equal budget,
    the lineage claim is narrative. Uses isolated pop/log files only."""
    _ensure_fixture(cfg)
    rng = random.Random(cfg.seed)
    scores = []
    for i in range(budget):
        d = WormDNA(worm_id="null-%d" % i, worm_type="fetcher",
                    aggressiveness=rng.uniform(0.1, 0.9),
                    min_confidence=rng.uniform(0.5, 0.95),
                    cooldown_s=rng.uniform(0.0, 0.2),
                    mutation_rate=0.15, hunches=[])
        f = score_genome(d, cfg, calib=None)
        s = round(f.score(), 4)
        scores.append(s)
        print("null %d/%d: score=%.4f" % (i + 1, budget, s), flush=True)
    return {"budget": budget,
            "best": round(max(scores), 4) if scores else 0.0,
            "mean": round(sum(scores) / len(scores), 4) if scores else 0.0,
            "scores": scores}


def one_generation(ga: GeneticAlgorithm, gnum: int, cfg: TrainConfig,
                   tuner: LillithTuner,
                   calib: Dict[str, float]) -> Dict[str, Any]:
    """Score every genome live, tune, evolve. Returns the gen summary.

    calib carries last generation's measured rung hit-rates into this
    generation's skip-live decisions, then is refreshed in place."""
    for d in ga.population:
        ga.record_fitness(score_genome(d, cfg, calib))
    scores = {d.worm_id: ga.fitness[d.worm_id].score() for d in ga.population}
    best = max(ga.population, key=lambda d: scores[d.worm_id])
    dr = _i.dream(log_path=cfg.log_path)
    decision = tuner.observe(scores[best.worm_id])
    _l.emit("tuner", "lillith", path=cfg.log_path, gen=gnum,
            width=decision["width"], reason=decision["reason"],
            best=round(scores[best.worm_id], 4))
    summary = {"gen": gnum, "best": round(scores[best.worm_id], 4),
               "mean": round(sum(scores.values()) / len(scores), 4),
               "dream_pairs": dr["pairs"],
               "dream_hit": round(dr["hit_rate"], 3),
               "best_id": best.worm_id[:8],
               "aggr": round(best.aggressiveness, 2),
               "conf": round(best.min_confidence, 2),
               "mutw": round(decision["width"], 3),
               "tune": decision["reason"]}
    print("gen {gen}: best={best:.4f} mean={mean:.4f} "
          "dream_pairs={dream_pairs} dream_hit={dream_hit:.3f} "
          "best={best_id} aggr={aggr:.2f} conf={conf:.2f} "
          "mutw={mutw:.3f} [{tune}]".format(**summary), flush=True)
    if cfg.calibrate:
        calib.clear()
        calib.update({r: c["hits"] / c["pairs"]
                      for r, c in dr.get("by_rung", {}).items()
                      if c.get("pairs", 0) >= 2})
    ga.evolve()
    inspired = teacher_bias(ga, best)
    _l.emit("evolve", "inspire", path=cfg.log_path, gen=gnum,
            teacher=best.worm_id[:8], strength=TEACH_STRENGTH,
            inspired=inspired)
    summary["insp"] = inspired
    tuner.apply(ga)
    return summary


def _ensure_fixture(cfg: TrainConfig) -> None:
    """Load the frozen oracle once. A --fixture flag that silently
    scores live is a lie shaped like a pin; this makes the pin real."""
    if cfg.fixture and cfg.fixture_data is None:
        cfg.fixture_data = _load_fixture(cfg.fixture)


def run(cfg: TrainConfig) -> List[Dict[str, Any]]:
    """Test/evolve cycling: live-score, tune, breed, repeat. Returns summaries."""
    _ensure_fixture(cfg)
    rng = random.Random(cfg.seed)
    ga = GeneticAlgorithm(population_size=cfg.pop, elite_keep=2, rng=rng)
    if cfg.fresh and os.path.exists(cfg.pop_path):
        os.remove(cfg.pop_path)
    start = load_or_seed(ga, cfg, rng)
    tuner = LillithTuner()
    out = []
    calib: Dict[str, float] = {}
    for g in range(start, start + cfg.gens):
        out.append(one_generation(ga, g, cfg, tuner, calib))
    save_pop(ga, cfg.pop_path)
    print("DONE gens %d-%d final_gen=%d pop_saved=%s tuner=lillith mutw=%.3f"
          % (start, start + cfg.gens - 1, ga.generation,
             cfg.pop_path, tuner.width), flush=True)
    return out
