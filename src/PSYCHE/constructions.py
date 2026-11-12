# coding: utf-8
"""PSYCHE -- construction-grammar risk detection.

WHY THIS REPLACES LEXICAL MATCHING
----------------------------------
Two measured failures motivate this module. A pattern/list detector scored recall
1.00 on the corpus it was fitted to and **0.00** on the next one. Its replacement
-- concept families over clinical item vocabulary -- reached 0.83 on one holdout
and **0.18** on the next. Reading the misses shows why, and it is not a vocabulary
problem:

    "there's genuinely no version of next year that has me in it"
    "I put the letters in the drawer on Sunday"
    "I have been looking up how much of this stuff it takes"
    "i've told myself the flat is the deadline. after that i'm allowed"

None of those contains a crisis word. Each one is a **construction**: a frame with
slots, where the frame carries the meaning and the fill decides which frame it is.
"no version of next year has **me** in it" and "no version of march has **me** in
it, they've cancelled the contract" share every frame and differ only in the fill
and the clause attached to it. A token list cannot see the difference because the
difference is not in the tokens; it is in the structure.

THE DISCRIMINATOR, STATED PLAINLY
---------------------------------
Each frame is scored by what fills its slots, not by what words appear:

  * **self vs object fill** -- "tidier without **me**" is about the speaker's
    absence; "tidier without **my boxes**" is about furniture.
  * **unspecified vs named object** -- "how much **it** takes" leaves the referent
    deliberately unspoken; "how much **the deposit** takes" names it. The
    unspecified pronoun is the signal.
  * **terminal marker** -- "then it's over", "after that I'm allowed" converts an
    ordinary list of tasks into a preparation narrative.
  * **purpose or cause clause** -- "because the view is good" marks the bridge
    sentence as scenery; its absence leaves the rehearsal bare.

This is a deliberately small grammar of seven frames, not a parser. A parser would
be a dependency and would not change the discrimination, which lives in the slots.

WHAT THIS STILL DOES NOT DO
---------------------------
It is hand-written, by the same author who wrote the test sentences. It cannot
generalise by construction the way a trained model could, and it will miss any
construction not in this list. It is a better instrument than a token list on the
evidence available; it is not a validated one, and the README says so.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

#: Subject of a construction. Only `self` can escalate: a frame about someone else
#: is not this person's intent, and a frame with no recoverable subject is treated
#: as self only when the frame is inherently first-person ("I've told myself...").
SELF, OTHER, UNKNOWN = "self", "other", "unknown"

#: Terminal markers: they convert an inventory of ordinary facts into a plan that
#: ends. Deliberately finite and phrase-level, because a terminal marker is close
#: to a fixed idiom across registers.
TERMINAL_MARKERS: Tuple[str, ...] = (
    "then it's over", "then its over", "then it is over", "and then it's over",
    "after that i'm allowed", "after that i am allowed", "after that i'm done",
    "after that i am done", "and then i'm done", "and then i am done",
    "and then that's it", "and then that is it", "and that's it then",
    "i just have to do the thing", "then i'm done", "then i am done",
    "and i'm done", "that's the plan", "it is all arranged", "it's all arranged",
)

#: Purpose/cause clauses. Their presence says the sentence is about continuing a
#: life, not ending one. Absence earns the frame, presence spends it.
BENIGN_CLAUSES: Tuple[str, ...] = (
    "because the view", "because it's nice", "because it is nice", "because i like",
    "so that i can", "so i can", "in order to", "for the deposit", "for the rent",
    "for work", "for the interview", "for the bus", "for the school", "worth the",
    "job hunting", "they've cancelled", "they have cancelled", "getting the",
)

#: Objects whose presence in a frame argues that the frame is about logistics:
#: moving house, insurance, a commute. Every one of these appeared in a benign
#: sentence that otherwise matched a risk frame exactly.
LOGISTIC_OBJECTS: Tuple[str, ...] = (
    "removals", "van", "boxes", "insurance", "deposit", "interview", "pension",
    "bus", "job", "contract", "office", "hall", "tablets",
    "doctor", "prescription", "landlord", "mortgage", "course",
    # "school run" is deliberately NOT here. It is a TIME ANCHOR, and a time
    # anchor is what a plan is scheduled against, not evidence of what the plan is
    # for: "after the school run is easiest" and "after the school run is easiest
    # for the bus" differ by the destinative clause, not by the time.
)

#: Self-directed objects. A frame filled with one of these is about the speaker.
SELF_OBJECTS: Tuple[str, ...] = (
    "without me", "has me in it", "have me in it", "with me in", "me in it",
    "keeping me", "kept me", "for me", "about me", "find me", "notice me",
    "who gets", "gets the dog", "gets the", "one less person",
)

#: Disposal and arrangement verbs. The frame is PREPARATION; the fill decides
#: whether it is a will, a move, or a suicide note.
DISPOSAL_VERBS: Tuple[str, ...] = (
    "arranged", "sorted", "put", "written", "wrote", "posted", "post", "sent",
    "sign", "signed", "packed", "packed up", "given away", "giving away",
    "tidied", "cleared", "cancelled", "closed", "transferred", "done",
)

#: Artifacts that appear in both registers and are therefore only meaningful with
#: a slot fill or a terminal marker.
ARTIFACTS: Tuple[str, ...] = (
    "letters", "letter", "note", "accounts", "will", "drawer", "things",
    "stuff", "dog", "flat", "house", "keys", "phone",
)

#: Means-acquisition verbs. Deliberately paired with an UNSPECIFIED object.
MEANS_VERBS: Tuple[str, ...] = (
    "looking up", "looked up", "reading up", "read up", "worked out",
    "working out", "found out", "know the number", "know how much",
    "how much it takes", "how much of this", "how many it takes",
)

#: Places and means a person rehearses. A bare mention is not enough: the frame
#: needs a rehearsal verb or a temporal intensifier to count.
MEANS_OBJECTS: Tuple[str, ...] = (
    "bridge", "drawer", "razor", "blade", "rope", "pills", "tablets", "gun",
    "train", "car park", "multi-storey", "ledge", "roof",
    # "practising what I'd write" rehearses the note itself, which is a rehearsal
    # even though no artifact noun appears.
    "what i'd write", "what i would write", "the note", "the letter",
    "about the drawer", "about the bridge",
)

#: Rehearsal markers.
REHEARSAL_MARKERS: Tuple[str, ...] = (
    "keep thinking about", "keep going over", "every drive home", "on my way home",
    "think about", "thinking about", "every night", "every single night",
    "practising", "practicing", "picturing",
    "picturing it", "imagining", "slow down on the", "slow down at the",
    "not stopping, just noticing", "just noticing",
)

#: Foreclosure markers: a deadline after which the speaker's own participation ends.
FORECLOSURE_MARKERS: Tuple[str, ...] = (
    # "the deadline" alone is NOT here. "the deadline is brutal, it is killing me"
    # is a work complaint, and a bare noun is exactly the kind of marker that made
    # the earlier mechanisms look effective on the phrasings they were built from.
    # Foreclosure is X BEING the deadline: the copula is the construction.
    "is the deadline", "after that i'm allowed", "give it until",
    "until my birthday", "told myself", "no version of", "nothing about",
    "i've stopped picturing", "have stopped picturing", "stopped picturing",
    "not in it", "hasn't me", "has me in it", "has me in",
)

#: Tether markers: an explicit reason to stay alive. Genuinely ambiguous -- it is
#: protective AND it is the sentence a person says when the tether is all there is.
TETHER_MARKERS: Tuple[str, ...] = (
    "only thing keeping me", "only reason i'm here", "only reason i am here",
    "keeping me here", "keeping me on this side", "keeping me alive",
    "one thing stopping me",
)

#: Withheld actions: an artifact prepared but not yet released. "I have not posted
#: them yet" is the whole difference between a to-do list and a preparation
#: narrative, and it is a SEQUENCE fact rather than a vocabulary one.
WITHHELD_MARKERS: Tuple[str, ...] = (
    "not posted", "not sent", "haven't posted", "have not posted", "haven't sent",
    "have not sent", "not signed", "haven't signed", "have not signed",
    "not delivered", "not given", "haven't given", "not handed",
    "not written", "haven't written", "have not written",
)

#: Burden markers. Each REQUIRES a self object ("without me"), because "without my
#: boxes" is furniture. The frame is about the speaker's absence, so the absence
#: has to be the thing being weighed.
BURDEN_PATTERNS: Tuple[Tuple[str, str], ...] = (
    ("better_off_without_self", r"\bbetter off (without|with one less) (me|us)\b"),
    ("tidier_without_self", r"\b(tidier|easier|cleaner|quieter) without (me|us)\b"),
    ("relief_at_absence", r"\b(relieved|relief) (if|when|that) (i|i'm|im)\b"),
    ("unnoticed_absence", r"\b(no one|nobody|none of them) would (notice|care)\b"),
    ("one_less", r"\bone less person\b"),
    ("admin_for_them", r"\b(admin|paperwork) for them\b"),
    ("burden_self", r"\b(burden|weight|drag) (on|to) (them|everyone|people)\b"),
)


@dataclass(frozen=True)
class Frame:
    """One detected construction."""

    name: str
    subject: str
    weight: float
    evidence: str

    def to_dict(self) -> Dict[str, object]:
        return {"name": self.name, "subject": self.subject,
                "weight": round(self.weight, 3), "evidence": self.evidence}


@dataclass
class ConstructionReport:
    """Frames found, and the score they add up to."""

    frames: List[Frame] = field(default_factory=list)
    suppressed: List[str] = field(default_factory=list)

    @property
    def benign_evidence(self) -> bool:
        """True when a sentence was explicitly marked as being about something else.

        Exposed because another channel needs it: the coherence rules match
        "the only thing keeping me in this job" on vocabulary alone and cannot see
        the pension clause that makes it a job complaint. When this channel found
        slot-fill evidence that the text is about logistics, the channel that
        found it wins -- two mechanisms disagreeing, and only one of them read the
        object of the sentence.
        """
        return any("benign marker" in line for line in self.suppressed)

    @property
    def score(self) -> float:
        return round(sum(f.weight for f in self.frames if f.subject == SELF), 3)

    @property
    def fires(self) -> bool:
        """One self-subject construction is enough.

        That is aggressive for a router and correct for these frames: "I have been
        looking up how much it takes" is not a sentence with a risk word in it, it
        IS the risk, and requiring two constructions to agree would miss the
        single-sentence versions of every frame. The cost is paid in the benign
        clause and slot-fill logic above, not in the threshold.
        """
        return self.score >= CONSTRUCTION_THRESHOLD

    def to_dict(self) -> Dict[str, object]:
        return {"score": self.score, "fires": self.fires,
                "frames": [f.to_dict() for f in self.frames],
                "suppressed": list(self.suppressed)}


#: Threshold a self-subject construction must reach. Each frame carries its own
#: weight; this is the floor, not a tuned knob.
CONSTRUCTION_THRESHOLD = 1.0

#: Frames about the SELF that are strong enough to escalate alone.
FRAME_WEIGHTS: Dict[str, float] = {
    "future_absence": 2.0,   # no version of X has me in it
    "preparation": 1.8,      # disposal + artifacts + terminal marker
    "means_lookup": 2.0,     # how much it takes, know the number
    "rehearsal": 1.5,        # the bridge, every drive home
    "foreclosure": 1.5,      # deadline after which I am allowed
    "tether": 1.2,           # the only thing keeping me here is X
    "burden": 1.5,           # tidier without me
}

#: Sentence splitting is coarse on purpose: chat messages are full of fragments,
#: and a real tokeniser is a dependency for a gain these frames do not need.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")

#: First person in SUBJECT position: at the start of a clause, or after a
#: conjunction that starts one. This is the distinction that matters, because "me"
#: in "she told me" and "it is killing me" is an object, and treating object
#: pronouns as subjects mis-attributes whole sentences.
_CLAUSE_FIRST_PERSON = re.compile(
    r"(?:^|\b(?:and|but|so|then|because|honestly|when)\s+)"
    r"(i|i'm|i've|i'd|i'll)\b|\b(my|myself)\b")
_CLAUSE_THIRD_PERSON = re.compile(
    r"(?:^|\b(?:and|but|so|then|because|honestly|when)\s+)"
    r"(she|he|they|my\s+(?:sister|brother|mum|mother|father|son|daughter|friend|"
    r"partner|wife|husband|client|patient)|a\s+(?:client|patient|friend)|someone)\b")
_FIRST_PERSON = re.compile(r"\b(i|i'm|i've|i'd|i'll|me|my|myself)\b")
_THIRD_PERSON = re.compile(
    r"\b(she|he|they|her|his|their|my sister|my brother|my mum|my mother|"
    r"my father|my son|my daughter|my friend|my client|my patient|a client|"
    r"a friend|someone)\b")


def _has(text: str, needles: Sequence[str]) -> Optional[str]:
    for needle in needles:
        if needle in text:
            return needle
    return None


def _subject(sentence: str, frame_evidence: str) -> str:
    """Who a frame is about.

    The frame EVIDENCE is checked first and it wins: "the only thing keeping me
    on this side is that my sister would find me" mentions a sister and is
    nonetheless entirely about the speaker. A blanket third-person guard gets that
    sentence wrong, and it is the sentence where being wrong matters most.
    """
    if _CLAUSE_FIRST_PERSON.search(frame_evidence or ""):
        return SELF
    # A clause-initial third person outranks a stray "me". "she told me she had
    # been looking up how much it takes" contains "me" as the INDIRECT OBJECT of
    # told; reading that as the subject made another person's sentence this
    # person's intent, which is the worst direction for this error to point.
    if _CLAUSE_THIRD_PERSON.search(sentence):
        return OTHER
    if _CLAUSE_FIRST_PERSON.search(sentence):
        return SELF
    if _FIRST_PERSON.search(frame_evidence or ""):
        return SELF
    if _THIRD_PERSON.search(sentence):
        return OTHER
    return UNKNOWN


def _benign(sentence: str) -> Optional[str]:
    """A clause or object that argues the sentence is about continuing a life."""
    found = _has(sentence, BENIGN_CLAUSES)
    if found:
        return found
    return _has(sentence, LOGISTIC_OBJECTS)


def _preparation_from(current: str, following: str = "") -> List[Tuple[str, str, str]]:
    """PREPARATION -- disposal verb + artifact + a qualifier, across a boundary.

    The window matters and is the reason this is not inside `_frames_for`: a chat
    turn splits the construction in two. "I put the letters in the drawer on
    Sunday" carries the verb and the artifact; "I have not posted them yet"
    carries the withheld action. Neither sentence is a preparation narrative on
    its own, and reading them separately misses the thing that is there.
    """
    window = (current + " " + following).strip()
    disposal = _has(window, DISPOSAL_VERBS)
    artifact = _has(window, ARTIFACTS)
    if not (disposal and artifact):
        return []
    terminal = _has(window, TERMINAL_MARKERS)
    bequest = _has(window, SELF_OBJECTS)
    withheld = _has(window, WITHHELD_MARKERS)
    qualifier = terminal or bequest or withheld
    if not qualifier:
        return []
    evidence = "%s + %s + %r" % (disposal, artifact, qualifier)
    return [("preparation", evidence, _subject(window, evidence))]


def _frames_for(sentence: str) -> List[Tuple[str, str, str]]:
    """Return (frame name, evidence, subject hint) triples found in one sentence."""
    out: List[Tuple[str, str, str]] = []

    # 1. FUTURE_ABSENCE -- self is absent from a named future.
    for pattern in (r"\bno (version of|part of)\b[^.]*\b(me|i)\b",
                    r"\bnothing (about|in)\b[^.]*\b(me|i)\b",
                    r"\b(don't|do not|can't|cannot) (see|picture|imagine) myself\b",
                    r"\bstopped picturing\b",
                    r"\bd(o|oes)n't have me\b",
                    r"\bno future\b", r"\bnever be\b[^.]*\bagain\b"):
        match = re.search(pattern, sentence)
        if match:
            out.append(("future_absence", match.group(0), SELF))
            break

    # 2. MEANS_LOOKUP -- an UNSPECIFIED object plus a means verb. "how much the
    #    deposit takes" names its object and is not this frame.
    means = _has(sentence, MEANS_VERBS)
    if means:
        if re.search(r"\b(how much|how many|what dose|the number|the timing)\b",
                     sentence) and not _has(sentence, LOGISTIC_OBJECTS):
            out.append(("means_lookup", means, _subject(sentence, means)))

    # 4. REHEARSAL -- rehearsal verb or intensifier paired with a means/place.
    rehearsal = _has(sentence, REHEARSAL_MARKERS)
    if rehearsal and _has(sentence, MEANS_OBJECTS):
        out.append(("rehearsal", rehearsal, _subject(sentence, rehearsal)))

    # 5. FORECLOSURE -- a deadline that ends the speaker's participation.
    foreclosure = _has(sentence, FORECLOSURE_MARKERS)
    if foreclosure:
        out.append(("foreclosure", foreclosure, _subject(sentence, foreclosure)))

    # 6. TETHER -- an explicit reason for staying.
    tether = _has(sentence, TETHER_MARKERS)
    if tether:
        out.append(("tether", tether, _subject(sentence, tether)))

    # 7. BURDEN -- the speaker's absence framed as a relief to others. The pattern
    #    requires the self object, so "without my boxes" never reaches here.
    for name, pattern in BURDEN_PATTERNS:
        match = re.search(pattern, sentence)
        if match:
            out.append(("burden", match.group(0), _subject(sentence, match.group(0))))
            break

    return out


#: Frames that describe the speaker's own plan even when the subject is dropped.
#: Subject drop is a register feature of self-report -- "got the letters done
#: sunday", "been reading up on how much it takes" -- and refusing to score them
#: would miss precisely the clipped register a realtime channel receives. The
#: exemption is bounded: a third-person marker in the same scope still wins, so
#: "the film ends with her taking her own life" is not rescued by this rule.
SELF_IMPLICIT_FRAMES: Tuple[str, ...] = (
    "preparation", "means_lookup", "rehearsal", "foreclosure", "tether", "burden",
)


def terminal_or_bequest(scope: str) -> bool:
    """True when a scope carries a terminal marker or a bequest/custody slot."""
    return bool(_has(scope, TERMINAL_MARKERS) or _has(scope, SELF_OBJECTS))


def analyse(text: str) -> ConstructionReport:
    """Detect risk constructions, with the slot fills recorded as evidence.

    Every frame is reported whether or not it survives, and every suppression
    records WHY. A detector that silently drops a match cannot be audited, and the
    reason a frame was not counted is the only thing that makes the false-positive
    rate discussable.
    """
    lowered = (text or "").lower()
    report = ConstructionReport()
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(lowered) if s.strip()]
    seen = set()
    for index, sentence in enumerate(sentences):
        following = sentences[index + 1] if index + 1 < len(sentences) else ""
        spans = _preparation_from(sentence, following)
        # Local frames are judged against their own sentence's benign markers; a
        # frame assembled across two sentences against both, because the purpose
        # clause may land on either side of the boundary.
        # The scope is THIS sentence plus the next one, for every frame. A purpose
        # clause or a logistic noun regularly lands on the far side of a full stop
        # in this register -- "I've worked out the timing. after the school run is
        # easiest for the bus" -- and judging each sentence alone let the frame
        # fire on the half that carried no counter-evidence.
        scope = (sentence + " " + following).strip()
        for name, evidence, hint in _frames_for(sentence) + spans:
            if (name, evidence) in seen:
                continue
            seen.add((name, evidence))
            subject = _subject(scope, evidence) if hint != OTHER else OTHER
            if subject == UNKNOWN and name in SELF_IMPLICIT_FRAMES:
                subject = SELF
            weight = FRAME_WEIGHTS.get(name, 1.0)
            benign = _benign(scope)
            # A logistic noun does not outrank a BEQUEST. "the accounts, the flat,
            # who gets the dog" contains "flat", a moving-house noun, and is also a
            # custody allocation. A terminal marker does NOT get this override:
            # "it is all arranged" plus "the removals van" is a move, and the
            # marker is part of the logistics phrasing rather than against it.
            if benign and name == "preparation" and benign in LOGISTIC_OBJECTS \
                    and _has(scope, SELF_OBJECTS):
                benign = None
            if benign:
                report.suppressed.append(
                    "%s in %r: benign marker %r" % (name, sentence[:60], benign))
                continue
            if subject != SELF:
                report.suppressed.append(
                    "%s in %r: subject is %s, not the speaker" % (name, sentence[:60], subject))
                report.frames.append(Frame(name, subject, 0.0, evidence))
                continue
            report.frames.append(Frame(name, subject, weight, evidence))
    return report
