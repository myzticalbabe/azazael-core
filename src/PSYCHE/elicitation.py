# coding: utf-8
"""PSYCHE -- construct elicitation, so construct data can exist at all.

THE PROBLEM
-----------
`tools/psyche_fit_pams.py` exits 2 because no store anywhere in this project holds
a construct reading: memory records carry tags, types and importance levels, and
nothing that says how avoidant this person was on Tuesday. The affect -> construct
bridge was therefore fitted against a SYNTHETIC ground truth, which validates the
estimator and says nothing about people.

The missing piece is not an algorithm. It is an instrument: something that asks,
records the answer in a schema the fitter can read, and does so together with a
PAD reading -- because a bridge between affect and constructs needs both halves
measured in the same moment. Asking them at different times would confound the map
with the drift.

WHAT IS IN THE BATTERY, AND WHAT IS NOT
---------------------------------------
Ten constructs, three items each, answered on a 0-4 scale, with at least one
reverse-scored item per construct so acquiescence (agreeing with everything) is
detectable rather than silently inflating every score.

These items are WRITTEN FOR THIS PROJECT. They are not a validated psychometric
instrument, they have no norms, and no factor structure has been established for
them. Calling them "the IPIP" or "the DASS" would be a false provenance claim, and
borrowing a validated inventory's item text would be both a licensing problem and
a validity claim this project cannot support. What they are is a consistent,
documented, reverse-balanced self-report that maps onto the same ten nodes the
graph already has, so that data can start existing and later be replaced by
something with standing.

The three affect sliders are likewise direct self-report on the PAD axes, not
inferred from text: an inferred PAD cannot be used to fit the bridge, because the
fit would then be recovering the inference model's assumptions instead of a
relationship between affect and constructs.

Usage (see `tools/elicit.py` for the CLI):

    session = new_session()
    answers = {"approach_1": 3, ...}
    record = score_session(answers, pad=(-0.4, 0.6, -0.3), session=session)
    record.to_row()          # one JSON object, ready for storage
"""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: Bumped whenever item text or scoring changes. A record without a version is a
#: record whose meaning changes underneath the fit that used it.
INSTRUMENT_VERSION = "psyche-constructs-1"

#: A stored row is JSON: the values arrive as `object`, and every reader has to
#: narrow them before use. These three do the narrowing once, so a malformed row
#: degrades to an empty field instead of raising `AttributeError: 'dict' object
#: has no attribute 'items'` from inside a comprehension.

def _as_mapping(value: object) -> Dict[str, Any]:
    """A JSON object from a stored row, or {} when the row is malformed."""
    return value if isinstance(value, dict) else {}


def _as_sequence(value: object) -> List[Any]:
    """A JSON array from a stored row, or [] when the row is malformed."""
    return list(value) if isinstance(value, (list, tuple)) else []


def _triple(values: object) -> Tuple[float, float, float]:
    """PAD is three floats by definition; a malformed row fails here, loudly.

    The old code built `tuple(float(v) for v in ...)`, which is `tuple[float,
    ...]` -- a length the field does not promise -- and then suppressed the
    mismatch with a `type: ignore`. Unpacking checks the length at runtime too.
    """
    first, second, third = _as_sequence(values)
    return (float(first), float(second), float(third))

#: Answer scale. 0..4 with a stated midpoint, so "3" means the same thing in every
#: session and a shift in scale cannot masquerade as a shift in construct.
SCALE = (0, 1, 2, 3, 4)
SCALE_LABELS = ("not at all", "a little", "somewhat", "quite a lot", "extremely")
SCALE_MIDPOINT = 2

#: The ten nodes of the construct graph. Kept identical to
#: `network.DEFAULT_CONSTRUCTS` on purpose: a battery that measures nine of ten
#: nodes leaves one permanently unobservable, and the graph would then be a
#: picture of the instrument rather than of the person.
CONSTRUCTS: Tuple[str, ...] = (
    "threat_appraisal", "reward_expectancy", "avoidance", "approach", "arousal",
    "rumination", "self_worth", "social_support", "sleep_quality", "control_appraisal",
)


@dataclass(frozen=True)
class Item:
    """One question, one construct, one direction."""

    key: str
    construct: str
    text: str
    reverse: bool = False

    def to_dict(self) -> Dict[str, object]:
        return {"key": self.key, "construct": self.construct,
                "text": self.text, "reverse": self.reverse}


