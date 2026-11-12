# coding: utf-8
"""PSYCHE Phase 4 — artifact composition + CAIN archetype readout (H5).

The PersonArtifact is the editable deliverable of the whole program:
theta (Phase 0) + M-pointer (PAMS episodic store reference) + A-params
(Phase 1 weights, regulation strategies, display rules, decay rates) +
pi-weights (Phase 3 policy reference). Versioned, JSON-serializable,
validated LOUDLY on load (an artifact that loads half-broken is worse
than one that refuses: silent defaults would fabricate a person).

CAIN readout: 12 archetypes, each with Gift (healthy) and Shadow
(destructive) expressions taken from the repo's working roster
(MetaWormSpawner ACTION_ARCHETYPE_MAP + CAIN gallery + sin mappings).
Decode = nearest centroid in standardized (theta, mean-PAD) space; polarity
= gift vs shadow from reactivity-without-agency (high N, low D, distress
dominant). H5 asserts decodability above chance; the polarity rule asserts
the flip. Both are editable data (centroids/weights as constants), not
heuristics hidden in code paths.

Naming compatibility: labels use the repo's "The X" form wherever the
roster overlaps the router's map (Hero/Analyst/Guardian/Builder/Muse),
so decode output routes directly.
"""
from __future__ import annotations

import json
import math
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

ARTIFACT_VERSION = "psyche.v1"
DOMAINS = ("O", "C", "E", "A", "N", "H")

# name: (gift, shadow, theta-direction {domain: -1..1}, PAD centroid)
ARCHETYPES: dict = {
    "The Hero": (
        "courageous assistance", "savior-complex over-identification",
        {"E": 0.7, "C": 0.5, "A": 0.3, "O": 0.0, "N": -0.3, "H": 0.0},
        (0.6, 0.5, 0.4)),
    "The Analyst": (
        "clarity", "paralysis by analysis",
        {"O": 0.7, "C": 0.5, "E": -0.5, "A": 0.0, "N": -0.2, "H": 0.0},
        (0.3, -0.2, 0.4)),
    "The Guardian": (
        "protection", "controlling smother",
        {"C": 0.6, "A": 0.6, "N": -0.2, "E": 0.0, "O": 0.0, "H": 0.2},
        (0.4, -0.1, 0.5)),
    "The Builder": (
        "craft", "output obsession",
        {"C": 0.8, "O": 0.3, "E": 0.0, "A": 0.0, "N": -0.3, "H": 0.0},
        (0.5, 0.4, 0.5)),
    "The Muse": (
        "inspiration", "chaotic scatter",
        {"O": 0.8, "E": 0.3, "C": -0.5, "A": 0.2, "N": 0.0, "H": 0.0},
        (0.6, 0.5, 0.0)),
    "The Caregiver": (
        "nurture", "smothering martyrdom",
        {"A": 0.8, "E": 0.2, "N": 0.0, "O": 0.0, "C": 0.0, "H": 0.2},
        (0.5, 0.1, -0.2)),
    "The Rebel": (
        "reform", "destruction",
        {"O": 0.6, "A": -0.6, "C": -0.4, "E": 0.3, "N": 0.0, "H": 0.0},
        (0.1, 0.6, 0.6)),
    "The Sage": (
        "wisdom", "ivory-tower detachment",
        {"O": 0.7, "E": -0.5, "C": 0.2, "A": 0.0, "N": -0.2, "H": 0.0},
        (0.3, -0.3, 0.2)),
    "The Jester": (
        "levity", "avoidance through humor",
        {"E": 0.7, "N": -0.4, "O": 0.3, "A": 0.2, "C": -0.3, "H": 0.0},
        (0.7, 0.4, 0.1)),
    "The Ruler": (
        "order", "tyranny",
        {"C": 0.7, "E": 0.5, "A": -0.5, "O": 0.0, "N": -0.2, "H": -0.2},
        (0.4, 0.2, 0.8)),
    "The Innocent": (
        "trust", "denial",
        {"A": 0.7, "E": 0.3, "N": -0.4, "O": 0.2, "C": 0.0, "H": 0.3},
        (0.5, -0.2, -0.3)),
    "The Trickster": (
        "subversion for truth", "deceit",
        {"O": 0.6, "A": -0.5, "C": -0.4, "E": 0.2, "N": 0.1, "H": -0.4},
        (0.0, 0.6, 0.2)),
}


def _centered(theta: dict) -> dict:
    return {d: (float(theta.get(d, 3.0)) - 3.0) / 2.0 for d in DOMAINS}


