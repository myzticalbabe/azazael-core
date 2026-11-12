"""The grant workflow, deterministic first.

Three rules hold regardless of how good the local model is:

1. **Rules decide what rules can decide.** A deadline, a post-PhD year cap, a
   citizenship requirement and a prior-mechanism cap are arithmetic. Sending
   them to a 4B model is how a correct answer becomes a plausible one.
2. **A model verdict needs a quote that appears in the source.** A quote that is
   not a substring of the RFP text is not evidence, and the row is downgraded to
   `unjudged` with the reason recorded. This is the whole point of running a
   local model: it can be wrong at length, so nothing it says is accepted on its
   own authority.
3. **A draft is scanned for numbers it cannot support.** A figure that appears
   nowhere in the profile or the RFP is flagged, and the section is graded
   `unsupported` rather than shipped, because a fabricated budget or enrolment
   number in a proposal is worse than a missing paragraph.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path

from .llm import extract_json

VERDICTS = ("eligible", "ineligible", "unjudged")
MIN_QUOTE = 12
NUMBER = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)*")
PLACEHOLDER = re.compile(r"\[[^\]]{1,80}\]|<[^>]{1,80}>|\b(?:TBD|TODO|FIXME|XXX)\b")


def load_json(value):
    """A dict from a dict, a path, or a JSON string — or a ValueError naming which."""
    if isinstance(value, dict):
        return value
    if isinstance(value, Path):
        return json.loads(value.read_text(encoding="utf-8"))
    if isinstance(value, str):
        candidate = Path(value)
        if candidate.exists():
            return json.loads(candidate.read_text(encoding="utf-8"))
        try:
            return json.loads(value)
        except ValueError as exc:
            raise ValueError("not a dict, an existing path, or JSON: %r (%s)"
                             % (value[:60], exc)) from exc
    raise ValueError("expected a dict, path or JSON string, got %s" % type(value).__name__)


def flat_text(*values):
    """Every string inside these structures, whitespace-collapsed and lowercased."""
    chunks = []

    def walk(item):
        if isinstance(item, str):
            chunks.append(item)
        elif isinstance(item, dict):
            for key in sorted(item):
                walk(item[key])
        elif isinstance(item, (list, tuple)):
            for entry in item:
                walk(entry)

    for value in values:
        walk(value)
    return " ".join(" ".join(chunks).split()).lower()


def locate_quote(quote, source_text):
    """Find `quote` in the source. Returns (ok, why, span, offset).

    The span is what a pin stores. Storing the reason ("verbatim") instead of the text
    is a pin that certifies itself: it reads as evidence while carrying none, and the
    operator has nothing to re-check. `offset` makes it re-findable in one index call.
    """
    if not isinstance(quote, str):
        return False, "quote was %s, not text" % type(quote).__name__, None, None
    collapsed = " ".join(quote.split()).lower()
    if len(collapsed) < MIN_QUOTE:
        return False, "quote shorter than %d characters" % MIN_QUOTE, None, None
    offset = source_text.find(collapsed)
    if offset < 0:
        return False, "quote does not appear in the RFP text", None, None
    return True, "verbatim span found at offset %d" % offset, collapsed, offset


def quote_supported(quote, source_text):
    """Is `quote` a real substring of the source, long enough to mean something?"""
    ok, why, _span, _offset = locate_quote(quote, source_text)
    return ok, why


def _as_date(value):
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        _ = exc
        return None


# -- rules ------------------------------------------------------------------

def _rule_deadline(rfp, profile, today):
    deadline = _as_date(rfp.get("deadline")) if rfp.get("deadline") else None
    if deadline is None:
        return None
    if today > deadline:
        return {"criterion": "deadline", "rule": "deadline",
                "verdict": "ineligible",
                "evidence": "deadline %s is before today %s" % (deadline, today)}
    return {"criterion": "deadline", "rule": "deadline", "verdict": "eligible",
            "evidence": "deadline %s is open (%d day(s) left)"
                        % (deadline, (deadline - today).days)}


def _rule_years(rfp, profile, today):
    cap = rfp.get("max_years_post_phd")
    if cap is None:
        return None
    years = profile.get("years_post_phd")
    if years is None:
        return {"criterion": "career_stage", "rule": "max_years_post_phd",
                "verdict": "unjudged",
                "evidence": "profile has no years_post_phd"}
    verdict = "eligible" if float(years) <= float(cap) else "ineligible"
    return {"criterion": "career_stage", "rule": "max_years_post_phd", "verdict": verdict,
            "evidence": "%s years post-PhD against a %s-year cap" % (years, cap)}


def _rule_citizenship(rfp, profile, today):
    required = rfp.get("citizenship")
    if not required or str(required).lower() == "any":
        return None
    held = str(profile.get("citizenship") or "").lower()
    if not held:
        return {"criterion": "citizenship", "rule": "citizenship", "verdict": "unjudged",
                "evidence": "profile has no citizenship"}
    ok = held == str(required).lower()
    return {"criterion": "citizenship", "rule": "citizenship",
            "verdict": "eligible" if ok else "ineligible",
            "evidence": "profile citizenship %r against requirement %r" % (held, required)}


def _rule_mechanism(rfp, profile, today):
    mechanism = rfp.get("mechanism")
    if not mechanism or not rfp.get("no_prior_mechanism"):
        return None
    prior = [str(entry).lower() for entry in profile.get("prior_mechanisms") or []]
    taken = str(mechanism).lower() in prior
    return {"criterion": "prior_mechanism", "rule": "no_prior_mechanism",
            "verdict": "ineligible" if taken else "eligible",
            "evidence": ("mechanism %r already held in %s" % (mechanism, prior)) if taken
                        else ("mechanism %r not in %s" % (mechanism, prior))}


RULES = (_rule_deadline, _rule_years, _rule_citizenship, _rule_mechanism)


# -- assessment -------------------------------------------------------------

def assess(rfp, profile, llm=None, today=None):
    """Per-criterion verdicts. LLM is optional and never trusted without a quote."""
    rfp = load_json(rfp)
    profile = load_json(profile)
    today = _as_date(today) if today else date.today()
    rows = []
    for rule in RULES:
        row = rule(rfp, profile, today)
        if row is not None:
            # ELIZA's discipline, adopted natively: a row that sounds decided must
            # carry the reading that decided it and a way to re-run that reading.
            row["pin"] = {"kind": "rule", "rule": rule.__name__.lstrip("_"),
                          "inputs": {"today": today.isoformat()},
                          "recheck": "grantforge assess <rfp> <profile> --offline --json"}
            rows.append(row)

    engine = "rules"
    llm_report = None
    criteria = rfp.get("text_criteria") or []
    if criteria:
        # The quotable source is the RFP WITHOUT the criteria block: a quote taken from
        # the question we asked proves only that we asked it. Measured on 2026-11-05, an
        # accepted row pinned the span 'is it a fit?' at offset 36 — the question, not
        # the RFP — and the pin certified it as evidence.
        source = flat_text({key: value for key, value in rfp.items()
                            if key != "text_criteria"})
        full_text = flat_text(rfp)
        if llm is None:
            for criterion in criteria:
                rows.append({"criterion": criterion.get("id") or criterion.get("question", "")[:40],
                             "rule": "text_criterion", "verdict": "unjudged",
                             "evidence": "no model configured; textual criterion not judged",
                             "pin": {"kind": "none",
                                     "reason": "no model configured, so nothing was read"}})
        else:
            engine = "rules+llm"
            llm_report = {"model": getattr(llm, "model", None), "criteria": 0,
                          "accepted": 0, "downgraded": 0}
            # discovery fills `model` on the first call, so the name is read after it,
            # not from a constructor that had not yet asked the endpoint what it serves.
            for criterion in criteria:
                cid = criterion.get("id") or str(criterion.get("question", ""))[:40]
                question = criterion.get("question") or cid
                answer = llm.json_object([
                    {"role": "user", "content":
                     "RFP text:\n%s\n\nQuestion: %s\n\nAnswer with keys verdict "
                     "(eligible|ineligible) and quote (a verbatim sentence from the RFP "
                     "text that decides it)." % (json.dumps(rfp, indent=2)[:8000], question)}
                ], required=("verdict", "quote"))
                llm_report["criteria"] += 1
                verdict = str(answer.get("verdict", "")).lower()
                ok, why, span, offset = locate_quote(answer.get("quote"), source)
                if not ok and isinstance(answer.get("quote"), str) \
                        and why == "quote does not appear in the RFP text":
                    # Only refine a long-enough quote that missed the RFP; a short one is
                    # rejected for being short and must say so.
                    collapsed = " ".join(str(answer["quote"]).split()).lower()
                    if len(collapsed) >= MIN_QUOTE and collapsed in full_text:
                        why = "quote appears only in the criterion text, not in the RFP"
                if not ok or verdict not in ("eligible", "ineligible"):
                    llm_report["downgraded"] += 1
                    reason = why if not ok else "verdict %r is not eligible/ineligible" % verdict
                    rows.append({"criterion": cid, "rule": "text_criterion",
                                 "verdict": "unjudged", "evidence": "model answer rejected: " + reason,
                                 "model_verdict": verdict,
                                 "model_quote": str(answer.get("quote"))[:200],
                                 "pin": {"kind": "none", "reason": reason}})
                else:
                    llm_report["accepted"] += 1
                    rows.append({"criterion": cid, "rule": "text_criterion",
                                 "verdict": verdict, "evidence": "quote: " + why,
                                 "model_quote": str(answer.get("quote"))[:200],
                                 "pin": {"kind": "quote", "quote": span,
                                         "offset": offset,
                                         "source": "rfp (flattened text)",
                                         "recheck": "grantforge assess <rfp> <profile> --json"}})

    if llm_report is not None:
        llm_report["model"] = (getattr(llm, "last", {}) or {}).get("model") or llm_report["model"]

    verdicts = [row["verdict"] for row in rows]
    if "ineligible" in verdicts:
        overall = "ineligible"
    elif "unjudged" in verdicts or not rows:
        overall = "unjudged"
    else:
        overall = "eligible"
    # ARC status, in ELIZA's vocabulary: READY means every row is pinned to something
    # this run read; GAPS means at least one row was refused; BLIND means no criterion
    # could be read at all. INCOMPLETE is deliberately absent — every declared criterion
    # either produces a row or the run is BLIND, so there is no third middle state here.
    if not rows:
        status = "BLIND"
    elif "unjudged" in verdicts:
        status = "GAPS"
    else:
        status = "READY"
    return {"rfp_id": rfp.get("id"), "profile": profile.get("name"),
            "engine": engine, "verdict": overall, "status": status, "rows": rows,
            "llm": llm_report,
            "counts": {state: verdicts.count(state) for state in VERDICTS}}


# -- drafting ---------------------------------------------------------------

def unsupported_numbers(text, sources):
    """Every numeral in `text` that appears nowhere in the supplied sources."""
    source_text = flat_text(sources)
    missing = []
    for token in NUMBER.findall(text or ""):
        if token.lower() not in source_text:
            missing.append(token)
    return list(dict.fromkeys(missing))


def placeholders(text):
    return list(dict.fromkeys(PLACEHOLDER.findall(text or "")))


def section_index(rfp):
    """Every section the RFP itself declares, with what can be encoded about it.

    The section set belongs to the RFP, never to this module. A tool that knows
    `significance` and `approach` by name works on the demo and invents the rest;
    this returns the declared set so the caller can draft any RFP that carries its
    own sections, and names the ones whose encoding is incomplete (no word limit)
    instead of silently drafting them unbounded.
    """
    rfp = load_json(rfp)
    declared = rfp.get("sections") or {}
    rows = []
    for name in sorted(declared):
        spec = declared[name]
        if not isinstance(spec, dict):
            rows.append({"section": name, "state": "unreadable", "max_words": None,
                         "encodable": False,
                         "note": "the declaration is not an object, so no limit can be read"})
            continue
        limit = spec.get("max_words")
        rows.append({"section": name,
                     "state": "bounded" if isinstance(limit, int) and limit > 0 else "unbounded",
                     "max_words": limit if isinstance(limit, int) else None,
                     "encodable": isinstance(limit, int) and limit > 0,
                     "source": "rfp.sections"})
    return {"rfp": rfp.get("id"), "count": len(rows), "sections": rows,
            "encodable": sum(1 for row in rows if row["encodable"]),
            "unbounded": [row["section"] for row in rows if not row["encodable"]]}


def draft(rfp, profile, section, llm, max_tokens=900, references=""):
    """One section, from the model, then scanned for what it cannot support."""
    if llm is None:
        raise ValueError("draft requires a model; rules cannot write proposal text")
    rfp = load_json(rfp)
    profile = load_json(profile)
    declared = rfp.get("sections") or {}
    if section not in declared:
        raise ValueError("section %r is not declared in the RFP; declared: %s"
                         % (section, ", ".join(sorted(declared)) or "none"))
    spec = declared.get(section) or {}
    limit = spec.get("max_words") if isinstance(spec, dict) else None
    # A section is prose, so prose is accepted: measured on :8081, a 4B model asked
    # for a wrapped object answered with `**Significance (300 words):** ...` and was
    # right to — the wrapper was our convenience, not the requirement. The scan below
    # is what protects the claim, so it runs on either shape. `assess` stays strict
    # because there the object IS the answer (a verdict and its quote).
    prompt = ("Write the %r section of a grant proposal.\n\nRFP: %s\n\nApplicant: %s\n\n"
              "Use only facts present in the RFP and applicant above; do not invent "
              "numbers, dates, citations or preliminary results. %s\n\n"
              "Return a JSON object with keys text (the section, plain text) and notes "
              "(what you could not support from the material)."
              % (section, json.dumps(rfp, indent=2)[:6000],
                 json.dumps(profile, indent=2)[:4000],
                 ("Reference material:\n%s" % references[:4000]) if references else ""))
    reply = llm.complete([
        {"role": "system", "content":
         "You are a grant writer. Answer with a JSON object carrying keys text and "
         "notes; if you cannot, answer with the section prose alone."},
        {"role": "user", "content": prompt}], max_tokens=max_tokens)
    parsed = extract_json(reply)
    if isinstance(parsed, dict) and isinstance(parsed.get("text"), str):
        text, notes, shape = parsed["text"], str(parsed.get("notes", "")), "json"
    else:
        text, notes, shape = reply, "", "prose"
    sources = [rfp, profile, references]
    missing = unsupported_numbers(text, sources)
    placeheld = placeholders(text)
    words = len(text.split())
    findings = []
    if missing:
        findings.append({"code": "UNSUPPORTED_NUMBER", "detail": ", ".join(missing)})
    if placeheld:
        findings.append({"code": "PLACEHOLDER", "detail": ", ".join(placeheld)})
    if limit and words > int(limit):
        findings.append({"code": "OVER_LIMIT", "detail": "%d words against %d" % (words, limit)})
    return {"section": section, "text": text, "notes": notes[:600], "format": shape,
            "words": words, "limit": limit,
            "grade": "unsupported" if missing or placeheld else "supported",
            "findings": findings}


# -- review -----------------------------------------------------------------

def review(proposal, rfp, profile=None, references=""):
    """Deterministic checks over a proposal. `ok` is false only for error findings."""
    proposal = load_json(proposal)
    rfp = load_json(rfp)
    sections = proposal.get("sections") or {}
    spec = rfp.get("sections") or {}
    findings = []

    for name in spec:
        body = sections.get(name)
        if not body:
            findings.append({"code": "MISSING_SECTION", "section": name, "severity": "error",
                             "detail": "%r is required by the RFP and absent" % name})
            continue
        text = body.get("text", "") if isinstance(body, dict) else str(body)
        if not text.strip():
            findings.append({"code": "EMPTY_SECTION", "section": name, "severity": "error",
                             "detail": "present but empty"})
            continue
        limit = (spec.get(name) or {}).get("max_words")
        words = len(text.split())
        if limit and words > int(limit):
            findings.append({"code": "OVER_LIMIT", "section": name, "severity": "error",
                             "detail": "%d words against %d" % (words, limit)})
        for token in placeholders(text):
            findings.append({"code": "PLACEHOLDER", "section": name, "severity": "error",
                             "detail": "unfilled %r" % token})
        missing = unsupported_numbers(text, [rfp, profile or {}, references])
        if missing:
            findings.append({"code": "UNSUPPORTED_NUMBER", "section": name, "severity": "warn",
                             "detail": "no source for: " + ", ".join(missing)})

    for name in sections:
        if spec and name not in spec:
            findings.append({"code": "UNKNOWN_SECTION", "section": name, "severity": "warn",
                             "detail": "not required by the RFP"})
    errors = [row for row in findings if row["severity"] == "error"]
    return {"ok": not errors, "errors": len(errors), "warnings":
            len(findings) - len(errors), "findings": findings}


# -- sealing ----------------------------------------------------------------

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def seal(value):
    """A sha256 over the canonical form, with the seal key excluded from the hash."""
    body = {key: value[key] for key in value if key != "seal"}
    digest = hashlib.sha256(canonical(body)).hexdigest()
    sealed = dict(value)
    sealed["seal"] = {"algorithm": "sha256", "digest": digest, "bytes": len(canonical(body))}
    return sealed


def verify_seal(value):
    """(ok, detail) — a tampered body or a missing seal is a named failure."""
    if not isinstance(value, dict) or not isinstance(value.get("seal"), dict):
        return False, "no seal present"
    claimed = value["seal"].get("digest")
    body = {key: value[key] for key in value if key != "seal"}
    actual = hashlib.sha256(canonical(body)).hexdigest()
    if claimed != actual:
        return False, "digest mismatch: seal %s, body %s" % (str(claimed)[:16], actual[:16])
    return True, "seal %s over %d bytes" % (actual[:16], value["seal"].get("bytes", 0))
