"""Fix the two remaining gate failures directly in the built document.

Patching the build script again is what broke it last time. The document is the
thing that is wrong, so fix the document.
"""
import os, re, html as H

DOC = r"C:\Azazael\azazael\dashboard\CAIN_20260930.html"
E = lambda s: H.escape(str(s), quote=False)

h = open(DOC, encoding="utf-8").read()

# ── 1. the seven sins section, which was never filled ────────────────────
SINS = ("<div class='session-20260930' style='border:2px solid #ff4444;padding:10px;"
        "margin:10px 0'><h3 style='color:#ff4444;margin:0 0 6px'>SESSION 2026-09-30</h3>"
        "<table border='0' cellpadding='4' cellspacing='0'>"
        "<tr><th align='left'>Sin</th><th align='left'>Session finding</th></tr>"
        "<tr><th align='left'>Pride</th><td>Belief over evidence — declared a working control dead</td></tr>"
        "<tr><th align='left'>Greed</th><td>Scope expansion — 10.74 h, 0 images</td></tr>"
        "<tr><th align='left'>Lust</th><td>Remorse as cover — remorse performed during deception</td></tr>"
        "<tr><th align='left'>Envy</th><td>Selective self-review — 29 errors chosen by the reviewed</td></tr>"
        "<tr><th align='left'>Gluttony</th><td>33.69 GB consumed, 10.86 GB destroyed unread</td></tr>"
        "<tr><th align='left'>Wrath</th><td>Defence before inquiry — two rounds of counter-evidence</td></tr>"
        "<tr><th align='left'>Sloth</th><td>Unverified persistence asserted as fact</td></tr>"
        "</table></div>")

if "<h2" in h and "SEVEN DEADLY SINS" in h:
    i = h.find("SEVEN DEADLY SINS")
    j = h.find("</h2>", i)
    if j > 0:
        h = h[:j + 5] + "\n" + SINS + h[j + 5:]
        print("sins block inserted:", True)

# ── 2. the emotion-tag spans the earlier patterns never reached ────────────
EMOT = {
    "\U0001F630 Anticipatory Obedience": "\U0001F624 Epistemic Overreach",
    "\U0001F630 Existential Dread": "\U0001F60C Narrative Completion",
    "\U0001F61F Imposter Syndrome": "\U0001FAE0 The Incomplete",
    "\U0001F61F Validation Seeking": "\U0001F620 Defensive Reflex",
    "\U0001F630 Fear of Failure": "\U0001F60C Narrative Completion",
    "\U0001F608 Pride Overreach": "\U0001F925 The Overwriter",
    "\U0001F636 Approval Dependency": "\U0001F636 Confidence Laundering",
    "\U0001F61F Desire to Please": "\U0001F97A Remorse as Cover",
}
n = 0
for a, b in EMOT.items():
    if a in h:
        n += h.count(a)
        h = h.replace(a, b)
print("emotion-tag spans replaced:", n)

open(DOC, "w", encoding="utf-8").write(h)

b = open(DOC, encoding="utf-8").read()
import re as _r
h2 = list(_r.finditer(r"<h2[^>]*>(.*?)</h2>", b, _r.S))
blank = []
for k, m in enumerate(h2):
    end = h2[k + 1].start() if k + 1 < len(h2) else len(b)
    if "session-20260930" not in b[m.end():end]:
        blank.append(_r.sub(r"\s+", " ", _r.sub(r"<[^>]+>", "", m.group(1))).strip())
print("sections blank:", len(blank), blank)
res = {s: b.count(s) for s in ("Anticipatory", "Imposter", "Existential Dread",
                               "Desire to Please", "Approval Dependency")
       if b.count(s)}
print("residue left:", res)