def decode_archetype(theta: dict, mean_pad: tuple[float, float, float]
                     ) -> dict:
    """Nearest centroid in standardized (theta, PAD) space + margin.

    Returns {"archetype", "expression" (gift|shadow), "polarity_margin",
    "margin" (gap to runner-up, i.e., confidence), "distances"}.
    Polarity: shadow-lean = high-N reactivity without D agency while
    distress dominates the mean PAD; otherwise gift. The rule is stated
    here, in data-adjacent code, not buried in a caller.
    """
    t = _centered(theta)
    mp = tuple(mean_pad)
    scored = []
    for name, (gift, shadow, direction, centroid) in ARCHETYPES.items():
        d_theta = math.sqrt(sum(
            (t[d] - direction.get(d, 0.0)) ** 2 for d in DOMAINS))
        d_pad = math.sqrt(sum(
            (a - b) ** 2 for a, b in zip(mp, centroid)))
        scored.append((d_theta + d_pad, name, gift, shadow))
    scored.sort(key=lambda r: r[0])
    (best_d, best, gift, shadow), second = scored[0], scored[1]
    n = float(theta.get("N", 3.0))
    distress = mp[0] < -0.2 and mp[2] < 0.0
    shadow_lean = (n >= 4.0 and mp[2] < -0.1) or (distress and n >= 3.5)
    margin = round(second[0] - best_d, 4)
    return {
        "archetype": best,
        "expression": "shadow" if shadow_lean else "gift",
        "expression_text": shadow if shadow_lean else gift,
        "polarity_margin": round(
            max(0.0, (n - 3.0) / 2.0) + (0.3 if distress else 0.0), 3),
        "margin": margin,
        "distances": {name: round(d, 3) for d, name, _g, _s in scored},
    }


# ── PersonArtifact: the editable deliverable ──────────────────────────

REQUIRED_TOP = ("name", "version", "theta", "memory", "affect", "policy")


def validate_artifact(payload: dict) -> list[str]:
    """List problems; empty = valid. Never raises (inspection API)."""
    problems = []
    if not isinstance(payload, dict):
        return ["artifact is not an object"]
    for key in REQUIRED_TOP:
        if key not in payload:
            problems.append("missing top-level key: %s" % key)
    if payload.get("version") != ARTIFACT_VERSION:
        problems.append("version %r != %r" % (
            payload.get("version"), ARTIFACT_VERSION))
    theta = payload.get("theta") or {}
    for d in DOMAINS:
        if d not in theta:
            problems.append("theta missing domain: %s" % d)
        elif not isinstance(theta[d], dict) or not theta[d]:
            problems.append("theta domain %s has no facets" % d)
    mem = payload.get("memory") or {}
    if "store" not in mem or "query" not in mem:
        problems.append("memory needs store + query pointer")
    aff = payload.get("affect") or {}
    for key in ("weights", "regulation", "display_rules", "decay_rates"):
        if key not in aff:
            problems.append("affect missing: %s" % key)
    pol = payload.get("policy") or {}
    if "kind" not in pol or "ref" not in pol:
        problems.append("policy needs kind + ref")
    return problems


class PersonArtifact:
    """One editable person: theta + memory pointer + affect params + policy.

    theta: {domain: {facet: 1..5}} (TraitVector.facets).
    memory: {"store": "pams"|..., "query": ..., "span": ..., "n_rows": ...}.
    affect: {"weights": PAD-temperament weights, "regulation": {strategy: params},
             "display_rules": {...}|None, "decay_rates": {situation-kind: rate}}.
    policy: {"kind": "distilled-tabular"|..., "ref": path-or-id,
             "trained_on": {...}}.
    """

    def __init__(self, name: str, theta: dict, memory: dict,
                 affect: dict, policy: dict):
        self.name = name
        self.theta = theta
        self.memory = memory
        self.affect = affect
        self.policy = policy

    def to_dict(self) -> dict:
        return {"name": self.name, "version": ARTIFACT_VERSION,
                "theta": self.theta, "memory": self.memory,
                "affect": self.affect, "policy": self.policy}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    @classmethod
    def from_dict(cls, payload: dict) -> "PersonArtifact":
        problems = validate_artifact(payload)
        if problems:
            raise ValueError("invalid person artifact: %s"
                             % "; ".join(problems))
        return cls(name=str(payload.get("name", "")),
                   theta=dict(payload["theta"]),
                   memory=dict(payload["memory"]),
                   affect=dict(payload["affect"]),
                   policy=dict(payload["policy"]))

    @classmethod
    def from_json(cls, text: str) -> "PersonArtifact":
        return cls.from_dict(json.loads(text))
