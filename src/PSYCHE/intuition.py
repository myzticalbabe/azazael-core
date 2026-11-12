# coding: utf-8
"""PSYCHE Phase 5 -- INTUITION: compiled inference with a conscience.

WHAT INTUITION ACTUALLY IS, IN THE LITERATURE
---------------------------------------------
The dual-process vocabulary ("System 1 / System 2") is Kahneman's *description*;
the mechanism that fits a machine is what Klein, Calderwood & Clinton (2010,
"Rapid Decision Making on the Fire Ground", and the Recognition-Primed Decision
model) actually observed: experts do not compare options. They recognise a
situation as an instance of a familiar type, and the first single course of
action that the recognition makes available is evaluated by MENTAL SIMULATION,
not by deliberation among alternatives. Intuition is therefore COMPILED
INFERENCE: a slow, expensive evaluation has been run enough times that its result
is retrievable directly. It is fast because it is cached, and it is right because
it was built from the same source as the slow path.

That is the whole design of this module, and it has one consequence worth stating
plainly: an intuition layer is only as good as the record of when it was WRONG.
A cache that is silently updated on disagreement is a cache whose error rate
cannot be measured. So `read()` never overwrites a prototype it disagrees with --
it files the disagreement, and `calibration()` reports it as a Brier score
against resolutions the caller supplied.

WHY THIS IS THE RIGHT FORM FOR PSYCHE
-------------------------------------
`network.py` gives message passing over a person's construct graph: that is the
deliberate path, O(edges * steps) per query with a full trace. This module
compiles repeated queries over that graph into `Prototype` objects -- stabilised
activation configurations with a support count -- and answers from the nearest
prototype in O(prototypes * nodes). The pass over the graph is unchanged, so
`agreement()` is a real measurement of whether the compiled path still tracks the
slow one.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
  * No language model, no embedding, no network call. Matching is a normalised
    inner product over named constructs, so a reading can be explained node by
    node -- which is the only way a person can contest it.
  * No "confidence" that is really a similarity. `confidence` is similarity times
    a support term, and both are returned separately so a caller can see which
    one is doing the work.
  * No autonomous action. `read()` returns a reading; what happens next is
    `rt_agent.py`'s decision, made where a safety check can precede it.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from PSYCHE.network import PersonNetwork, clamp

try:
    from CORE.failure_ledger import note_failure
except ImportError as _ledger_exc:                     # pragma: no cover - host always has it
    _LEDGER_IMPORT_ERROR = str(_ledger_exc)

    def note_failure(where: str, exc: BaseException | None = None) -> None:
        """Record nothing: the ledger itself is missing. Named, not silent."""
        return None

#: Below this support count a configuration is a SINGLE EVENT, not a prototype.
#: One occurrence is not a pattern; the RPD literature's experts have thousands.
MIN_SUPPORT = 2

#: Cosine floor for "this cue pattern is an instance of that prototype". Below
#: it the honest answer is "I have not seen this", which `read()` returns as
#: `known=False` rather than as a low-confidence guess.
MATCH_FLOOR = 0.55

#: Default threshold at which the fast path is trusted. Calibrated per person by
#: `recommend_threshold()`, never hard-coded into behaviour.
DEFAULT_THRESHOLD = 0.62

#: Resolutions kept for calibration. Bounded: a session is not a dataset, and an
#: unbounded ledger in a realtime loop is a leak.
MAX_RESOLUTIONS = 200


def _norm(vec: Dict[str, float]) -> float:
    return math.sqrt(sum(v * v for v in vec.values())) or 1e-12


def cosine(a: Dict[str, float], b: Dict[str, float],
           keys: Optional[Sequence[str]] = None) -> float:
    """Cosine similarity over named coordinates (missing coordinate = 0)."""
    names = list(keys) if keys is not None else sorted(set(a) | set(b))
    dot = sum(a.get(k, 0.0) * b.get(k, 0.0) for k in names)
    return dot / (_norm({k: a.get(k, 0.0) for k in names})
                  * _norm({k: b.get(k, 0.0) for k in names}))


def coverage_similarity(cues: Dict[str, float], profile: Dict[str, float]) -> Tuple[float, float]:
    """Similarity over the coordinates actually measured, discounted by coverage.

    The realtime loop does not always observe every construct: a turn may carry
    two of ten nodes. Plain cosine over the union punishes that twice -- once for
    the missing coordinates contributing zero to the dot product, and once for
    them inflating the norms -- so a cue that is an EXACT instance of a prototype
    on the two nodes it measured scores 0.76 and never clears the threshold.

    Measured 2026-11-01: with 2 of 4 coordinates the fast path was taken 0 times
    in 100 calls, which is a broken decision rule, not a cautious one.

    The fix is the standard one: match on the intersection, then multiply by
    sqrt(coverage) so incomplete evidence cannot claim full confidence. A
    1-of-10 cue scores ~0.32 at best, a full-coverage cue is unchanged, and the
    asymmetry pushes partial evidence toward the deliberate path -- which is
    where incomplete evidence belongs.

    Returns (similarity, coverage).
    """
    common = [k for k in cues if k in profile]
    union = set(cues) | set(profile)
    coverage = len(common) / len(union) if union else 0.0
    if not common:
        return 0.0, 0.0
    observed = cosine(cues, profile, keys=common)
    return max(0.0, observed) * math.sqrt(coverage), coverage


@dataclass
class Prototype:
    """A named, stabilised activation configuration -- one compiled intuition.

    ``support`` counts how many observations folded into it, ``hit_count`` how
    many times it was read successfully, and ``wrong`` how many times a resolution
    contradicted it. The last field is the one that keeps this honest: a prototype
    with support 9 and wrong 7 is a habit, not knowledge, and `calibration()`
    will say so.
    """

    name: str
    profile: Dict[str, float]
    support: int = 1
    hit_count: int = 0
    wrong: int = 0
    last_seen_s: float = 0.0

    def to_dict(self) -> dict:
        return {"name": self.name,
                "profile": {k: round(v, 6) for k, v in self.profile.items()},
                "support": self.support, "hit_count": self.hit_count,
                "wrong": self.wrong, "last_seen_s": self.last_seen_s}

    @classmethod
    def from_dict(cls, row: dict) -> "Prototype":
        return cls(name=str(row["name"]),
                   profile={str(k): float(v) for k, v in (row.get("profile") or {}).items()},
                   support=int(row.get("support", 1)),
                   hit_count=int(row.get("hit_count", 0)),
                   wrong=int(row.get("wrong", 0)),
                   last_seen_s=float(row.get("last_seen_s", 0.0)))


@dataclass
class Reading:
    """What the fast path returns. Every field is a claim the caller can check."""

    prototype: Optional[str]
    confidence: float
    similarity: float
    support: int
    known: bool
    latency_us: float
    path: str
    profile: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"prototype": self.prototype, "confidence": round(self.confidence, 6),
                "similarity": round(self.similarity, 6), "support": self.support,
                "known": self.known, "latency_us": round(self.latency_us, 2),
                "path": self.path}


class IntuitionIndex:
    """Prototype store over one person's construct graph.

    The index is per-person, like every other PSYCHE artifact: a prototype
    learned from one graph is meaningless against another's coordinates.
    """

    def __init__(self, network: PersonNetwork,
                 min_support: int = MIN_SUPPORT,
                 match_floor: float = MATCH_FLOOR) -> None:
        self.network = network
        self.min_support = int(min_support)
        self.match_floor = float(match_floor)
        self._prototypes: Dict[str, Prototype] = {}
        self._resolutions: List[dict] = []
        self._errors: List[str] = []
        self._counter = 0

    # -- learning ---------------------------------------------------------
    def absorb(self, activation: Dict[str, float], name: Optional[str] = None,
               at_s: Optional[float] = None) -> Prototype:
        """Fold one activation configuration into the index.

        If it is an instance of an existing prototype (cosine >= match_floor) the
        prototype's profile moves toward it by a running mean, weighted by
        support: a new observation nudges a well-established prototype, it does
        not redefine it. Otherwise a prototype is created at support 1 and stays
        below `min_support` -- visible, but not yet trusted by `read()`.
        """
        profile = {k: clamp(v) for k, v in activation.items()}
        if not profile:
            raise ValueError("absorb() needs at least one activated construct")
        now = time.monotonic() if at_s is None else float(at_s)
        best_name, best_sim = None, -1.0
        for proto in self._prototypes.values():
            sim, _coverage = coverage_similarity(profile, proto.profile)
            if sim > best_sim:
                best_name, best_sim = proto.name, sim
        if best_name is not None and best_sim >= self.match_floor:
            proto = self._prototypes[best_name]
            weight = 1.0 / (proto.support + 1)
            for key, value in profile.items():
                proto.profile[key] = clamp(proto.profile.get(key, 0.0)
                                           + weight * (value - proto.profile.get(key, 0.0)))
            proto.support += 1
            proto.last_seen_s = now
            return proto
        self._counter += 1
        label = name or "proto-%03d" % self._counter
        proto = Prototype(label, profile, support=1, last_seen_s=now)
        self._prototypes[label] = proto
        return proto

    # -- the fast path ----------------------------------------------------
    def read(self, cues: Dict[str, float]) -> Reading:
        """Answer from the nearest trusted prototype, or say it is not known.

        Latency is measured, not assumed: the returned `latency_us` is the
        elapsed wall time of this call. A fast path that has to be benchmarked
        externally to be believed is not being measured where it matters.
        """
        started = time.perf_counter()
        profile = {k: clamp(v) for k, v in cues.items()}
        best: Optional[Prototype] = None
        best_sim = -1.0
        for proto in self._prototypes.values():
            if proto.support < self.min_support:
                continue
            sim, _coverage = coverage_similarity(profile, proto.profile)
            if sim > best_sim:
                best, best_sim = proto, sim
        elapsed = (time.perf_counter() - started) * 1e6
        if best is None or best_sim < self.match_floor:
            return Reading(prototype=None, confidence=0.0,
                           similarity=max(0.0, best_sim), support=0,
                           known=False, latency_us=elapsed, path="intuition")
        # Confidence is similarity SMALL-TIME support, and both are reported
        # separately: a 0.60 match from forty observations is not the same claim
        # as a 0.60 match from two, and a caller that only sees the product
        # cannot tell them apart.
        evidence = 1.0 - math.exp(-best.support / 4.0)
        confidence = best_sim * evidence
        best.hit_count += 1
        return Reading(prototype=best.name, confidence=confidence,
                       similarity=best_sim, support=best.support, known=True,
                       latency_us=elapsed, path="intuition",
                       profile=dict(best.profile))

    # -- the deliberate path ----------------------------------------------
    def deliberate(self, cues: Dict[str, float], steps: int = 2) -> Reading:
        """Run the graph. Expensive, fully traceable, always answers.

        This is the source of truth the fast path is compiled from; calling it is
        how a disagreement gets detected in the first place.
        """
        started = time.perf_counter()
        propagated = self.network.propagate(cues, steps=steps)
        best: Optional[Prototype] = None
        best_sim = -1.0
        for proto in self._prototypes.values():
            if proto.support < self.min_support:
                continue
            sim, _coverage = coverage_similarity(propagated, proto.profile)
            if sim > best_sim:
                best, best_sim = proto, sim
        elapsed = (time.perf_counter() - started) * 1e6
        evidence = 1.0 - math.exp(-(best.support / 4.0)) if best else 0.0
        return Reading(prototype=best.name if best else None,
                       confidence=max(0.0, best_sim) * evidence if best else 0.0,
                       similarity=max(0.0, best_sim), support=best.support if best else 0,
                       known=best is not None and best_sim >= self.match_floor,
                       latency_us=elapsed, path="deliberate",
                       profile=propagated)

    def read_or_think(self, cues: Dict[str, float],
                      threshold: float = DEFAULT_THRESHOLD) -> Reading:
        """The decision this module exists for: is this worth thinking about?

        One rule, and it is the published one: use the recognised instance when
        recognition is confident; otherwise fall back to evaluation. The fallback
        is not a failure -- it is why the fast path can be trusted at all.
        """
        fast = self.read(cues)
        if fast.known and fast.confidence >= threshold:
            return fast
        slow = self.deliberate(cues)
        slow.path = "deliberate-after-miss"
        return slow

    # -- calibration: the part that keeps it honest ------------------------
    def resolve(self, reading: Reading, outcome: str, correct: bool,
                at_s: Optional[float] = None) -> dict:
        """File what actually happened against what the reading claimed.

        Deliberately NOT an update. Marking a prototype `wrong` is all this does:
        the profile stays as it was, so the same error can be made again and
        counted again. Editing the profile here would erase the evidence that
        the intuition was bad.
        """
        record = {"prototype": reading.prototype, "path": reading.path,
                  "confidence": clamp(reading.confidence), "outcome": str(outcome),
                  "correct": bool(correct),
                  "at_s": time.monotonic() if at_s is None else float(at_s)}
        self._resolutions.append(record)
        if len(self._resolutions) > MAX_RESOLUTIONS:
            self._resolutions = self._resolutions[-MAX_RESOLUTIONS:]
        if not correct and reading.prototype in self._prototypes:
            self._prototypes[reading.prototype].wrong += 1
        return record

    def calibration(self) -> dict:
        """Brier score and agreement, computed only from resolved readings.

        Brier = mean((confidence - outcome)^2) over resolved readings, with
        outcome 1.0 for a correct resolution. Reported WITH the count it rests
        on, because 0.0 over one resolution is not calibration. A reading that
        was never resolved contributes nothing, which is why `resolved` is the
        first field.
        """
        if not self._resolutions:
            return {"resolved": 0, "brier": None, "accuracy": None,
                    "mean_confidence": None,
                    "note": "no resolved readings: calibration is unknown, not perfect"}
        squared = []
        hits = 0
        for row in self._resolutions:
            target = 1.0 if row["correct"] else 0.0
            squared.append((row["confidence"] - target) ** 2)
            hits += 1 if row["correct"] else 0
        count = len(self._resolutions)
        return {"resolved": count,
                "brier": round(sum(squared) / count, 6),
                "accuracy": round(hits / count, 6),
                "mean_confidence": round(
                    sum(r["confidence"] for r in self._resolutions) / count, 6),
                "note": "Brier 0 is perfect; compare the mean confidence against "
                        "the accuracy rather than reading either alone"}

    def agreement(self, cues: Dict[str, float]) -> dict:
        """Do the fast and slow paths name the same prototype, and what did each cost?

        The speed claim is only meaningful next to this: O(prototypes) against
        O(edges * steps) for the same question, with the same answer.
        """
        fast = self.read(cues)
        slow = self.deliberate(cues)
        return {"fast": fast.prototype, "slow": slow.prototype,
                "agree": fast.prototype == slow.prototype,
                "fast_us": round(fast.latency_us, 2),
                "slow_us": round(slow.latency_us, 2),
                "ratio": round(slow.latency_us / fast.latency_us, 2)
                if fast.latency_us else None}

    def recommend_threshold(self) -> dict:
        """The confidence threshold at which the fast path starts to be right.

        Sweeps the resolved readings and returns the lowest threshold whose
        subset accuracy is at least the overall accuracy. Returns
        `threshold=None` when there is not enough resolved data, which is the
        honest answer and stops a caller from adopting a number pulled from
        two samples.
        """
        if len(self._resolutions) < 8:
            return {"threshold": None, "resolved": len(self._resolutions),
                    "note": "need at least 8 resolved readings to recommend a threshold"}
        overall = sum(1 for r in self._resolutions if r["correct"]) / len(self._resolutions)
        for candidate in (0.40, 0.45, 0.50, 0.55, 0.60, 0.62, 0.65, 0.70, 0.75, 0.80):
            subset = [r for r in self._resolutions if r["confidence"] >= candidate]
            if len(subset) < 4:
                continue
            acc = sum(1 for r in subset if r["correct"]) / len(subset)
            if acc >= overall:
                return {"threshold": candidate, "resolved": len(self._resolutions),
                        "subset": len(subset), "subset_accuracy": round(acc, 4),
                        "overall_accuracy": round(overall, 4)}
        return {"threshold": None, "resolved": len(self._resolutions),
                "note": "no threshold beats the base rate on this data"}

    # -- introspection ----------------------------------------------------
    def prototypes(self) -> List[Prototype]:
        return sorted(self._prototypes.values(),
                      key=lambda p: (-p.support, p.name))

    def diagnostics(self) -> dict:
        trusted = [p for p in self._prototypes.values() if p.support >= self.min_support]
        return {"prototypes": len(self._prototypes), "trusted": len(trusted),
                "resolved": len(self._resolutions),
                "min_support": self.min_support,
                "match_floor": self.match_floor,
                "errors": list(self._errors[-20:])}

    def to_dict(self) -> dict:
        return {"min_support": self.min_support, "match_floor": self.match_floor,
                "counter": self._counter,
                "prototypes": [p.to_dict() for p in self._prototypes.values()],
                "resolutions": self._resolutions[-50:]}

    @classmethod
    def from_dict(cls, row: dict, network: PersonNetwork) -> "IntuitionIndex":
        index = cls(network, min_support=int(row.get("min_support", MIN_SUPPORT)),
                    match_floor=float(row.get("match_floor", MATCH_FLOOR)))
        index._counter = int(row.get("counter", 0))
        for p in row.get("prototypes") or []:
            proto = Prototype.from_dict(p)
            index._prototypes[proto.name] = proto
        for r in row.get("resolutions") or []:
            index._resolutions.append(dict(r))
        return index

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=1), encoding="utf-8")
        return target

    @classmethod
    def load(cls, path: str | Path, network: PersonNetwork) -> "IntuitionIndex":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")), network)
