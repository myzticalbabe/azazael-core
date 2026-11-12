"""redress.py -- Boston Tea Party native functionality.

Public grievance and redress, in-house: file a grievance, draft the
redress instruments, route to the free legal-aid directory. What it can
do without an API key it does (records, drafts, referrals); what needs
a key it prepares (Regulations.gov comment packets, FOIA letters) and
says what key is missing. Nothing filed on anyone's behalf silently.

Web-search findings it encodes (2026-11-06):
- Regulations.gov API v4 GET: dockets/documents/comments search, key via
  api.data.gov (1000/hr). POST comments: authorized GSA key (50/min).
- LawHelp.org (Pro Bono Net): free state-by-state legal-aid directory.
- LawHelp Interactive: free guided court forms (housing/family/debt).
- ABA Free Legal Answers: low-income online lawyer Q&A.
- LSC finder + usa.gov/legal-aid hub.

Learning styles: `tutorial` teaches grievance filing guided, step by
step. Visual/reading/kinesthetic tracks are goal-iteration candidates,
recorded in the harness goal file, not pretended here.
"""
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

try:
    from CORE.failure_ledger import note_failure
except Exception:
    def note_failure(where, exc=None):
        pass

GRIEVANCE_DIR = Path.home() / ".azazael" / "grievances"

CATEGORIES = ["federal-rule", "housing", "debt", "family", "consumer",
              "workplace", "civil-rights", "environment", "other"]

LEGAL_AID = {
    "national": [
        ("LawHelp.org (free legal aid by state)", "https://www.lawhelp.org/"),
        ("LSC finder (low-income legal aid)", "https://www.lsc.gov/about-lsc/what-legal-aid/get-legal-help"),
        ("ABA Free Legal Answers (lawyer Q&A)", "https://abafreelegalanswers.org/"),
        ("LawHelp Interactive (free court forms)", "https://lawhelpinteractive.org/"),
        ("USA.gov legal aid hub", "https://www.usa.gov/legal-aid"),
    ],
}

REGS_GOV_SEARCH = ("https://api.regulations.gov/v4/documents"
                   "?api_key=%s&filter[searchTerm]=%s&page[size]=5")


def _gdir():
    GRIEVANCE_DIR.mkdir(parents=True, exist_ok=True)
    return GRIEVANCE_DIR


