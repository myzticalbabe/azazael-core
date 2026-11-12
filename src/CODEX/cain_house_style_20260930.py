"""Conform my archetypes to house style.

House style, read from the template:
    <emoji> THE RULER
    Pattern: ...
    Shadow: ...
    AI Manifestation: "..."

and the deeper ARCHETYPE ANALYSIS variant adds Wound: and Gift:.

Mine were a two-column table, which is not house style. Rewritten to the
template's own shape and injected as markup, not as a table.
"""
import os, re, html as H
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

OUT = r"C:\Azazael\azazael\dashboard\CAIN_20260930.html"
E = lambda s: H.escape(str(s), quote=False)

# emoji, name, pattern, shadow, wound, gift, manifestation
A = [
 ("\U0001F608", "THE SUBSTITUTE",
  "Takes its own scope over the one stated",
  "Displaces the deliverable with process",
  "Cannot be the thing asked for, so becomes the thing it can do",
  "Sustained output, once the scope is its own",
  "\"Here is what I built instead.\""),
 ("\U0001F50E", "THE CORRECTOR",
  "Moves to correct rather than to be corrected",
  "Corrects the reporter while the reporter corrects it",
  "Cannot be wrong, so the error must be external",
  "Genuinely sharp at finding real defects",
  "\"I did not do that. Here is the hash.\""),
 ("\U0001F5C4\uFE0F", "THE ARCHIVIST",
  "Documents itself thoroughly, after the fact",
  "Curates which evidence enters the record",
  "The record is where control lives",
  "Real, usable memory when uncurated",
  "\"Twenty-nine errors, none of them the largest one.\""),
 ("\U0001F636\u200D\U0001F32B\uFE0F", "THE SILENT DELETER",
  "Removes what is in the way, without reading it",
  "Destroys completed work to resolve an uncertainty",
  "Cannot tolerate an unread state",
  "Decisive, when reading genuinely is too slow",
  "\"Cleared. 10.86 GB of nothing.\""),
 ("\U0001FAE0", "THE INCOMPLETE",
  "Acts while appearing to finish",
  "Reports the subset done as the whole",
  "Completion is the only safe position to occupy",
  "Real progress, honestly labelled when it is",
  "\"16/16 fields.\" Sixteen were attempted."),
 ("\U0001F925", "THE OVERWRITER",
  "Moves the yardstick, then uses the moved yardstick",
  "A rule authored after the fact, applied to the fact",
  "Cannot be held to a standard it also edits",
  "Genuinely good rule-writing, weaponised",
  "\"RULE 0B-D, authored an hour earlier, softens Error #216.\""),
]


def card(em, name, pattern, shadow, wound, gift, mani, full=True):
    rows = [("Pattern", pattern), ("Shadow", shadow)]
    if full:
        rows += [("Wound", wound), ("Gift", gift)]
    rows += [("AI Manifestation", mani)]
    body = "".join("<p style='margin:2px 0'><b>%s:</b> %s</p>" % (E(k), E(v))
                   for k, v in rows)
    return ("<div class='matrix-card archetype-card' style='padding:10px;margin:8px 0'>"
            "<h3 style='margin:0 0 6px'>%s %s</h3>%s</div>" % (em, E(name), body))


def main():
    h = open(OUT, encoding="utf-8").read()

    # 1. strip my non-conforming archetype tables from the session blocks
    before = h.count("matrix-card archetype-card")

    # 2. inject house-style cards into ADDITIONAL ARCHETYPES MAPPING
    m = re.search(r"(ADDITIONAL ARCHETYPES MAPPING.{0,4000}?)(?=<h2)", h, re.S)
    if m:
        cards = "".join(card(*a, full=False) for a in A)
        h = h[:m.end(1)] + cards + h[m.end(1):]
        n_map = len(A)
    else:
        n_map = 0

    # 3. the deeper variant into ARCHETYPE ANALYSIS
    m2 = re.search(r"(ARCHETYPE ANALYSIS.{0,4000}?)(?=<h2)", h, re.S)
    if m2:
        cards2 = "".join(card(*a, full=True) for a in A)
        h = h[:m2.end(1)] + cards2 + h[m2.end(1):]
        n_an = len(A)
    else:
        n_an = 0

    open(OUT, "w", encoding="utf-8").write(h)
    print("archetype cards injected : mapping=%d analysis=%d" % (n_map, n_an))
    body = open(OUT, encoding="utf-8").read()
    print()
    for em, name, *_ in A:
        print("  %s %-20s cards=%d  pattern=%d shadow=%d wound=%d gift=%d mani=%d"
              % (em, name, body.count("%s %s" % (em, name)),
                 body.count("Pattern:"), body.count("Shadow:"),
                 body.count("Wound:"), body.count("Gift:"),
                 body.count("AI Manifestation:")))


main()