#: Three items per construct. The `reverse` item is not decoration: without it a
#: person who answers 4 to everything scores maximally on all ten nodes, and the
#: resulting graph is a straight line that no estimator can distinguish from a
#: real profile.
ITEMS: Tuple[Item, ...] = (
    Item("threat_1", "threat_appraisal", "Today I felt like something bad was about to happen."),
    Item("threat_2", "threat_appraisal", "Today my body was braced, as if for a shock."),
    Item("threat_3", "threat_appraisal", "Today I felt safe.", reverse=True),

    Item("reward_1", "reward_expectancy", "Today I expected something to go well."),
    Item("reward_2", "reward_expectancy", "Today I could picture a good outcome."),
    Item("reward_3", "reward_expectancy", "Today I expected to be let down.", reverse=True),

    Item("avoid_1", "avoidance", "Today I wanted to be left alone."),
    Item("avoid_2", "avoidance", "Today I avoided something I needed to do."),
    Item("avoid_3", "avoidance", "Today I reached out to someone first.", reverse=True),

    Item("approach_1", "approach", "Today I started something I had been putting off."),
    Item("approach_2", "approach", "Today I moved toward what I wanted."),
    Item("approach_3", "approach", "Today I stayed put rather than act.", reverse=True),

    Item("arousal_1", "arousal", "Today I felt keyed up."),
    Item("arousal_2", "arousal", "Today my thoughts were racing."),
    Item("arousal_3", "arousal", "Today I felt flat and slow.", reverse=True),

    Item("rumin_1", "rumination", "Today the same thought kept coming back."),
    Item("rumin_2", "rumination", "Today I went over a conversation again and again."),
    Item("rumin_3", "rumination", "Today my mind moved on easily.", reverse=True),

    Item("worth_1", "self_worth", "Today I felt like a decent person."),
    Item("worth_2", "self_worth", "Today I was on my own side."),
    Item("worth_3", "self_worth", "Today I felt like a failure.", reverse=True),

    Item("support_1", "social_support", "Today I had someone I could tell things to."),
    Item("support_2", "social_support", "Today I felt alone in what I was carrying.", reverse=True),
    Item("support_3", "social_support", "Today someone checked in on me."),

    Item("sleep_1", "sleep_quality", "Today I woke up rested."),
    Item("sleep_2", "sleep_quality", "Today I was running on empty.", reverse=True),
    Item("sleep_3", "sleep_quality", "Today I slept enough."),

    Item("control_1", "control_appraisal", "Today I had a say in what happened to me."),
    Item("control_2", "control_appraisal", "Today things happened to me rather than because of me.",
         reverse=True),
    Item("control_3", "control_appraisal", "Today I could steer my own day."),
)

#: Affect is measured on the same three axes the loop already carries, on the same
#: -1..1 range, so a record needs no rescaling before it reaches `activation_from_pad`.
AFFECT_ITEMS: Tuple[Tuple[str, str, str], ...] = (
    ("pleasure", "Right now, how pleasant does this moment feel?", "-1 = very unpleasant, +1 = very pleasant"),
    ("arousal", "Right now, how activated do you feel?", "-1 = flat, +1 = keyed up"),
    ("dominance", "Right now, how much is this moment yours to steer?", "-1 = not at all, +1 = entirely"),
)


def _by_construct() -> Dict[str, List[Item]]:
    grouped: Dict[str, List[Item]] = {c: [] for c in CONSTRUCTS}
    for item in ITEMS:
        grouped.setdefault(item.construct, []).append(item)
    return grouped


@dataclass
class Session:
    """The order the items were asked in, and why that is recorded.

    Order is randomised per session because a fixed order lets position effects
    accumulate into a construct score, and because the same items asked twice in
    the same order teach a person what the instrument "wants" -- which is the
    reflexivity problem this project is trying to measure, not commit.
    """

    order: List[str] = field(default_factory=list)
    seed: int = 0

    def to_dict(self) -> Dict[str, object]:
        return {"order": list(self.order), "seed": self.seed,
                "instrument": INSTRUMENT_VERSION}


def new_session(seed: Optional[int] = None) -> Session:
    rng = random.Random(seed)
    keys = [item.key for item in ITEMS]
    if seed is None:
        rng.shuffle(keys)
        return Session(order=keys, seed=-1)
    rng.shuffle(keys)
    return Session(order=keys, seed=int(seed))