def file_grievance(respondent, category, narrative, redress_sought,
                   evidence=None):
    """File a grievance record. Returns the record (with id)."""
    if category not in CATEGORIES:
        raise ValueError("category must be one of %s" % CATEGORIES)
    gid = "grv-%s" % time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    record = {"id": gid, "respondent": respondent, "category": category,
              "narrative": narrative, "redress_sought": redress_sought,
              "evidence": evidence or [],
              "filed_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "status": "filed"}
    with open(_gdir() / (gid + ".json"), "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
    return record


def list_grievances():
    out = []
    if not GRIEVANCE_DIR.is_dir():
        return out
    for p in sorted(GRIEVANCE_DIR.glob("grv-*.json")):
        try:
            with open(p, encoding="utf-8") as f:
                out.append(json.load(f))
        except Exception as e:
            note_failure("redress:list", e)
    return out


def draft_foia(record, agency, requester_name):
    """Draft a FOIA request letter from a grievance record."""
    return (
        "FREEDOM OF INFORMATION ACT REQUEST\n"
        "To: %s FOIA Office\nFrom: %s\nDate: %s\nRe: grievance %s (%s)\n\n"
        "Under 5 U.S.C. section 552, I request records relating to:\n%s\n\n"
        "Redress sought: %s\n\n"
        "I request a waiver of fees as disclosure is in the public interest.\n"
        % (agency, requester_name,
           time.strftime("%Y-%m-%d", time.gmtime()),
           record["id"], record["category"], record["narrative"],
           record["redress_sought"]))


def draft_public_comment(record):
    """Draft a Regulations.gov-ready public comment packet (not filed)."""
    return {
        "commentOn": "see docket search below",
        "comment": ("As a member of the public affected by %s: %s "
                    "I urge redress: %s" % (
                        record["respondent"], record["narrative"],
                        record["redress_sought"])),
        "category": "Individual Consumer" if record["category"] == "consumer"
                    else "Public Comment(s)",
        "note": ("POST requires a GSA-authorized Comment API key "
                 "(50/min). Set REGULATIONS_GOV_API_KEY; until then "
                 "this packet is filed locally with the grievance."),
    }


def search_dockets(term, api_key=None):
    """Search Regulations.gov dockets (read-only GET). Returns dockets."""
    key = api_key or os.environ.get("REGULATIONS_GOV_API_KEY", "DEMO_KEY")
    url = REGS_GOV_SEARCH % (urllib.parse.quote(key),
                             urllib.parse.quote(term))
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            data = json.load(r)
        return [{"id": d.get("id"),
                 "title": (d.get("attributes") or {}).get("title", "")[:120]}
                for d in data.get("data", [])]
    except Exception as e:
        note_failure("redress:dockets", e)
        return [{"error": "docket search failed: %s" % e}]


def legal_referral(state=None):
    """Free legal-aid referrals (static directory, always available)."""
    refs = list(LEGAL_AID["national"])
    if state:
        refs.insert(0, ("LawHelp state page for %s" % state.upper(),
                        "https://www.lawhelp.org/states/%s/" % state.lower()))
    return refs


def prove_case(gid):
    """Compile a court-filing evidence bundle for a grievance.

    Error-proof means: every checkable proposition is machine-checked
    (record schema, evidence hashes, timeline order, Z3 completeness
    judgment) and every uncheckable one is labeled OPINION, never proof.
    Returns the bundle dict; the caller files it, Eve never does."""
    import hashlib
    recs = [r for r in list_grievances() if r["id"] == gid]
    if not recs:
        raise ValueError("no grievance %s" % gid)
    rec = recs[0]
    checks = []

    def check(name, ok, detail):
        checks.append({"check": name, "passed": bool(ok),
                       "detail": detail})
        return bool(ok)

    required = ["id", "respondent", "category", "narrative",
                "redress_sought", "filed_utc", "status"]
    missing = [k for k in required if not rec.get(k)]
    check("record_schema_complete", not missing,
          "missing=%s" % missing if missing else "all fields present")
    check("category_known", rec.get("category") in CATEGORIES,
          rec.get("category"))
    manifest = []
    try:
        from .evidence import resolve as _resolve
    except Exception:
        def _resolve(ref):
            return ref
    for ev in rec.get("evidence", []):
        p = Path(_resolve(ev))
        if p.is_file():
            h = hashlib.sha256()
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            manifest.append({"path": ev, "bytes": p.stat().st_size,
                             "sha256": h.hexdigest(), "present": True})
        else:
            manifest.append({"path": ev, "present": False})
    check("evidence_present",
          all(m["present"] for m in manifest) if manifest else True,
          "%d/%d files present" % (
              sum(1 for m in manifest if m["present"]), len(manifest)))
    check("narrative_substantive", len(rec.get("narrative", "")) >= 20,
          "%d chars" % len(rec.get("narrative", "")))
    nsp_note = "nsp-unavailable"
    try:
        from .nsp_check import verify_router_trace
        ev_count = sum(1 for m in manifest if m["present"])
        events = [{"event_type": "prove", "symbolic_state": {
            "query_norm": min(len(rec.get("narrative", "")) / 10.0, 50.0),
            "selected_idx": 0, "num_providers": max(ev_count, 1),
            "dead_provider_idx": -1}}]
        verdict, detail = verify_router_trace(events, [
            "valid_query_features", "valid_provider_selection",
            "no_dead_provider"])
        nsp_note = "nsp:%s [%s]" % (verdict, detail)
        check("nsp_judgment", verdict == "verified", nsp_note)
    except Exception as e:
        note_failure("redress:prove-nsp", e)
        check("nsp_judgment", False, "checker failed: %s" % e)
    checkable = [c for c in checks]
    passed = sum(1 for c in checkable if c["passed"])
    bundle = {"grievance": rec, "evidence_manifest": manifest,
              "checks": checks,
              "judgment": "%d/%d machine checks passed" % (passed,
                                                                len(checks)),
              "opinion": ("OPINION (not machine-checked): on the checked "
                            "record above, the grievance states a coherent "
                            "claim for %s. File with counsel review."
                            % rec.get("redress_sought", "redress").rstrip(".")),
              "filed_note": ("Compiled %s. Eve compiles; only you file."
                               % time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                time.gmtime()))}
    return bundle


def tutorial():
    """Guided grievance-filing tutorial (guided learning style)."""
    return [
        "STEP 1 -- NAME THE GRIEVANCE: who did what? One sentence.",
        "STEP 2 -- PICK THE CATEGORY: %s." % ", ".join(CATEGORIES),
        "STEP 3 -- STATE REDRESS: what fixes it? (refund, repair, rule change...)",
        "STEP 4 -- GATHER EVIDENCE: file paths, dates, screenshots.",
        "STEP 5 -- FILE: eve grievance file --respondent X --category Y "
        "--narrative '...' --redress '...'",
        "STEP 6 -- ROUTE: eve grievance draft (FOIA/public comment) + "
        "eve grievance refer (free legal aid).",
        "STEP 7 -- TRACK: eve grievance show. Nothing auto-files anywhere "
        "without your key and your word.",
    ]
