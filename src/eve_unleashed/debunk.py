"""debunk.py -- native myth-debunking for civil justice.

No network, no model, no key: the claim is taken apart with its own
words. Checkable propositions are separated from opinion language,
known fallacy shapes are flagged, and self-contradiction is tested
mechanically. The verdict answers only what the text supports:

- REFUTED: the claim contradicts itself on its own terms.
- SUSPECT: fallacy shapes present; needs live corroboration.
- CHECKABLE: propositions extracted, ready for `eve factcheck`.
- UNVERIFIABLE: pure opinion language, nothing to check.

Every uncheckable proposition is labeled OPINION, never proof --
the same doctrine as redress.prove_case. Eve compiles the
analysis; only you file it, and only you judge the person.
"""
import re

try:
    from CORE.failure_ledger import note_failure
except Exception:
    def note_failure(where, exc=None):
        pass

ABSOLUTES = ["always", "never", "all ", " every", " none ", "nobody",
             "nothing", "impossible", "undeniable", "irrefutable"]
HEDGES = ["maybe", "perhaps", "could be", "might be", "some say",
          "some people say", "allegedly", "reportedly"]
OPINION_CUES = ["i feel", "i believe", "in my opinion", "should be",
                "ought to", "disgusting", "corrupt", "evil", "stupid",
                "everyone knows", "obviously"]
WEASELS = ["studies show", "experts agree", "science says", "they say",
           "it is known", "sources say", "everyone agrees"]
AD_HOMINEM = ["shill", "sheep", "idiot", "moron", "liar", "fraud",
              "scam artist", "criminal"]
ANECDOTE = ["my neighbor", "my friend", "my cousin", "someone i know",
            "happened to me", "in my experience"]


def _sentences(text):
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _has_any(lowered, phrases):
    return [p.strip() for p in phrases if p in lowered]


def extract_propositions(text):
    """Split a claim into checkable propositions vs opinion language."""
    props, opinions = [], []
    for sent in _sentences(text):
        low = sent.lower()
        cues = _has_any(low, OPINION_CUES)
        if cues:
            opinions.append({"text": sent, "cues": cues})
        else:
            props.append(sent)
    return props, opinions


def flag_fallacies(text):
    """Flag known fallacy shapes. Returns [(flag, matched)]."""
    low = text.lower()
    flags = []
    absents = _has_any(low, ABSOLUTES)
    hedges = _has_any(low, HEDGES)
    if absents and hedges:
        flags.append(("absolute_plus_hedge",
                      absents[:2] + hedges[:2]))
    if _has_any(low, WEASELS):
        flags.append(("weasel_authority", _has_any(low, WEASELS)[:2]))
    if _has_any(low, AD_HOMINEM):
        flags.append(("ad_hominem", _has_any(low, AD_HOMINEM)[:2]))
    if _has_any(low, ANECDOTE):
        flags.append(("anecdote_as_data", _has_any(low, ANECDOTE)[:2]))
    return flags


def check_self_contradiction(props):
    """Mechanical contradiction: absolute claim plus its own exception."""
    notes = []
    for p in props:
        low = p.lower()
        if (("always" in low or "never" in low)
                and ("except" in low or "but " in low
                     or "however" in low or "although" in low)):
            notes.append(p)
    return notes


def debunk_claim(text):
    """Take a claim apart. Returns the analysis dict."""
    if not text or not text.strip():
        raise ValueError("nothing to debunk: empty claim")
    props, opinions = extract_propositions(text)
    flags = flag_fallacies(text)
    contra = check_self_contradiction(props)
    checks = []

    def check(name, ok, detail):
        checks.append({"check": name, "passed": bool(ok),
                       "detail": detail})
        return bool(ok)

    check("has_checkable_proposition", bool(props),
          "%d proposition(s), %d opinion(s)" % (len(props), len(opinions)))
    check("no_self_contradiction", not contra,
          "contradicted=%s" % contra if contra else "none found")
    check("no_fallacy_shapes", not flags,
          "flags=%s" % [f[0] for f in flags] if flags else "none found")
    if contra:
        verdict = "REFUTED"
    elif not props:
        verdict = "UNVERIFIABLE"
    elif flags:
        verdict = "SUSPECT"
    else:
        verdict = "CHECKABLE"
    return {"claim": text, "propositions": props,
            "opinions": [o["text"] for o in opinions],
            "fallacy_flags": [{"flag": f, "matched": m}
                              for f, m in flags],
            "contradictions": contra, "checks": checks,
            "verdict": verdict,
            "next": ("run `eve factcheck --claim` on the propositions"
                     if verdict in ("CHECKABLE", "SUSPECT")
                     else "no live check can settle this; treat as OPINION")}
