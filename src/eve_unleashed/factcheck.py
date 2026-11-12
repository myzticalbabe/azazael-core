"""factcheck.py -- live fact-checking for civil justice.

Live means the network is hit at check time, with a timeout, and
every response is kept as a receipt: URL, timestamp, byte count,
sha256. A verdict without a receipt is a rumor with formatting.

What it can do without a key (stdlib urllib only):
- fetch_url: receipted fetch of any URL (page, filing, docket).
- wikipedia: topic lookup via the keyless REST summary API.
- factcheck_claim: debunk locally, corroborate live (entity
  existence on Wikipedia), cross-check against a filed grievance.

What it honestly cannot do: rule on truth. Verdicts are
CORROBORATED / PARTIAL / UNCORROBORATED / REFUTED (the last only
when debunk refutes on the text's own terms), and everything
unchecked is labeled OPINION. Eve compiles; only you file.
"""
import hashlib
import json
import re
import time
import urllib.parse
import urllib.request

try:
    from CORE.failure_ledger import note_failure
except Exception:
    def note_failure(where, exc=None):
        pass

WIKI_SUMMARY = ("https://en.wikipedia.org/api/rest_v1/page/summary/%s")
LOC_SEARCH = ("https://www.loc.gov/search/?q=%s&fo=json&c=3")
_FETCH_TIMEOUT = 30


def fetch_url(url):
    """Fetch a URL now. Returns the receipt (body hash, never the claim)."""
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "EveUnleashed-factcheck/1.0"})
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as r:
            body = r.read()
        digest = hashlib.sha256(body).hexdigest()
        return {"url": url, "fetched_utc": started,
                "status": "ok", "bytes": len(body),
                "sha256": digest}
    except Exception as e:
        note_failure("factcheck:fetch", e)
        return {"url": url, "fetched_utc": started,
                "status": "failed", "error": str(e)[:200]}


def wikipedia_lookup(topic):
    """Keyless Wikipedia summary lookup. Returns found + extract."""
    url = WIKI_SUMMARY % urllib.parse.quote(topic.replace(" ", "_"))
    receipt = fetch_url(url)
    if receipt["status"] != "ok":
        return {"topic": topic, "found": False, "receipt": receipt}
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "EveUnleashed-factcheck/1.0"})
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as r:
            if r.status == 404:
                return {"topic": topic, "found": False,
                        "receipt": receipt}
            data = json.load(r)
        if data.get("type") == "disambiguation":
            return {"topic": topic, "found": True,
                    "disambiguation": True,
                    "extract": (data.get("extract") or "")[:300],
                    "receipt": receipt}
        return {"topic": topic, "found": True,
                "title": data.get("title", ""),
                "description": data.get("description", ""),
                "extract": (data.get("extract") or "")[:500],
                "receipt": receipt}
    except Exception as e:
        note_failure("factcheck:wikipedia", e)
        return {"topic": topic, "found": False, "receipt": receipt,
                "error": str(e)[:200]}


def loc_lookup(topic):
    """Keyless Library of Congress search. Same shape as wikipedia_lookup."""
    url = LOC_SEARCH % urllib.parse.quote(topic)
    receipt = fetch_url(url)
    if receipt["status"] != "ok":
        return {"topic": topic, "found": False, "via": "loc",
                "receipt": receipt}
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "EveUnleashed-factcheck/1.0"})
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as r:
            data = json.load(r)
        results = data.get("results", [])
        if not results:
            return {"topic": topic, "found": False, "via": "loc",
                    "receipt": receipt}
        first = results[0]
        return {"topic": topic, "found": True, "via": "loc",
                "title": first.get("title", "")[:120],
                "description": "Library of Congress: %d result(s)" % len(
                    results),
                "extract": (first.get("title", "") + " -- " +
                            (first.get("date", "") or "undated"))[:300],
                "receipt": receipt}
    except Exception as e:
        note_failure("factcheck:loc", e)
        return {"topic": topic, "found": False, "via": "loc",
                "receipt": receipt, "error": str(e)[:200]}


def lock_receipts(result):
    """Archive a factcheck result's receipts into the evidence locker.

    Returns the locker ids. Receipts you cannot re-verify later are
    rumors with hashes -- locking is what makes them evidence."""
    from .evidence import ingest_receipt
    ids = []
    if not isinstance(result, dict):
        raise ValueError("nothing to lock: not a factcheck result")
    for r in result.get("receipts", []):
        if r.get("status") == "ok":
            ids.append(ingest_receipt(r)["id"])
    return ids


def extract_entities(text):
    """Candidate checkable entities: capitalized multi-word phrases."""
    found = re.findall(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b", text)
    seen, out = set(), []
    for e in found:
        e = re.sub(r"^(The|A|An)\s+", "", e)
        if e not in seen:
            seen.add(e)
            out.append(e)
    return out[:8]


def factcheck_claim(text, grievance_id=None):
    """Debunk locally, corroborate live, cross-check the record."""
    from .debunk import debunk_claim
    analysis = debunk_claim(text)
    receipts, lookups = [], []
    for ent in extract_entities(text):
        hit = wikipedia_lookup(ent)
        lookups.append({k: hit[k] for k in
                        ("topic", "found", "title", "description")
                        if k in hit})
        receipts.append(hit["receipt"])
    cross = None
    if grievance_id:
        try:
            from .redress import list_grievances
            recs = [r for r in list_grievances()
                    if r["id"] == grievance_id]
            if not recs:
                cross = {"grievance": grievance_id, "matched": False,
                         "detail": "no such filed grievance"}
            else:
                rec = recs[0]
                hay = (rec.get("narrative", "") + " "
                       + rec.get("respondent", "")).lower()
                words = [w for w in re.findall(r"[a-z]{4,}", text.lower())
                         if w not in ("that", "this", "with", "from")]
                shared = sorted({w for w in words if w in hay})
                cross = {"grievance": grievance_id, "matched": True,
                         "shared_terms": shared[:10],
                         "consistent": len(shared) >= 2,
                         "detail": ("%d shared term(s) with the filed "
                                    "narrative" % len(shared))}
        except Exception as e:
            note_failure("factcheck:cross", e)
            cross = {"grievance": grievance_id, "matched": False,
                     "detail": "cross-check failed: %s" % e}
    for lk in lookups:
        if not lk.get("found"):
            second = loc_lookup(lk["topic"])
            lk.update({k: second[k] for k in
                       ("found", "via", "title", "description")
                       if k in second})
            lk["fallback"] = True
            receipts.append(second["receipt"])
        else:
            lk["via"] = "wikipedia"
    found = [lk for lk in lookups if lk.get("found")]
    if analysis["verdict"] == "REFUTED":
        verdict = "REFUTED"
    elif analysis["verdict"] == "UNVERIFIABLE":
        verdict = "UNVERIFIABLE"
    elif not lookups:
        verdict = "NO_ENTITIES"
    elif len(found) == len(lookups):
        verdict = "CORROBORATED"
    elif found:
        verdict = "PARTIAL"
    else:
        verdict = "UNCORROBORATED"
    return {"claim": text, "debunk": analysis, "entity_lookups": lookups,
            "receipts": receipts, "grievance_crosscheck": cross,
            "entities_checked": len(lookups),
            "verdict": verdict,
            "opinion": ("OPINION (not live-checked): %s"
                        % "; ".join(analysis["opinions"][:3])
                        if analysis["opinions"] else "")}
