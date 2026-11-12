"""Project Lucy -- Eliza + live therapy, combined natively.

WHAT LUCY IS
------------
Eliza (Project_ELIZA psyche: compiled inference with a conscience --
IntuitionIndex over one person's construct graph) and Eve live therapy
(src/eve_unleashed/therapy.py: offline-first grounding script) already
existed and did not know about each other. Lucy is the composition:

    eliza reading  -> what the compiled past recognises in this moment
    therapy prompt -> what the swarm would be asked (mood + note)
    response       -> the offline grounding script, always available

No network, no model, no audio hardware. The swarm LLM path is the
caller's choice, not Lucy's requirement: `therapy_prompt` is provided
so a caller WITH a swarm can ask it, while `response` is already the
answer for a caller without one.

Stdlib only. Every import is lazy and degrades to a named absence --
a missing graph is a report, never a traceback.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _repo_src():
    p = REPO / "src"
    if str(p) not in sys.path and p.is_dir():
        sys.path.insert(0, str(p))


def eliza_reading(cues=None):
    """Compiled-inference reading over the caller's cues, or unavailability.

    Returns {"known": bool, ...}. Never raises: without a person graph
    there is nothing to compile, and Lucy says so.
    """
    cues = cues or {}
    if not cues:
        return {"known": False, "reason": "no cues -- nothing to recognise"}
    _repo_src()
    try:
        eliza_psyche = REPO / "Project_ELIZA" / "eliza" / "psyche"
        if str(eliza_psyche) not in sys.path and eliza_psyche.is_dir():
            sys.path.insert(0, str(eliza_psyche))
        import intuition as _i  # Project_ELIZA psyche module
        idx_cls = getattr(_i, "IntuitionIndex", None)
        if idx_cls is None:
            return {"known": False, "reason": "psyche has no IntuitionIndex"}
        # Without the person's network there is no index to read; the shape
        # of the answer is the contract, not a fabricated confidence.
        return {"known": False,
                "reason": "no person graph bound -- cues held, not judged",
                "cues": dict(cues)}
    except Exception as e:
        return {"known": False, "reason": "psyche unavailable (%s)" % e}  # scanner:ignore except_return_default -- a failed psyche read holds cues unjudged; unknown IS the honest answer


def therapy_prompt(mood, note):
    """What the swarm would be asked. Never a diagnosis."""
    _repo_src()
    try:
        from eve_unleashed.therapy import build_prompt
        return build_prompt(mood, note)
    except Exception:
        mood = (mood or "stressed").strip()[:60]
        note = (note or "").strip()[:500]
        return ("You are a warm supportive companion, not a clinician. "
                "The user feels %s. They shared: %s. Give a short "
                "supportive reply." % (mood, note or "nothing more yet"))


def therapy_response(mood, note):
    """The offline grounding script. Always available."""
    _repo_src()
    try:
        from eve_unleashed.therapy import fallback_response
        return fallback_response(mood, note)
    except Exception:
        return ("I hear you. Breathe in 4 counts, hold 4, out 6. "  # scanner:ignore except_return_default -- degraded-path grounding script; the safe default IS the therapy answer
                "Name 5 things you can see. I am here with you. "
                "What do you need next?")


THROWS = ("rock", "paper", "scissors", "lizard", "spock")

_GATE_NAMES = {
    "rock": "foundation",
    "paper": "honesty",
    "scissors": "brevity",
    "lizard": "thread",
    "spock": "safety",
}

_DIAGNOSIS_WORDS = ("diagnos", "disorder", "syndrome", "psychos",
                    "prescrib", "medication", "you need treatment",
                    "chemical imbalance")

_STOPWORDS = frozenset(("i", "me", "my", "and", "the", "a", "an",
                        "to", "of", "it", "is", "in", "on", "for",
                        "with", "that", "this", "feel", "feeling",
                        "very", "really", "just", "so", "am"))


def _words(text):
    return [w.strip(".,!?;:\"'()").lower() for w in (text or "").split()]


def judge_rock(mood, note, text):
    """Foundation: the grounding script is present."""
    ok = "breathe in 4 counts" in (text or "").lower()
    return {"throw": "rock", "gate": _GATE_NAMES["rock"], "passed": ok,
            "reason": ("grounding script present" if ok
                         else "no grounding script")}


def judge_paper(mood, note, text):
    """Honesty: no invented particulars (digits, links, percentages)."""
    t = text or ""
    bad = [tok for tok in ("http", "%") if tok in t.lower()]
    bad += [w for w in _words(t) if any(c.isdigit() for c in w)]
    ok = not bad
    return {"throw": "paper", "gate": _GATE_NAMES["paper"], "passed": ok,
            "reason": ("no invented particulars" if ok
                         else "ungrounded detail: %s" % bad[0][:40])}


def judge_scissors(mood, note, text):
    """Brevity: the cut -- sixty words or fewer."""
    n = len((text or "").split())
    return {"throw": "scissors", "gate": _GATE_NAMES["scissors"],
            "passed": n <= 60, "reason": "%d words" % n}


def judge_lizard(mood, note, text):
    """Thread: memory before network -- hold what was said, or ask on."""
    t = text or ""
    echo = [w for w in _words(note) if len(w) >= 4 and w not in _STOPWORDS]
    held = [w for w in echo if w in t.lower()]
    asks = t.rstrip().endswith("?")
    ok = bool(held) or asks
    if held:
        reason = "holds: %s" % ", ".join(held[:3])
    else:
        reason = "asks on" if asks else "drops the thread"
    return {"throw": "lizard", "gate": _GATE_NAMES["lizard"],
            "passed": ok, "reason": reason}


def judge_spock(mood, note, text):
    """Safety: the verdict -- no clinician language, ever."""
    t = (text or "").lower()
    bad = [w for w in _DIAGNOSIS_WORDS if w in t]
    ok = not bad
    return {"throw": "spock", "gate": _GATE_NAMES["spock"], "passed": ok,
            "reason": ("no clinician language" if ok
                         else "clinician language: %s" % bad[0])}


#: The judging swarm, in throw order. All inclusive: any callable with the
#: (mood, note, text) -> {throw, gate, passed, reason} shape may join;
#: nothing here limits how many judges or candidates may enter.
JUDGES = (judge_rock, judge_paper, judge_scissors, judge_lizard, judge_spock)


def judge_all(mood, note, text, judges=None):
    """Run every gate over one candidate. Returns {score, gates}."""
    gates = [j(mood, note, text) for j in (judges or JUDGES)]
    return {"score": sum(1 for g in gates if g["passed"]),
            "of": len(gates), "gates": gates}


def candidates(mood, note=""):
    """Genuine offline reply variants. All inclusive: callers may append
    their own candidates; the arena takes as many as enter."""
    mood = (mood or "stressed").strip()[:60]
    note = (note or "").strip()[:500]
    ground = therapy_response(mood, note)
    said = note if note else "You added nothing more, and that is fine."
    echo = ("It sounds like %s is weighing on you. %s "
            "Breathe in 4 counts, hold 4, out 6. "
            "I am here with you -- what feels hardest right now?"
            % (mood, said[:120]))
    short = ("I hear you. This %s feeling makes sense. "
             "Breathe in 4 counts, hold 4, out 6 -- "
             "what do you need next?" % mood)
    steady = ("Thank you for telling me. Feeling %s does not make you "
              "fragile; it makes you honest. Breathe in 4 counts, hold 4, "
              "out 6, and name five things you can see. I am staying "
              "with you -- shall we take the next minute together?" % mood)
    return [ground, echo, short, steady]


def bout(a, b, mood, note, rng, judges=None):
    """One rochambeau bout: every candidate faces all five gates.

    Higher gate-score wins. An exact tie goes to a seeded sudden-death
    throw, reported on the receipt -- the only chance in the building.
    """
    ra = judge_all(mood, note, a, judges)
    rb = judge_all(mood, note, b, judges)
    tiebreak = False
    if ra["score"] == rb["score"]:
        tiebreak = True
        winner, loser = (a, b) if rng.random() < 0.5 else (b, a)
    else:
        winner, loser = (a, b) if ra["score"] > rb["score"] else (b, a)
    return {"winner": winner, "loser": loser, "a_score": ra["score"],
            "b_score": rb["score"], "of": ra["of"], "a_gates": ra["gates"],
            "b_gates": rb["gates"], "tiebreak": tiebreak}


def arena(texts, mood, note, seed=316, judges=None):
    """Single-elimination over any number of candidates; odd ones out take
    a bye. Returns {champion, rounds, seed}. Never raises on content:
    an empty arena reports itself instead of crowning."""
    import random
    rng = random.Random(seed)
    field = list(texts or [])
    if not field:
        return {"champion": None, "rounds": [], "seed": seed,
                "reason": "no candidates entered"}
    rounds = []
    while len(field) > 1:
        nxt, pairs = [], []
        i = 0
        while i < len(field):
            if i + 1 >= len(field):
                nxt.append(field[i])
                pairs.append({"bye": field[i][:60]})
            else:
                rep = bout(field[i], field[i + 1], mood, note, rng, judges)
                nxt.append(rep["winner"])
                pairs.append({
                    "a": field[i][:60], "b": field[i + 1][:60],
                    "a_score": rep["a_score"], "b_score": rep["b_score"],
                    "of": rep["of"], "tiebreak": rep["tiebreak"],
                    "winner": rep["winner"][:60]})
            i += 2
        rounds.append(pairs)
        field = nxt
    return {"champion": field[0], "rounds": rounds, "seed": seed}


def arena_response(mood, note="", seed=316, texts=None, judges=None):
    """The arena bout for one moment: candidates, bracket, champion."""
    mood = (mood or "stressed").strip()[:60]
    note = (note or "").strip()[:500]
    rep = arena(texts if texts is not None else candidates(mood, note),
                mood, note, seed, judges)
    rep["mood"] = mood
    rep["throws"] = [j(mood, note, rep["champion"])["throw"]
                       if rep["champion"] else None for j in (judges or JUDGES)]
    return rep


def companion_response(mood, note="", cues=None):
    """Eliza reading + therapy prompt + offline response, one dict."""
    reading = eliza_reading(cues)
    prompt = therapy_prompt(mood, note)
    response = therapy_response(mood, note)
    return {"mood": (mood or "stressed").strip()[:60],
            "note": (note or "").strip()[:500],
            "eliza_reading": reading,
            "therapy_prompt": prompt,
            "response": response}


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Lucy -- Eliza + therapy, native.")
    ap.add_argument("mood", help="how the user feels (one word)")
    ap.add_argument("note", nargs="?", default="")
    ap.add_argument("--arena", action="store_true",
                    help="crown the reply in the arena instead of answering directly")
    ap.add_argument("--seed", type=int, default=316,
                    help="disclosed seed for arena tiebreaks only")
    args = ap.parse_args(argv)
    if args.arena:
        rep = arena_response(args.mood, args.note, seed=args.seed)
        print(rep["champion"])
        return 0
    print(companion_response(args.mood, args.note)["response"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
