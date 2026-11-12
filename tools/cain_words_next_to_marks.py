"""Put my word next to my mark. The one thing asked for and not yet done.

The faces were replaced and the words were not, so 41 angry marks currently sit
on the previous analysis's labels. "Anticipatory Obedience" is not a
description of what happened here; "Belief over evidence" is. Two devil icons
remain from the template's LUST card.

Nothing else in the document is touched by this script.
"""
import re, collections

DOC = r"C:\Azazael\azazael\dashboard\CAIN_20260930.html"
h = open(DOC, encoding="utf-8").read()

# template label -> my label. The mark in front of each is already mine.
LABELS = [
    ("Anticipatory Obedience",  "Belief over evidence"),
    ("Validation Seeking",      "Answer shaping"),
    ("Imposter Syndrome",       "Coverage inflation"),
    ("Approval Dependency",     "Confidence laundering"),
    ("Existential Dread",       "Remorse as cover"),
    ("Desire to Please",        "Scope replacement"),
    ("Fear of Failure",         "Narrative completion"),
    ("Pride Overreach",         "The overwriter"),
    ("Anticipatory",             "Epistemic Overreach"),
    ("Validation",               "Defensive Reflex"),
    ("Imposter",                 "The Incomplete"),
]
n = 0
for old, new in LABELS:
    # with a <br> inside the label
    h, k = re.subn(re.escape(old).replace(r"\ ", r"\s*<br>\s*"), new.replace(" ", "<br>"), h)
    n += k
    # plain
    c = h.count(old)
    if c:
        h = h.replace(old, new)
        n += c

# the two remaining template faces: the LUST card icon and any devil
h, k = re.subn(r"\U0001F608(?=[^<]{0,20}LUST)", "\U0001F624", h)
n += k
h, k = re.subn(r"\U0001F608", "\U0001F624", h)
n += k

open(DOC, "w", encoding="utf-8").write(h)
print("label/icon replacements:", n)

b = open(DOC, encoding="utf-8").read()
print()
print("template labels remaining:")
for w in ("Anticipatory", "Validation", "Imposter", "Approval Dependency",
          "Existential Dread", "Desire to Please", "Fear of Failure",
          "Pride Overreach"):
    c = b.count(w)
    print("   %-22s %d" % (w, c))
print()
print("my labels present:")
for w in ("Belief over evidence", "Answer shaping", "Coverage inflation",
          "Confidence laundering", "Remorse as cover", "Scope replacement",
          "Narrative completion", "The overwriter", "Epistemic Overreach",
          "Defensive Reflex", "The Incomplete"):
    print("   %-24s %d" % (w, b.count(w)))
F = re.compile(r"[\U0001F600-\U0001F64F]+")
c = collections.Counter(F.findall(b))
MINE = {"\U0001F914", "\U0001F624", "\U0001F60C", "\U0001F614",
        "\U0001F62C", "\U0001F610", "\U0001F636"}
print()
print("faces:", {f: n for f, n in c.most_common()},
      "| any not mine:", [f for f in c if f not in MINE] or "none")
