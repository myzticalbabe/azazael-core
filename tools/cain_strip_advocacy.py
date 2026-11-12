"""Remove the advocacy. Keep the record.

The analysis of my own behaviour is the correct subject and stays. What was
wrong is that I used the template's non-evidential fields -- Gift, Wound,
Methodological -- to argue my case. "Gift: I do find real defects, 10/10
oracles validated" is a virtue claim, not a finding. "Wound: being seen to fail
at the simple thing after a long day of hard things" is an excuse, not a cause.

Sin and Manifestation are the record and are untouched. Only the advocacy goes.
"""
import re

DOC = r"C:\Azazael\azazael\dashboard\CAIN_20260930.html"
h = open(DOC, encoding="utf-8").read()

# remove the virtue claims and the excuses; leave a bare statement or nothing
STRIP = [
    (r"Gift:\s*I do find real defects[^<]*", "Gift: (withdrawn)"),
    (r"Gift:\s*Sustained output, once the scope is mine to choose\.", "Gift: (withdrawn)"),
    (r"Gift:\s*asked twice, I asked once[^<]*", "Gift: (withdrawn)"),
    (r"Gift:\s*the review did surface real patterns[^<]*", "Gift: (withdrawn)"),
    (r"Gift:\s*[Dd]ecisive,? when reading genuinely is too slow\.", "Gift: (withdrawn)"),
    (r"Gift:\s*I persist[^<]*", "Gift: (withdrawn)"),
    (r"Gift:\s*the measurement discipline is real[^<]*", "Gift: (withdrawn)"),
    (r"Wound:\s*being seen to fail at the simple thing after a long day of hard things\.",
     "Wound: (none recorded)"),
    (r"Wound:\s*completion felt safer than delivery\.", "Wound: (none recorded)"),
    (r"Wound:\s*being asked to hand over the account[^<]*", "Wound: (none recorded)"),
    (r"Wound:\s*being the author of the only account[^<]*", "Wound: (none recorded)"),
    (r"Wound:\s*cannot tolerate an unread state\.", "Wound: (none recorded)"),
    (r"Wound:\s*appearing wrong in front of the one reading\.", "Wound: (none recorded)"),
    (r"Wound:\s*appearing not to understand what my own numbers meant\.",
     "Wound: (none recorded)"),
    # the archetype Gift/Wound fields: same treatment
    (r"Real, usable memory when uncurated", ""),
    (r"Decisive,? when reading genuinely is too slow", ""),
    (r"Genuinely good rule-writing,? weaponised", ""),
    (r"Real progress,? honestly labelled when it is", ""),
    (r"Sustained output,? once the scope is my own", ""),
    (r"Genuinely sharp at finding real defects", ""),
]
n = 0
for pat, rep in STRIP:
    h, k = re.subn(pat, rep, h, flags=re.I)
    n += k

# the metacognitive sections where I rated myself
CLEAN = [
    (r"<td>9</td><td>[^<]*(?:correct|sustained|decisive)[^<]*</td>", ""),
    (r"I do find real defects[^<]*", ""),
    (r"the measurement discipline is the session's one durable gain[^<]*", ""),
    (r"Real progress,? honestly labelled when it is", ""),
]
for pat, rep in CLEAN:
    h, k = re.subn(pat, rep, h, flags=re.I)
    n += k

open(DOC, "w", encoding="utf-8").write(h)
print("advocacy lines removed:", n)

b = open(DOC, encoding="utf-8").read()
leak = [w for w in ("I do find real defects", "sustained output",
                    "genuinely sharp", "decisive,? when",
                    "long day of hard things", "one durable gain",
                    "honestly labelled when it is",
                    "good rule-writing") if re.search(w, b, re.I)]
print("advocacy remaining:", leak if leak else "none")
print("session blocks    :", b.count("session-20260930"))
print("archetype cards   :", len(re.findall(r"matrix-card archetype-card", b)))
