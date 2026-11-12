# coding: utf-8
"""PSYCHE Phase 0 — trait vector: OCEAN + Honesty-Humility, scored, editable.

A person's stable behavioral variance compresses to ~6 numbers (T1): the
Big Five (lexical-hypothesis discovery, cross-culturally replicated:
Goldberg; Costa & McCrae) plus HEXACO Honesty-Humility (sincerity,
fairness, greed-avoidance, modesty — the dimension that predicts
exploitation-relevant behavior the Big Five distributes away).

This module is the FIRST QUARTER of the person artifact (P1):
theta (this file) + episodic memory M (PAMS) + appraisal A (Phase 1) +
policy pi (Phase 3). Each is independently editable; the artifact is
their composition, not a prompt.

Item bank: 60 IPIP-STYLE items (10/domain, 5 positively + 5 negatively
keyed), each tagged to a facet. Facet lists follow Costa & McCrae (OCEAN)
and Ashton & Lee (HEXACO). Items are written in IPIP style (first-person,
behavioral, no double-barreled clauses); they are NOT claimed verbatim
from any copyrighted inventory (IPIP itself is public domain; these are
style-consistent analogues so the bank is self-contained and auditable).

Scoring: 1..5 Likert per item; reverse-keyed items scored as 6 - x.
Facet score = mean of its items; domain score = mean of its facets
(equal facet weight, so a facet with fewer items is not drowned).
Domain scores therefore live on 1..5 whatever the item counts.

Gate (tests/test_psyche_traits.py): synthetic respondents at known levels
recover their levels; keying is balanced 5/5 per domain; JSON round-trips
losslessly (this is the editable-artifact property: what you write is
what loads).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

DOMAINS = ("O", "C", "E", "A", "N", "H")

DOMAIN_NAMES = {
    "O": "openness",
    "C": "conscientiousness",
    "E": "extraversion",
    "A": "agreeableness",
    "N": "neuroticism",
    "H": "honesty_humility",
}

FACETS: dict[str, tuple[str, ...]] = {
    "O": ("imagination", "artistic_interests", "emotionality",
          "adventurousness", "intellect", "liberalism"),
    "C": ("self_efficacy", "orderliness", "dutifulness",
          "achievement_striving", "self_discipline", "cautiousness"),
    "E": ("friendliness", "gregariousness", "assertiveness",
          "activity_level", "excitement_seeking", "cheerfulness"),
    "A": ("trust", "morality", "altruism",
          "cooperation", "modesty", "sympathy"),
    "N": ("anxiety", "anger", "depression",
          "self_consciousness", "immoderation", "vulnerability"),
    "H": ("sincerity", "fairness", "greed_avoidance", "modesty"),
}

# (domain, facet, positively_keyed, text)
ITEMS: tuple = (
    # -- Openness --
    ("O", "imagination", True, "Have a vivid imagination"),
    ("O", "imagination", False, "Seldom get lost in thought"),
    ("O", "artistic_interests", True, "Enjoy the beauty of art and music"),
    ("O", "artistic_interests", False, "Do not care much for poetry or art"),
    ("O", "emotionality", True, "Feel others' emotions deeply"),
    ("O", "emotionality", False, "Rarely notice my own emotional reactions"),
    ("O", "adventurousness", True, "Prefer variety to routine"),
    ("O", "adventurousness", False, "Prefer familiar routines over new experiences"),
    ("O", "intellect", True, "Enjoy thinking about abstract ideas"),
    ("O", "intellect", False, "Avoid philosophical discussions"),
    # -- Conscientiousness --
    ("C", "self_efficacy", True, "Complete tasks successfully"),
    ("C", "self_efficacy", False, "Doubt my ability to get things done"),
    ("C", "orderliness", True, "Keep my belongings neat and organized"),
    ("C", "orderliness", False, "Leave my space messy"),
    ("C", "dutifulness", True, "Keep my promises"),
    ("C", "dutifulness", False, "Break rules when convenient"),
    ("C", "achievement_striving", True, "Work hard to accomplish my goals"),
    ("C", "achievement_striving", False, "Put off difficult tasks"),
    ("C", "self_discipline", True, "Carry out my plans"),
    ("C", "self_discipline", False, "Get distracted from important work"),
    # -- Extraversion --
    ("E", "friendliness", True, "Make friends easily"),
    ("E", "friendliness", False, "Keep others at a distance"),
    ("E", "gregariousness", True, "Love large parties"),
    ("E", "gregariousness", False, "Prefer time alone to social events"),
    ("E", "assertiveness", True, "Take charge in groups"),
    ("E", "assertiveness", False, "Let others lead the way"),
    ("E", "activity_level", True, "Am always busy"),
    ("E", "activity_level", False, "Prefer a slow pace"),
    ("E", "excitement_seeking", True, "Seek out thrilling experiences"),
    ("E", "excitement_seeking", False, "Avoid risks and excitement"),
    # -- Agreeableness --
    ("A", "trust", True, "Trust what people say"),
    ("A", "trust", False, "Suspect hidden motives in others"),
    ("A", "morality", True, "Would never cheat to get ahead"),
    ("A", "morality", False, "Take advantage of others when I can"),
    ("A", "altruism", True, "Love to help others"),
    ("A", "altruism", False, "Am indifferent to the needs of others"),
    ("A", "cooperation", True, "Avoid arguments and conflict"),
    ("A", "modesty", False, "Believe I am better than others"),
    ("A", "sympathy", True, "Feel sympathy for the suffering of others"),
    ("A", "sympathy", False, "Am not interested in other people's problems"),
    # -- Neuroticism (high = emotionally reactive) --
    ("N", "anxiety", True, "Worry about many things"),
    ("N", "anxiety", False, "Remain calm under pressure"),
    ("N", "anger", True, "Get angry easily"),
    ("N", "anger", False, "Rarely lose my temper"),
    ("N", "depression", True, "Often feel sad or blue"),
    ("N", "depression", False, "Seldom feel down"),
    ("N", "self_consciousness", True, "Feel self-conscious around others"),
    ("N", "self_consciousness", False, "Feel comfortable meeting new people"),
    ("N", "immoderation", True, "Give in to cravings easily"),
    ("N", "immoderation", False, "Control my impulses well"),
    # -- Honesty-Humility (high = sincere, fair, modest) --
    ("H", "sincerity", True, "Say what I mean, even when awkward"),
    ("H", "sincerity", False, "Flatter people to get what I want"),
    ("H", "fairness", True, "Would never accept a bribe"),
    ("H", "fairness", False, "Bend the rules for personal gain"),
    ("H", "greed_avoidance", True, "Am not interested in luxury or status"),
    ("H", "greed_avoidance", False, "Want to be rich and important"),
    ("H", "modesty", True, "Dislike showing off"),
    ("H", "modesty", False, "Think highly of my own abilities"),
    ("H", "sincerity", True, "Mean what I say in every conversation"),
    ("H", "fairness", False, "Give special treatment to important people"),
)


def _score_item(keyed: bool, response: int) -> float:
    if not 1 <= int(response) <= 5:
        raise ValueError("Likert responses must be integers 1..5")
    return float(response) if keyed else float(6 - response)


def score_responses(responses: dict[int, int] | list[int]) -> dict:
    """Score a full response set -> {domain: {facet: score}} on 1..5.

    Accepts {item_index: 1..5} or a 60-length list in bank order.
    """
    if isinstance(responses, list):
        if len(responses) != len(ITEMS):
            raise ValueError("need %d responses, got %d"
                             % (len(ITEMS), len(responses)))
        responses = dict(enumerate(responses))
    missing = [i for i in range(len(ITEMS)) if i not in responses]
    if missing:
        raise ValueError("missing responses for items %s" % missing[:10])
    facet_vals: dict[str, dict[str, list]] = {
        d: {f: [] for f in FACETS[d]} for d in DOMAINS}
    for i, (domain, facet, keyed, _text) in enumerate(ITEMS):
        facet_vals[domain][facet].append(_score_item(keyed, responses[i]))
    out: dict = {}
    for domain in DOMAINS:
        out[domain] = {}
        for facet, vals in facet_vals[domain].items():
            out[domain][facet] = round(sum(vals) / len(vals), 3) if vals else None
    return out


def domain_scores(facet_scores: dict) -> dict[str, float]:
    """Collapse facets to domain means (equal facet weight)."""
    out = {}
    for domain in DOMAINS:
        vals = [v for v in facet_scores.get(domain, {}).values()
                if isinstance(v, (int, float))]
        out[domain] = round(sum(vals) / len(vals), 3) if vals else None
    return out


@dataclass
class TraitVector:
    """An editable person-trait artifact (quarter 1 of 4: theta).

    facets: {domain: {facet: 1..5}}. Missing facets default to None and are
    excluded from domain means (partial profiles stay honest).
    """
    facets: dict = field(default_factory=dict)
    name: str = ""
    source: str = ""

    @classmethod
    def from_responses(cls, responses, name: str = "",
                       source: str = "ipip-style-60") -> "TraitVector":
        return cls(facets=score_responses(responses), name=name,
                   source=source)

    def domains(self) -> dict[str, float]:
        return domain_scores(self.facets)

    def to_dict(self) -> dict:
        return {"name": self.name, "source": self.source,
                "domains": list(DOMAINS), "facets": self.facets,
                "domain_scores": self.domains()}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    @classmethod
    def from_dict(cls, payload: dict) -> "TraitVector":
        return cls(facets=dict(payload.get("facets", {})),
                   name=str(payload.get("name", "")),
                   source=str(payload.get("source", "")))

    @classmethod
    def from_json(cls, text: str) -> "TraitVector":
        return cls.from_dict(json.loads(text))


def keying_balance() -> dict[str, dict[str, int]]:
    """Audit helper: + vs - counts per domain (gate pins 5/5)."""
    out = {}
    for domain in DOMAINS:
        plus = sum(1 for d, _f, k, _t in ITEMS if d == domain and k)
        minus = sum(1 for d, _f, k, _t in ITEMS if d == domain and not k)
        out[domain] = {"+": plus, "-": minus}
    return out


#: Live-store filename for a fitted person theta (domain-score snapshot JSON).
#: Home-based, like the PAMS live store (`~/.pams`) and the memory store
#: (`~/.azazael/memory_store.db`), so every checkout tree shares one person
#: record. This is a LIVE data path, never a project-root fallback: the
#: stale-root guard covers `Path.home() / "azazael"` (the old checkout),
#: while `.azazael` is the live store directory every tree already shares.
#:
#: Shape note: the file holds FITTED DOMAIN SCORES
#: (`{"name", "source", "domain_scores": {D: 1..5}, "mse", "n_used"}`), not a
#: TraitVector. A live fit (identify-style descent) yields domain levels only;
#: expanding them into facet detail would fabricate precision the fit never
#: measured, so the snapshot keeps exactly what was fitted. Readers must
#: range-check each score and drop non-numeric values.
THETA_SNAPSHOT_FILENAME = "person_theta.json"


def default_theta_path() -> Path:
    """Canonical live path for the fitted theta, overridable via PSYCHE_THETA."""
    override = os.environ.get("PSYCHE_THETA")
    if override:
        return Path(override)
    return Path.home() / ".azazael" / THETA_SNAPSHOT_FILENAME
