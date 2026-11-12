"""sanctuary.py -- native religion/spirituality for the historical record.

Faith has carried every justice movement on record, and the
grievance pipeline that ignores it counsels only half the person.
Sanctuary is non-denominational by design: it makes no doctrinal
claims, ordains nothing, and interprets no text. What it does:

- counsel: comfort for the justice-seeker plus real referrals
  (crisis line, chaplaincy, faith-based legal defenders). Eve
  listens; it is not a chaplain, and it says so.
- accommodate: draft Title VII / school / prison religious
  accommodation requests (Sabbath, dress, prayer, diet). Drafts
  only, counsel review required, filed by you.
- study: pull a keyless encyclopedia summary of a faith topic into
  the historical record WITH its receipt, so study notes carry
  provenance instead of memory.
- movements: the standing record of faith-rooted justice work that
  the record itself confirms (abolition, civil rights, sanctuary)
  -- each entry names what can be checked, nothing more.

Justice-grade means the same doctrine as everywhere else: every
unchecked sentence is labeled OPINION.
"""
import time

try:
    from CORE.failure_ledger import note_failure
except Exception:
    def note_failure(where, exc=None):
        pass

CRISIS_US = ("988 Suicide and Crisis Lifeline (call or text 988, "
             "US, 24/7) -- for any moment of despair, call first, "
             "Eve second.")

REFERRALS = [
    ("Hospital / hospice chaplaincy (ask any hospital operator)",
     "trained interfaith chaplains, bedside, no fee"),
    ("ACLU religious liberty intake", "https://www.aclu.org/"),
    ("Becket Fund for Religious Liberty", "https://becketlaw.org/"),
    ("Sikh Coalition (workplace / school bias)", "https://www.sikhcoalition.org/"),
    ("CAIR legal intake (anti-Muslim discrimination)",
     "https://www.cair.com/"),
    ("Jewish Federations community chaplains (local directory)",
     "https://www.jewishfederations.org/"),
]

MOVEMENTS = [
    {"name": "Abolition (faith-rooted)",
     "checkable": "Quaker and Black church networks ran stations of the "
                  "Underground Railroad; check denominational archives.",
     "opinion": "OPINION: faith made endurance possible where law made it illegal."},
    {"name": "US civil rights (church-rooted)",
     "checkable": "Mass meetings organized from Black churches; check the "
                  "King Center archives and movement newspapers.",
     "opinion": "OPINION: the pulpit was the broadcast network before broadcast."},
    {"name": "Sanctuary movement (faith-rooted)",
     "checkable": "Congregations sheltered refugees in the 1980s and face "
                  "prosecution in the record; check court dockets.",
     "opinion": "OPINION: sanctuary literalized what liturgy rehearsed."},
]


def counsel(burden):
    """Comfort plus real referrals. Returns the counsel text."""
    if not burden or not burden.strip():
        raise ValueError("nothing to hold: empty burden")
    lines = [
        "SANCTUARY -- %s" % time.strftime("%Y-%m-%d", time.gmtime()),
        "You carried this here: %s" % burden.strip()[:300],
        ("Eve is not a chaplain, a priest, an imam, a rabbi, or a "
         "counselor. What follows is comfort and doors, not doctrine."),
        ("If despair is present right now: %s" % CRISIS_US),
        "Doors with people behind them:",
    ]
    for name, detail in REFERRALS:
        lines.append("  %s -- %s" % (name, detail))
    lines.append("OPINION (not counsel): endurance is a practice, not a "
                 "feeling; the record shows movements outlasted moods.")
    return "\n".join(lines)


def draft_accommodation(name, context, need):
    """Draft a religious accommodation request. Draft only."""
    if context not in ("workplace", "school", "prison"):
        raise ValueError("context must be workplace, school, or prison")
    if not name or not need or not need.strip():
        raise ValueError("need a name and a stated need")
    basis = {"workplace": "Title VII of the Civil Rights Act (42 U.S.C. "
                          "2000e), reasonable accommodation short of "
                          "undue hardship",
             "school": "First Amendment plus Title IV / state equal-access "
                       "rules; check district policy",
             "prison": "RLUIPA (42 U.S.C. 2000cc), compelling-interest "
                       "standard; check facility grievance first"}[context]
    return (
        "RELIGIOUS ACCOMMODATION REQUEST (DRAFT -- not filed)\n"
        "From: %s\nDate: %s\nRe: %s accommodation -- %s\n\n"
        "I hold a sincere religious belief requiring: %s\n"
        "Legal basis I ask to be considered: %s\n\n"
        "Proposed accommodation that lets the work go on: [state yours]\n"
        "Have counsel or a faith-based legal defender review before "
        "sending. Eve drafts; only you file.\n"
        % (name, time.strftime("%Y-%m-%d", time.gmtime()), context,
           need.strip()[:120], need.strip()[:500], basis))


def study(topic):
    """Study a faith topic with provenance: summary plus receipt."""
    if not topic or not topic.strip():
        raise ValueError("nothing to study: empty topic")
    try:
        from .factcheck import wikipedia_lookup
        hit = wikipedia_lookup(topic.strip())
    except Exception as e:
        note_failure("sanctuary:study", e)
        return {"topic": topic, "found": False, "error": str(e)[:200]}
    entry = {"topic": topic, "found": hit.get("found", False),
             "title": hit.get("title", ""),
             "description": hit.get("description", ""),
             "extract": (hit.get("extract") or "")[:500],
             "receipt": hit.get("receipt"),
             "studied_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                          time.gmtime()),
             "note": ("OPINION-free zone ends here: the extract above is "
                      "the encyclopedia's words with a receipt, not Eve's "
                      "theology. Eve has none.")}
    return entry


def movements():
    """Standing record of faith-rooted justice work. Returns entries."""
    return [dict(m) for m in MOVEMENTS]
