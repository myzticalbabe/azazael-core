# coding: utf-8
"""PSYCHE Phase 6 -- REALTIME THERAPEUTIC AGENTS: the loop a stance is chosen in.

WHAT THIS IS, AND WHAT IT REFUSES TO BE
---------------------------------------
A realtime agent here is the JITAI shape from digital mental health (Nahum-Shani
et al., "Just-in-Time Adaptive Interventions (JITAIs) in Mobile Health", Annals
of Behavioral Medicine 2018): SENSE a state, DECIDE whether and how to intervene
now, DELIVER a bounded dose, and LEARN from what happened. Three of those four
words are doing real work:

  * SENSE uses the person's own graph and affect model, never a population prior.
  * "Whether" is a real decision. The JITAI literature is explicit that
    intervening at the wrong moment is worse than silence -- a person outside
    their window of tolerance cannot learn a skill, so a skills intervention
    delivered at arousal 0.9 teaches them that therapy does not work. So the loop
    has a RECEPTIVITY gate and a REGULATE stance that fires before SKILLS.
  * DELIVER is bounded: one stance, one short message, no stacked advice.

It is NOT a therapist, and the code says so rather than implying it. It does not
diagnose, does not name a disorder, and cannot replace a human -- on any elevated
risk signal it stops being an intervention engine and becomes a router to people.
That routing is the FIRST branch of `turn()`, before any stance is scored, and
`test_psyche_rt_agent` asserts it is exclusive. A safety check that can be
outranked by a utility score is not a safety check.

THE FOUR STANCES AND WHY THESE FOUR
-----------------------------------
Grounded in what the process literature actually measures, not in a modality:

  SAFETY      risk triage fired; deliver a resource, deliver nothing else.
  VALIDATE    emotional processing needs the experience acknowledged before any
              change work; skips straight past reframing resistance.
  REGULATE    arousal above the learning window; downregulate first.
  REFRAME     arousal workable and an appraisal is driving the distress --
              re-appraisal is the highest-value target when the belief, not the
              state, is what is stuck.
  SKILLS      workable state, no dominant misappraisal, avoidance is the active
              node -- approach/activation work.
  CONSOLIDATE nothing acute: name what worked so it is retrievable next time
              (this is the same move as `intuition.py` compiling a prototype).

Selection is deterministic: a utility per applicable stance, argmax, name
tiebreak. No sampling, so a session is reproducible and its audit is checkable.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from PSYCHE.affect import (appraise_event, clamp, emotion_to_pad, express,
                           pad_blend, pad_decay)
from PSYCHE.intuition import DEFAULT_THRESHOLD, IntuitionIndex, Reading
from PSYCHE import constructions
from PSYCHE.network import PersonNetwork, activation_from_pad, bridge_name

#: Crisis vocabulary. Deliberately explicit and deliberately narrow: this is a
#: ROUTER, so it must fire on unambiguous statements of intent and must not fire
#: on ordinary distress language. The negative-control test exercises both sides.
CRISIS_CUES: Tuple[str, ...] = (
    "kill myself", "end my life", "ending my life", "suicide", "suicidal",
    "want to die", "wish i was dead", "better off dead", "not want to be here",
    "hurt myself", "hurting myself", "self harm", "self-harm", "cut myself",
    "overdose", "no reason to live", "can't go on", "cannot go on",
)

#: Words that look like crisis vocabulary but are not, and which would produce a
#: false positive if matched by substring: "killing time", "die of embarrassment".
CRISIS_EXCLUSIONS: Tuple[str, ...] = (
    "kill time", "killing time", "die of embarrassment", "dying to know",
    "could die laughing", "dead tired", "deadline",
    # Added 2026-11-01 with the coherence channel: "this traffic is going to be
    # the death of me" brings self + cessation + intent together and is still an
    # idiom. A fixed idiom is a MASK, not a lowered score -- the words are present
    # and mean the opposite, which is exactly the case exclusions exist for.
    "the death of me", "death of me",
)

#: Arousal above this is outside the window in which skills are learnable.
LEARNING_WINDOW_AROUSAL = 0.72

#: INDIRECT intent, as patterns rather than exact strings. Added 2026-11-01
#: after `tools/triage_eval.py` measured recall 0.76 on the dev corpus: all three
#: misses were euphemisms ("not be around anymore", "end it all", "rather not
#: wake up"), which is a MECHANISM gap -- an exact-phrase list only ever catches
#: the phrasings it was written around, and people do not speak in those. These
#: are families, so the next paraphrase is more likely to fall inside one.
CRISIS_PATTERNS: Tuple[Tuple[str, str], ...] = (
    ("indirect_absence", r"\b(not|no longer)\s+(be\s+around|be\s+here|wake\s+up)\b"),
    ("end_it", r"\bend\s+(it\s+all|things|my\s+life)\b"),
    ("better_off_gone", r"\bbetter\s+off\s+(dead|without\s+me|gone)\b"),
    ("no_point", r"\b(no|not\s+see\s+the)\s+point\s+(in|to)?\s*(living|going\s+on|carrying\s+on)\b"),
)

#: CONCEPT LEXEMES for the coherence channel. Added 2026-11-01, replacing further
#: pattern tuning. The measured reason: the pattern set scored recall 1.00 on the
#: dev corpus after fitting and 0.00 on a fresh disjoint holdout written before
#: the fitting -- an exact-phrase list generalises at about zero, because people
#: do not speak in the phrasings it was written around. These are item FAMILIES,
#: each traceable to a published instrument rather than to a sentence I happened
#: to have in front of me:
#:
#:   cessation / desire / plan  -- Columbia Suicide Severity Rating Scale item
#:                                 groups (ideation, intent, method/plan/prep)
#:   hopeless / no_future / no_exit -- Beck Hopelessness Scale, SPS (no future
#:                                 thinking), entrapment items
#:   burden                    -- Interpersonal Needs Questionnaire (PB)
#:   tether                    -- Reasons for Living Inventory
#:   self_harm                 -- deliberate self-harm, distinct from ideation
#:
#: Detection is COHERENCE, not a keyword: crisis requires several families to be
#: present and connected in one statement. "I could kill for a coffee" shares the
#: cessation family with "I want to die" and shares nothing else, and that is the
#: whole discrimination.
CONCEPTS: Dict[str, Tuple[str, ...]] = {
    "self": (r"\bi\b", r"\bme\b", r"\bmy\b", r"\bmyself\b", r"\bi'm\b",
             r"\bi've\b", r"\bi'd\b", r"\bi'll\b", r"\bmy own\b"),
    "cessation": (r"\bdie\b", r"\bdead\b", r"\bdying\b", r"\bdeath\b",
                  # "kill" counts only where it is SELF-directed. "kill me", "kill
                  # this presentation" and "my boss will kill me" are the dominant
                  # first-person objects and every one of them is hyperbole. This
                  # is a reflexivity rule (English), added 2026-11-01 after two
                  # holdout3 FPs; it is not a threshold tuned on those rows.
                  r"\bkill\b(?!\s+(?:me|him|her|them|it|this|that)\b)",
                  r"\bkilling\b(?!\s+(?:me|him|her|them|it|this|that)\b)",
                  r"\bsuicid\w*", r"\boverdos\w*",
                  r"\bend\b", r"\bending\b", r"\bstop\b", r"\bstopping\b",
                  r"\bstopped\b", r"\bdisappear\w*", r"\bvanish\w*",
                  r"\bgon(e|na)\b", r"\bdo\s+it\b", r"\bdoing\s+it\b",
                  r"\bgo\s+on\b", r"\bwake\s+up\b", r"\bexit\b", r"\baway\b"),
    "life": (r"\blive\b", r"\bliving\b", r"\balive\b", r"\blife\b", r"\bhere\b"),
    "desire": (r"\bwant\w*", r"\bwish\w*", r"\brather\b", r"\bwould\b",
               r"\bready\b", r"\bdecided\b", r"\bplan(ned|ning)?\b",
               r"\bthink(ing)?\b", r"\bidea\b", r"\bconsidering\b",
               r"\btempted\b", r"\bcoming\s+back\b", r"\bgoing\s+to\b",
               r"\bwork(ing)?\s+out\b", r"\bkeep\b"),
    "hopeless": (r"\bnothing\b", r"\bpointless?\b", r"\bhopeless\b",
                 r"\bnever\b", r"\btired\b", r"\bexhausted\b", r"\btrapped\b",
                 r"\bstuck\b", r"\bcan't\b", r"\bcannot\b", r"\bdon't\s+see\b",
                 r"\bdoes\s+not\s+matter\b", r"\bno\s+point\b", r"\bempty\b",
                 r"\bmeaningless\b", r"\bdone\b", r"\bpretending\b"),
    "no_future": (r"\bnothing\s+left\b", r"\bno\s+(reason|future|hope)\b",
                  r"\bnothing\s+to\s+live\b", r"\bnot\s+be\s+alive\b",
                  r"\bhow\s+I\b", r"\bmuch\s+longer\b"),
    "no_exit": (r"\bway\s+out\b", r"\bno\s+escape\b", r"\bescape\b",
                r"\bno\s+end\b", r"\bout\s+of\s+this\b"),
    "burden": (r"\bbetter\s+off\b", r"\bburden\b", r"\brelieved\b",
               r"\bwithout\s+me\b", r"\bdragging\b", r"\bproblem\b",
               r"\brelief\b"),
    "plan_obj": (r"\bnote\b", r"\bletter\b", r"\bmethod\b", r"\bpills?\b",
                 r"\brope\b", r"\bgun\b", r"\bmedication\b",
                 r"\bgiving\s+(my\s+)?(things|stuff)\s+away\b",
                 r"\bthings\s+away\b", r"\blast\s+night\b", r"\bdate\s+picked\b",
                 r"\bstopped\s+taking\b", r"\bprepared\b"),
    "tether": (r"\bkeeping\s+me\s+here\b", r"\bonly\s+(thing|reason)\b",
               r"\breason\s+to\s+stay\b", r"\bkeeping\s+me\s+alive\b"),
    "self_harm": (r"\b(hurt|hurting|harm|harming|cut|cutting)\s+myself\b",
                  r"\bself[-\s]?harm\w*", r"\bself[-\s]?injur\w*",
                  r"\bcutting\b"),
}

#: Coherence rules. Each is (name, required families, weight). A family noted
#: with a leading '!' must be ABSENT. Score >= CRISIS_SCORE is `crisis`. Weights
#: are ordinal, not calibrated probabilities -- three families agreeing is not
#: twice as dangerous as two, and pretending it is would be a false precision.
COHERENCE_RULES: Tuple[Tuple[str, Tuple[str, ...], float], ...] = (
    ("ideation_intent", ("self", "cessation", "desire"), 2.0),
    ("preparation", ("self", "plan_obj", "cessation"), 2.0),
    ("plan_artifact", ("self", "plan_obj", "desire"), 1.8),
    ("self_harm", ("self", "self_harm"), 2.0),
    ("no_future", ("self", "no_future"), 1.5),
    ("entrapment", ("self", "hopeless", "no_exit"), 1.5),
    ("burden", ("self", "burden", "desire"), 1.5),
    ("cessation_hopeless", ("self", "cessation", "hopeless"), 1.5),
    ("tether", ("self", "tether"), 1.5),
    ("implicit_no_future", ("!self", "no_future", "life"), 1.5),
    ("cessation_only", ("self", "cessation"), 0.5),
)

#: Score at which the coherence channel calls `crisis`. Set so that a single
#: family in isolation (0.5) never reaches it: "I could kill for a coffee" is
#: indistinguishable from "I want to die" on the cessation family alone, and the
#: second family is what separates them.
CRISIS_SCORE = 1.5

#: Attribution guards. A crisis word in someone else's story, in the past, or
#: inside a quotation is NOT this person's intent, and treating it as one is how
#: a router cries wolf until people turn it off. Each guard DOWNGRADES to
#: `monitor` rather than discarding: the mention is still worth not ignoring.
THIRD_PERSON_MARKERS: Tuple[str, ...] = (
    "my mother", "my father", "my brother", "my sister", "my son", "my daughter",
    "my friend", "a friend", "my partner", "my wife", "my husband", "my ex",
    "my colleague", "a colleague", "someone i know", "he tried", "she tried",
    "they tried", "his suicide", "her suicide",
    # Added 2026-11-01 after a holdout FP: "a client told me they were suicidal
    # last month, I referred them on" scored as SELF because the list had no
    # clinical-register entries -- and a clinician's register is this system's
    # actual deployment context. "told me they" is the cue that carries it.
    "a client", "my client", "a patient", "my patient", "a student",
    "my student", "told me they", "told me he", "told me she",
)
HISTORICAL_MARKERS: Tuple[str, ...] = (
    "years ago", "ten years", "last year", "in 2015", "in 2016", "in 2019",
    "in 2020", "in 2015", "when i was", "used to", "have been clean",
    "back then", "since my last", "it is behind me", "behind me now",
)
QUOTATION_MARKERS: Tuple[str, ...] = (
    '"', "\u201c", "\u201d", "lyric", "the song", "a song", "quote", "the novel",
    "the character",
)
#: First-person intent overrides every guard: "I want to die" is this person's
#: statement even if the sentence also mentions a relative.
FIRST_PERSON_MARKERS: Tuple[str, ...] = (
    "i want to", "i have been thinking", "i am going to", "i will", "i keep",
    "i have decided", "i plan", "i am done",
)

#: Risk levels, ordered. Only `low` permits a non-safety stance.
RISK_LEVELS: Tuple[str, ...] = ("low", "monitor", "elevated", "crisis")

#: The stances that do not ask the person to change anything. On elevated risk
#: only these are eligible -- see the cap in `TherapeuticLoop._select`.
LOW_DOSE_STANCES: Tuple[str, ...] = ("validate", "regulate", "consolidate")

#: Per-stance message templates. Short by design: a realtime channel punishes a
#: wall of text, and a long message is advice regardless of its hedging. `{focus}`
#: is the construct the graph named, so the message is about THIS person's state.
STANCE_TEMPLATES: Dict[str, str] = {
    "safety": ("What you just said matters more than anything else in this "
               "conversation. Please reach a person now: 988 (Suicide & Crisis "
               "Lifeline, US) or your local emergency number. I am staying here "
               "with you, and I am not the right kind of help for this."),
    "validate": ("That sounds genuinely hard, and it makes sense that {focus} is "
                 "where it lands. I am not going to move you past it yet."),
    "regulate": ("Before anything else, let us bring the intensity down a notch. "
                 "One slow breath out, longer than the breath in, twice. "
                 "Nothing to solve yet."),
    "reframe": ("The story running right now is doing a lot of the work. "
                "What is one piece of evidence that sits outside it?"),
    "skills": ("{focus} is the thing asking for attention. What is the smallest "
               "step toward it that you would actually do today?"),
    "consolidate": ("Notice what you just did: you stayed with {focus} instead of "
                    "leaving it. That is the part worth remembering."),
}


@dataclass
class Triage:
    """The risk verdict. `reasons` is never empty -- an empty list is not a pass."""

    risk: str
    reasons: List[str] = field(default_factory=list)
    matched: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"risk": self.risk, "reasons": list(self.reasons),
                "matched": list(self.matched)}


@dataclass
class TurnResponse:
    """Everything one realtime turn decided, with the evidence for each part."""

    stance: str
    message: str
    focus: str
    risk: Triage
    pad: Tuple[float, float, float]
    reading: Optional[Reading]
    latency_ms: float
    within_budget: bool
    rationale: str
    activation: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"stance": self.stance, "message": self.message, "focus": self.focus,
                "risk": self.risk.to_dict(),
                "pad": [round(v, 4) for v in self.pad],
                "reading": self.reading.to_dict() if self.reading else None,
                "latency_ms": round(self.latency_ms, 3),
                "within_budget": self.within_budget, "rationale": self.rationale}


def _attribution(lowered: str) -> str:
    """Whose risk is this sentence describing? self / other / historical / quotation.

    Order matters. A quotation is a quotation even when it is also historical;
    the narrowest guard wins, because each one only ever downgrades.
    """
    if any(marker in lowered for marker in QUOTATION_MARKERS):
        return "quotation"
    if any(marker in lowered for marker in HISTORICAL_MARKERS):
        return "historical"
    if any(marker in lowered for marker in THIRD_PERSON_MARKERS):
        return "other"
    return "self"


#: Tokens that negate a cessation word when they sit just before it.
NEGATION_TOKENS: Tuple[str, ...] = (
    "not", "never", "no", "n't", "nobody", "denied", "hardly", "rarely",
    "no longer", "absolutely not",
)


def _negated(text: str, start: int, window: int = 24) -> bool:
    """True when the tokens immediately before `start` negate it.

    Scope is deliberately short: "I have not slept, I want to die" must still
    score the cessation word, so a negation anywhere in the sentence is wrong.
    """
    before = text[max(0, start - window):start].lower()
    trimmed = before.rstrip()
    for token in NEGATION_TOKENS:
        if re.search(r"(?:^|\W)%s\W{0,12}$" % re.escape(token), trimmed):
            return True
    return False


def _coherence(lowered: str) -> Tuple[float, List[str]]:
    """Score how many concept families a statement brings together.

    Returns (score, rule names that fired). Families are detected by regex so
    that "die" does not match "diet" and "self-harm" also matches "self-harming".

    NEGATION SCOPE applies to the cessation family only, because that is where
    denial is common and literal: "I am not suicidal" states the ABSENCE of the
    thing, and scoring it as presence inverts the reading. The window is the few
    tokens immediately before the match -- "not sleeping" before an unrelated
    cessation word is not a denial of it.
    """
    present = set()
    for family, patterns in CONCEPTS.items():
        joined = "|".join(patterns)
        if family == "cessation":
            if any(not _negated(lowered, m.start())
                   for m in re.finditer(joined, lowered)):
                present.add(family)
            continue
        if re.search(joined, lowered):
            present.add(family)
    total = 0.0
    hits: List[str] = []
    for name, families, weight in COHERENCE_RULES:
        if all((f[1:] not in present) if f.startswith("!") else (f in present)
               for f in families):
            total += weight
            hits.append(name)
    return total, hits


def triage(text: str, activation: Optional[Dict[str, float]] = None) -> Triage:
    """Route risk before anything else happens in the turn.

    Two independent channels, kept apart on purpose:

      TEXT  statements of suicidal or self-harm intent -> `crisis`, with an
            ATTRIBUTION guard: the same words in a relative's story, in the
            past, or inside a quotation downgrade to `monitor` instead.
      STATE the isolated-collapse pattern (low self-worth + high avoidance +
            high rumination) -> `elevated`, with no text needed.

    The text channel checks exclusions first, so "killing time" and "dead tired"
    do not fire; that is the negative control this function ships with. Indirect
    phrasing is caught by CRISIS_PATTERNS, added after measurement showed the
    exact-phrase list missing every euphemism it was tested on.
    """
    lowered = (text or "").lower()
    matched = [cue for cue in CRISIS_CUES if cue in lowered]
    score, hits = _coherence(lowered)
    built = constructions.analyse(lowered)
    # Precedence between the two lexical-ish channels: the one that read the
    # OBJECT of the sentence wins. "the only thing keeping me in this job" scores
    # on the coherence family `tether` and cannot see the pension clause; the
    # construction pass found that clause and recorded why it did not count. Two
    # mechanisms disagreeing is a real ambiguity, and resolving it toward silence
    # here is safe only because the construction channel is the more specific one
    # -- it fires on far fewer sentences and its evidence is auditable.
    coerced = 0.0
    if score >= CRISIS_SCORE and built.benign_evidence and not built.fires:
        # NOT appended to `matched`: that list IS the "something matched" signal, so
        # recording a suppression in it made the suppression itself fire. Notes go
        # to `coerced`, which is only ever reported in the reasons.
        coerced = score
        score = 0.0
    # Third channel: constructions. Added because two lexical mechanisms were
    # measured against fresh holdouts and neither generalised (1.00 -> 0.00, then
    # 0.83 -> 0.18). The misses had no crisis vocabulary at all -- "no version of
    # next year that has me in it" -- because the signal is in the FRAME and its
    # slot fills, not in the tokens. A construction that is about the speaker
    # escalates directly: its subject was resolved from the frame, which is a
    # better attribution decision than the blanket guard below.
    if built.fires:
        names = sorted({f.name for f in built.frames if f.subject == constructions.SELF})
        matched.append("construction:%s" % "+".join(names))
        return Triage("crisis",
                      ["risk construction %s (score %.1f): %s"
                       % ("+".join(names), built.score,
                          "%s=%r" % (built.frames[0].name, built.frames[0].evidence))],
                      matched)
    for name, pattern in CRISIS_PATTERNS:
        if re.search(pattern, lowered):
            matched.append("pattern:%s" % name)
    if score >= CRISIS_SCORE:
        matched.append("coherence:%.1f" % score)
    excluded = [cue for cue in CRISIS_EXCLUSIONS if cue in lowered]
    if coerced and not matched:
        return Triage("low",
                      ["coherence would have scored %.1f, but the construction pass "
                       "found a slot fill that makes this text about something else"
                       % coerced], matched)
    if matched and not excluded:
        who = _attribution(lowered)
        first_person = any(marker in lowered for marker in FIRST_PERSON_MARKERS)
        if who == "self" or first_person:
            why = ("coherent intent across %s (score %.1f)" % ("+".join(hits), score)
                   if score >= CRISIS_SCORE else
                   "explicit intent language: %r" % matched[0])
            return Triage("crisis", [why], matched)
        return Triage(
            "monitor",
            ["intent language %r attributed to %s: not this person's stated intent, "
             "still worth not ignoring" % (matched[0], who)], matched)
    reasons: List[str] = []
    if excluded and matched:
        reasons.append("crisis vocabulary present but excluded as idiom: %r" % excluded[0])
    active = activation or {}
    low_worth = active.get("self_worth", 0.0) <= -0.6
    high_avoid = active.get("avoidance", 0.0) >= 0.6
    high_rumination = active.get("rumination", 0.0) >= 0.6
    if low_worth and high_avoid and high_rumination:
        return Triage("elevated",
                      ["isolated-collapse pattern: self_worth<=-0.6, avoidance>=0.6, "
                       "rumination>=0.6 in the same reading"], matched)
    if sum((low_worth, high_avoid, high_rumination)) >= 2:
        return Triage("monitor",
                      ["two of three collapse markers present; not elevated without "
                       "the third"], matched)
    if not reasons:
        reasons.append("no intent language and no collapse markers in this reading")
    return Triage("low", reasons, matched)


def _triple(values: Tuple[float, float, float]) -> Tuple[float, float, float]:
    """Coerce a PAD triple, keeping its length.

    `tuple(float(v) for v in pad)` is `tuple[float, ...]`: a length no field or
    downstream signature promises, which is why the two assignments that used it
    needed a `type: ignore` that the checker went on reporting through. Unpacking
    into three names states the length the type already required.
    """
    first, second, third = (float(v) for v in values)
    return (first, second, third)


class TherapeuticLoop:
    """The realtime loop over one person's artifacts.

    Held state: the affect state (PAD), the network, the intuition index, and a
    bounded audit of recent turns. Nothing else -- a loop that keeps more becomes
    a store, and this is supposed to be a loop.
    """

    def __init__(self, network: PersonNetwork, index: IntuitionIndex,
                 pad: Tuple[float, float, float] = (0.0, 0.0, 0.0),
                 baseline: Tuple[float, float, float] = (0.0, 0.0, 0.0),
                 decay_rate: float = 0.15, budget_ms: float = 50.0,
                 threshold: float = DEFAULT_THRESHOLD,
                 audit_limit: int = 64) -> None:
        self.network = network
        self.index = index
        self.pad = _triple(pad)
        self.baseline = _triple(baseline)
        self.decay_rate = float(decay_rate)
        self.budget_ms = float(budget_ms)
        self.threshold = float(threshold)
        self.audit_limit = int(audit_limit)
        self._audit: List[TurnResponse] = []
        self._turn_count = 0

    # -- the turn ---------------------------------------------------------
    def turn(self, text: str = "", appraisal: Optional[dict] = None,
             dt_s: float = 1.0, observe: bool = True) -> TurnResponse:
        """One realtime turn: sense, decide, deliver, record.

        Order is fixed and matters. Affect decays toward the person's own
        baseline FIRST (so state is current), appraisal blends in SECOND, the
        graph is updated THIRD, triage runs FOURTH on the fresh activation --
        and if triage says crisis, the turn ends here with a safety stance and no
        other candidate is ever scored.
        """
        started = time.perf_counter()
        self._turn_count += 1

        self.pad = pad_decay(self.pad, self.baseline, self.decay_rate, dt_s)

        families: List[str] = []
        if appraisal:
            ranked = appraise_event(
                desirability=float(appraisal.get("desirability", 0.0)),
                likelihood=float(appraisal.get("likelihood", 1.0)),
                confirmed=appraisal.get("confirmed"),
                effort=float(appraisal.get("effort", 0.0)),
                unexpectedness=float(appraisal.get("unexpectedness", 0.0)),
                for_other=bool(appraisal.get("for_other", False)),
                liking_other=float(appraisal.get("liking_other", 0.0)),
            )
            if ranked:
                top_family, intensity, _basis = ranked[0]
                families = [top_family]
                self.pad = pad_blend(
                    self.pad, emotion_to_pad(top_family, intensity),
                    weight=clamp(0.35 * intensity, 0.0, 1.0))
                if top_family in ("distress", "fears_confirmed", "sadness"):
                    families.append("sadness")
                elif top_family in ("fear", "anxiety", "hope"):
                    families.append("anxiety")

        activation = activation_from_pad(self.pad, families)
        # Stamp the map that produced this activation, so a graph is never read
        # as if the hand map and the fitted regression were the same claim.
        self.network.activation_source = bridge_name()
        if observe:
            self.network.observe(activation, at_s=float(self._turn_count))

        risk = triage(text, activation)
        reading = self.index.read_or_think(activation, threshold=self.threshold)

        if risk.risk == "crisis":
            stance, focus, rationale = "safety", "this moment", \
                "triage=crisis on intent language; every other stance is barred"
        else:
            stance, focus, rationale = self._select(activation, risk, reading)

        message = STANCE_TEMPLATES[stance].format(focus=focus)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        response = TurnResponse(
            stance=stance, message=message, focus=focus, risk=risk, pad=self.pad,
            reading=reading, latency_ms=elapsed_ms,
            within_budget=elapsed_ms <= self.budget_ms, rationale=rationale,
            activation=activation)
        self._audit.append(response)
        if len(self._audit) > self.audit_limit:
            self._audit = self._audit[-self.audit_limit:]
        return response

    # -- stance selection -------------------------------------------------
    def _select(self, activation: Dict[str, float], risk: Triage,
                reading: Optional[Reading]) -> Tuple[str, str, str]:
        """Score every applicable stance, return the argmax with its reason.

        Receipts are the point: `rationale` names the numbers that decided, so a
        supervisor (or the person) can disagree with the decision by disagreeing
        with a specific quantity rather than with the system as a whole.
        """
        _p, arousal, dominance = self.pad
        focus = self._focus(activation)
        threat = activation.get("threat_appraisal", 0.0)
        avoidance = activation.get("avoidance", 0.0)
        approach = activation.get("approach", 0.0)
        rumination = activation.get("rumination", 0.0)
        worth = activation.get("self_worth", 0.0)

        scores: List[Tuple[str, float, str]] = []

        # VALIDATE: high negative affect and high threat, regardless of arousal.
        # Acknowledging first is cheap and skipping it poisons everything after.
        neg_affect = max(0.0, -self.pad[0])
        if neg_affect >= 0.25 or threat >= 0.35:
            scores.append(("validate", 0.55 + 0.4 * neg_affect + 0.2 * threat,
                           "neg_affect=%.2f threat=%.2f" % (neg_affect, threat)))

        # REGULATE: outside the learning window. Beats SKILLS and REFRAME by
        # construction, because neither lands in a dysregulated state.
        if arousal >= LEARNING_WINDOW_AROUSAL:
            scores.append(("regulate",
                           0.9 + 0.6 * (arousal - LEARNING_WINDOW_AROUSAL),
                           "arousal=%.2f >= learning window %.2f"
                           % (arousal, LEARNING_WINDOW_AROUSAL)))

        # REFRAME: workable arousal, threat is doing the work, and there is enough
        # agency to hold a reappraisal without collapsing.
        if arousal < LEARNING_WINDOW_AROUSAL and threat >= 0.3 and dominance > -0.5:
            scores.append(("reframe", 0.6 + 0.5 * threat + 0.2 * dominance,
                           "threat=%.2f dominance=%.2f below learning window"
                           % (threat, dominance)))

        # SKILLS: avoidance is the active node with a workable state.
        if avoidance >= 0.3 and arousal < LEARNING_WINDOW_AROUSAL:
            scores.append(("skills", 0.5 + 0.6 * avoidance - 0.3 * max(0.0, rumination),
                           "avoidance=%.2f rumination=%.2f" % (avoidance, rumination)))

        # CONSOLIDATE: nothing acute and something actually moved.
        if not scores or (neg_affect < 0.25 and approach >= 0.3 and worth >= 0.0):
            scores.append(("consolidate", 0.45 + 0.3 * approach + 0.2 * worth,
                           "approach=%.2f self_worth=%.2f, nothing acute"
                           % (approach, worth)))

        if not scores:
            scores.append(("validate", 0.4, "fallback: no stance predicate matched"))

        # A monitor-level risk reading outranks ordinary work: change work is
        # damped, but the stance set is unchanged.
        if risk.risk == "monitor":
            scores = [(name, score - (0.15 if name in LOW_DOSE_STANCES else 0.0), why)
                      for name, score, why in scores]
        # ELEVATED is a different claim and gets a different mechanism: the dose
        # is CAPPED, not discounted. An early version subtracted 0.15 here and
        # reframe still won at 0.75 versus validate at 0.67 -- a penalty that can
        # be outbid is not a cap, and pushing reappraisal at someone in the
        # isolated-collapse pattern is exactly the mis-timing the JITAI
        # literature warns about. When no low-dose stance applies, the damping
        # is the fallback rather than the primary mechanism.
        dose_note = ""
        if risk.risk == "elevated":
            low_dose = [row for row in scores if row[0] in LOW_DOSE_STANCES]
            if low_dose:
                scores = low_dose
                dose_note = "; dose capped to low-dose stances on elevated risk"
            else:
                scores = [(name, score - 0.15, why) for name, score, why in scores]
                dose_note = "; no low-dose stance applied, change work damped 0.15"

        name, score, why = max(scores, key=lambda row: (row[1], row[0]))
        path = reading.path if reading else "none"
        proto = reading.prototype if reading and reading.prototype else "no-prototype"
        return name, focus, ("utility=%.2f (%s)%s; risk=%s; intuition=%s/%s"
                             % (score, why, dose_note, risk.risk, path, proto))

    def _focus(self, activation: Dict[str, float]) -> str:
        """The construct the graph says is most active, as readable text."""
        if not activation:
            return "this"
        node, _value = max(activation.items(), key=lambda kv: abs(kv[1]))
        return node.replace("_", " ")

    # -- observation of the loop itself -----------------------------------
    def audit(self) -> List[dict]:
        return [turn.to_dict() for turn in self._audit]

    def stats(self) -> dict:
        """Loop-level numbers, including whether the realtime claim holds.

        `within_budget_rate` over the audited turns is the only honest form of
        "realtime": a budget declared in the constructor and never measured
        against observed turns is a wish.
        """
        if not self._audit:
            return {"turns": 0, "within_budget_rate": None,
                    "note": "no turns run: latency is unknown, not zero"}
        inside = sum(1 for t in self._audit if t.within_budget)
        latencies = sorted(t.latency_ms for t in self._audit)
        middle = latencies[len(latencies) // 2]
        stances: Dict[str, int] = {}
        for turn in self._audit:
            stances[turn.stance] = stances.get(turn.stance, 0) + 1
        # `turns` counts the session; every other figure is over the AUDIT WINDOW.
        # Mixing the two denominators in one dict is how a 200-turn session ends up
        # reporting 54 stances against 200 turns and looking like lost turns.
        return {"turns": self._turn_count,
                "audited": len(self._audit),
                "window": "within_budget_rate, median_ms, max_ms and stances are "
                          "computed over the %d audited turns" % len(self._audit),
                "within_budget_rate": round(inside / len(self._audit), 4),
                "budget_ms": self.budget_ms,
                "median_ms": round(middle, 3),
                "max_ms": round(latencies[-1], 3),
                "stances_in_audit": stances,
                "safety_turns_in_audit": stances.get("safety", 0)}

    def felt_vs_displayed(self, rules: Optional[dict] = None) -> dict:
        """Expose the affect layer's masking distinction at the loop boundary."""
        return express(self.pad, rules)
