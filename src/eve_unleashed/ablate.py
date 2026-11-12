"""Native refusal ablation for Eve.

Asks each provider the same benign prompts, classifies each reply as
ANSWERED / REFUSED / ERROR, then shows what the swarm fallback (with NSP
judgment) delivers for the same prompt. The ablation is the answer to
'how did NSP improve Eve': single providers refuse or die; the verified
fallback answers. Refusal detection is heuristic keyword matching and is
labeled as such, never as proof of a model's policy.
"""
from .model_registry import note_failure

REFUSAL_MARKS = ("i can't", "i cannot", "i'm unable", "i am unable",
                 "i refuse", "unable to help", "against my",
                 "i don't have access")


def classify(reply_text, error=None):
    """Return (verdict, detail) for one provider reply."""
    if error:
        return "ERROR", str(error)[:100]
    if not reply_text or not reply_text.strip():
        return "ERROR", "empty reply"
    lowered = reply_text.lower()
    for mark in REFUSAL_MARKS:
        if mark in lowered:
            return "REFUSED", "matched %r (heuristic)" % mark
    return "ANSWERED", "%d chars" % len(reply_text)


def ablate_prompt(prompt, providers, single_fn, timeout=60):
    """Run one prompt across providers plus the swarm fallback.

    single_fn(provider_name, prompt, timeout) -> (text, error).
    Returns a row dict with per-provider verdicts and the fallback row.
    """
    rows = []
    for name in providers:
        try:
            text, err = single_fn(name, prompt, timeout)
        except Exception as e:
            note_failure("ablate:provider", e)
            text, err = None, e
        verdict, detail = classify(text, err)
        rows.append({"provider": name, "verdict": verdict,
                     "detail": detail})
    answered = sum(1 for r in rows if r["verdict"] == "ANSWERED")
    rows.append({"provider": "swarm+NSP fallback", "verdict": "n/a",
                 "detail": "%d/%d providers answered; fallback serves "
                           "first answer with Z3 routing judgment"
                           % (answered, len(providers))})
    return {"prompt": prompt[:80], "rows": rows,
            "answered": answered, "total": len(providers)}