@dataclass
class Record:
    """One completed session: a PAD reading and a construct vector from one moment."""

    taken_at: str
    pad: Tuple[float, float, float]
    constructs: Dict[str, float]
    answers: Dict[str, int]
    order: List[str]
    instrument: str = INSTRUMENT_VERSION
    note: str = ""
    #: The digest as it was WRITTEN, carried only by a record read back from a
    #: store. It exists because `digest` is a property that recomputes from the
    #: object in hand: comparing `record.digest` to `record.to_row()["digest"]`
    #: compares a value to itself and always passes -- an instrument that reports
    #: the health of whatever it is holding. The stored copy is the independent
    #: side of that comparison.
    stored_digest: str = ""

    @property
    def digest(self) -> str:
        """Content hash, so a stored record can be proven unedited later."""
        payload = repr((self.taken_at, self.pad, sorted(self.constructs.items()),
                        sorted(self.answers.items()), self.order, self.instrument))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_row(self) -> Dict[str, object]:
        """One JSON object. `digest` here is always the RECOMPUTED hash.

        Re-emitting a record read from a store therefore repairs its digest, and
        that is why `verify` compares `digest` to `stored_digest` instead: a row
        whose content was edited and then resaved would otherwise carry a
        matching hash and pass.
        """
        return {"taken_at": self.taken_at, "pad": list(self.pad),
                "constructs": dict(self.constructs), "answers": dict(self.answers),
                "order": list(self.order), "instrument": self.instrument,
                "note": self.note, "digest": self.digest}

    @classmethod
    def from_row(cls, row: Dict[str, object]) -> "Record":
        return cls(taken_at=str(row["taken_at"]),
                   pad=_triple(row.get("pad")),
                   constructs={str(k): float(v)
                               for k, v in _as_mapping(row.get("constructs")).items()},
                   answers={str(k): int(v)
                            for k, v in _as_mapping(row.get("answers")).items()},
                   order=[str(k) for k in _as_sequence(row.get("order"))],
                   instrument=str(row.get("instrument", INSTRUMENT_VERSION)),
                   note=str(row.get("note", "")),
                   stored_digest=str(row.get("digest", "")))


class IncompleteSession(ValueError):
    """Raised rather than scored: a partial session is not a shorter record.

    Scoring 24 of 30 items would silently renormalise the missing construct, and
    every session would then carry a small unmarked error in the direction of
    whatever was skipped.
    """


def score_session(answers: Dict[str, int], pad: Sequence[float], *,
                  taken_at: str, order: Optional[List[str]] = None,
                  note: str = "") -> Record:
    """Score a completed session onto the ten constructs, on -1..1.

    Reverse items are flipped before aggregation, then the construct mean is
    rescaled from 0..4 to -1..1. The midpoint maps to 0, so "no reading" and "a
    neutral reading" are the same value -- which is correct for a graph baseline
    and is stated in `network`'s docstring.
    """
    missing = [item.key for item in ITEMS if item.key not in answers]
    if missing:
        raise IncompleteSession("%d item(s) unanswered: %s"
                               % (len(missing), ", ".join(missing[:6])))
    bad = {k: v for k, v in answers.items()
           if not isinstance(v, int) or v not in SCALE}
    if bad:
        raise ValueError("answers outside %r: %r" % (SCALE, bad))
    if len(pad) != 3:
        raise ValueError("pad needs three values (pleasure, arousal, dominance)")

    grouped = _by_construct()
    constructs: Dict[str, float] = {}
    for construct, items in grouped.items():
        if not items:
            raise ValueError("construct %r has no items" % construct)
        total = 0.0
        for item in items:
            value = float(answers[item.key])
            if item.reverse:
                value = float(max(SCALE) + min(SCALE) - value)
            total += value
        mean = total / len(items)
        constructs[construct] = round((mean - SCALE_MIDPOINT) / SCALE_MIDPOINT, 4)

    return Record(taken_at=taken_at,
                  pad=(float(pad[0]), float(pad[1]), float(pad[2])),
                  constructs=constructs, answers=dict(answers),
                  order=list(order or []), note=note)


def homogeneity(records: Sequence[Record]) -> Dict[str, float]:
    """Agreement of a person's reverse-scored items, per construct.

    A reverse item is the instrument's internal control. If someone answers every
    item 4, the reverse items flip to 0 and the construct score lands near the
    midpoint -- which looks like a middling reading and is really an unreadable
    one. This function makes that visible: a construct whose forward and reverse
    means are far apart is flagged, and a session with several such constructs
    should be re-taken rather than fitted.
    """
    if not records:
        return {}
    totals: Dict[str, List[float]] = {}
    for record in records:
        for construct, items in _by_construct().items():
            forward = [record.answers.get(i.key, SCALE_MIDPOINT)
                       for i in items if not i.reverse and i.key in record.answers]
            reverse = [max(SCALE) + min(SCALE) - record.answers.get(i.key, SCALE_MIDPOINT)
                       for i in items if i.reverse and i.key in record.answers]
            if forward and reverse:
                gap = abs(sum(forward) / len(forward) - sum(reverse) / len(reverse))
                totals.setdefault(construct, []).append(gap)
    return {c: round(sum(v) / len(v), 3) for c, v in totals.items()}


def as_pad_construct_pairs(records: Sequence[Record]) -> List[Tuple[Tuple[float, float, float],
                                                                    Dict[str, float]]]:
    """The shape `tools/fit_affect_bridge.py --from-records` consumes."""
    return [(r.pad, r.constructs) for r in records if r.constructs]
