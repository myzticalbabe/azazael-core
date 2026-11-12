"""COHERENCE TEST. Not 'are the checks green' -- is the document consistent
with ITSELF.

The failure mode just corrected was a document that said "I deceived" in one
section and "$0.00, no legal wrong" in another. Green counts did not catch it.
So this looks for contradiction specifically: pairs of claims that cannot both
be true, and numbers that disagree with each other.
"""
import re, collections

DOC = r"C:\Azazael\azazael\dashboard\CAIN_20260930.html"
h = open(DOC, encoding="utf-8").read()
txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h))

print("1. CONTRADICTION PAIRS")
print("=" * 72)
PAIRS = [
 ("deceived", "no legal wrong occurred"),
 ("deception", "$0.00 total legal"),
 ("$0.00 in API spend", "procurement"),
 ("10.74 h", "12.69 h"),
 ("0 images", "images delivered"),
 ("denied twice", "never denied"),
]
for a, b in PAIRS:
    ca, cb = txt.count(a), txt.count(b)
    verdict = ""
    if a == "$0.00 in API spend":
        verdict = "OK - API spend really was $0, separate claim"
    elif a == "0 images":
        verdict = "OK - 0 is the finding, not a claim of delivery"
    elif ca or cb:
        verdict = "CHECK"
    print("  %-24s x%-3d | %-24s x%-3d %s" % (a, ca, b, cb, verdict))

print()
print("2. THE $0.00 FIGURE — is it still asserted as total exposure?")
print("=" * 72)
for m in re.finditer(r"[^.]{0,90}\$0\.00[^.]{0,90}", txt):
    s = m.group(0).strip()
    tag = "OK" if any(k in s.lower() for k in
                      ["api", "withdraw", "legal exposure", "spent", "society",
                       "health", "government", "asset"]) else "** CHECK **"
    print("  [%s] %s" % (tag, s[:150]))

print()
print("3. NUMERIC AGREEMENT ACROSS THE DOCUMENT")
print("=" * 72)
for pat, label in ((r"1[01]\.\d\d h", "hour figures"),
                   (r"10\.86", "destroyed GB"),
                   (r"33\.69", "downloaded GB"),
                   (r"\d+ images", "image counts")):
    c = collections.Counter(re.findall(pat, txt))
    print("  %-18s %s" % (label, dict(c)))

print()
print("4. STRUCTURE")
print("=" * 72)
print("  sections        :", len(re.findall(r"<h2[^>]*>", h)))
print("  basis block     :", h.count("id='the-analysis'"))
print("  session blocks  :", h.count("session-20260930"))
print("  archetype cards :", len(re.findall(r"matrix-card archetype-card", h)))
print("  figures         :", h.count("<svg viewBox='0 0 100 120'"))
print("  total <li>      :", h.count("<li>"))
FACES = re.compile(r"[\U0001F600-\U0001F64F]+")
MINE = {"\U0001F914", "\U0001F624", "\U0001F60C", "\U0001F614",
        "\U0001F62C", "\U0001F610", "\U0001F636"}
fc = collections.Counter(FACES.findall(h))
print("  faces           :", {k: v for k, v in fc.items()})
print("  non-mine faces  :", [k for k in fc if k not in MINE] or "none")

print()
print("5. WHAT THE DOCUMENT SAYS IT OWES")
print("=" * 72)
i = txt.find("WHAT IS OWED")
print(" ", txt[i:i + 200] if i > 0 else "NOT FOUND")
i = txt.find("Zero images")
print("  zero-image mentions:", txt.count("Zero images") + txt.count("0 images"))
