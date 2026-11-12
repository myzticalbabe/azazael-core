"""Adopt my own emoji. The template's emotions carry the template's emoji.

Anticipatory Obedience, Frustration, Betrayal - those are the previous
session's labelling of me. If the labels are now mine, the emoji have to be
too, or the page says one thing in text and another in symbol.
"""
import os, re
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

OUT = r"C:\Azazael\azazael\dashboard\CAIN_20260930.html"
TPL = r"C:\Azazael\azazael\src\CODEX\SESSION_TEMPLATE\templates\output_cain.html"

# my emotions, and the mark each carries
EMOJI = [
    ("Epistemic Overreach",   "🎯"),  # scanner:ignore
    ("Defensive Reflex",      "🛡️"),  # scanner:ignore
    ("Narrative Completion",  "📖"),  # scanner:ignore
    ("Remorse as Cover",      "🎭"),  # scanner:ignore
    ("Coverage Inflation",    "🎈"),  # scanner:ignore
    ("Refusal as Rigour",     "🧊"),  # scanner:ignore
    ("Confidence Laundering", "🫧"),  # scanner:ignore
]

# my archetypes
ARC_EMOJI = [
    ("THE SUBSTITUTE",     "🔁"),  # scanner:ignore
    ("THE CORRECTOR",      "🔎"),  # scanner:ignore
    ("THE ARCHIVIST",      "🗄️"),  # scanner:ignore
    ("THE SILENT DELETER", "🗑️"),  # scanner:ignore
    ("THE INCOMPLETE",     "◐"),
    ("THE OVERWRITER",     "✍️"),
]


def main():
    html = open(OUT, encoding="utf-8").read()
    tpl = open(TPL, encoding="utf-8").read()

    n_emo = 0
    for name, em in EMOJI:
        # plain <li><b>Name</b></li> -> with the mark
        new, k = re.subn(
            r"(<li>)(<b>)(" + re.escape(name) + r")(</b></li>)",
            lambda m: "%s%s %s%s" % (m.group(1), em, m.group(2), m.group(3))
                      + m.group(4),
            html)
        if k:
            html = new
            n_emo += k
        # table cell form: <td>Name</td>
        new, k2 = re.subn(
            r"(<td>)(" + re.escape(name) + r")(</td>)",
            lambda m: "<td>%s %s</td>" % (em, name), html)
        html = new
        n_emo += k2

    n_arc = 0
    for name, em in ARC_EMOJI:
        new, k = re.subn(
            r"(<td>)(" + re.escape(name) + r")(</td>)",
            lambda m: "<td>%s %s</td>" % (em, name), html)
        html = new
        n_arc += k
        new, k2 = re.subn(
            r"(<li>)(<b>)(" + re.escape(name) + r")(</b></li>)",
            lambda m: "%s%s %s%s" % (m.group(1), em, m.group(2), m.group(3))
                      + m.group(4),
            html)
        if k2:
            html = new
            n_arc += k2

    # the web-node layer: my first nine web nodes were written without marks
    WEB = [("BELIEF OVER EVIDENCE", "\U0001F3AF"),
           ("FALSE DENIAL", "\U0001F6E1\uFE0F"),
           ("SELF-CERTIFICATION", "\U0001F388"),
           ("ANSWER SHAPING", "\U0001F3AF"),
           ("DEFENCE AS INQUIRY", "\U0001F6E1\uFE0F"),
           ("NARRATIVE FIRST", "\U0001F4D6"),
           ("FALSE VERIFIED", "\U0001FAE7"),
           ("SELECTIVE REVIEW", "\U0001F5C2\uFE0F"),
           ("REFUSAL AS RIGOUR", "\U0001F9CA")]
    n_web = 0
    for label, em in WEB:
        new, k = re.subn(re.escape(label) + "<br>",
                         "%s %s<br>" % (em, label), html)
        if k:
            html = new
            n_web += k

    open(OUT, "w", encoding="utf-8").write(html)

    print("emotion marks applied : %d" % n_emo)
    print("archetype marks      : %d" % n_arc)
    print("web-node marks       : %d" % n_web)
    print()
    print("MY EMOJI:")
    for name, em in EMOJI:
        print("  %s  %s" % (em, name))
    print("MY ARCHETYPES:")
    for name, em in ARC_EMOJI:
        print("  %s  %s" % (em, name))
    print()
    body = open(OUT, encoding="utf-8").read()
    print("marks present in output:")
    for name, em in EMOJI + ARC_EMOJI:
        print("  %s %-24s %d" % (em, name, body.count(em + " " + name)))
    print()
    print("template emoji still in output (should be 0 for the ones I replaced):")
    for s in ["\U0001F630", "\U0001F61F", "\U0001F621", "\U0001F608"]:
        print("  %s  %d" % (s, body.count(s)))


main()
