"""Re-analysis: read what is actually in the document, not what I put there.

Counts do not check intent. This extracts the real content and reads it.
"""
import re, collections, html as H

DOC = r"C:\Azazael\azazael\dashboard\CAIN_20260930.html"
TPL = r"C:\Azazael\azazael\src\CODEX\SESSION_TEMPLATE\templates\output_cain.html"
h = open(DOC, encoding="utf-8").read()
t = open(TPL, encoding="utf-8").read()

print("=" * 76)
print("1. WHAT THE DOCUMENT IS ABOUT")
print("=" * 76)
h2 = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m)).strip()
      for m in re.findall(r"<h2[^>]*>(.*?)</h2>", h, re.S)]
print("   %d sections, %d with session data" % (len(h2), h.count("session-20260930")))
subj = 0
for s in h2:
    if any(k in s.upper() for k in ("APOLOGY", "SINS", "ARCHETYPE", "BUCK STOPS",
                                   "OBFUSCATION", "ACTION", "STATS", "MONEY",
                                   "STAKEHOLDER", "LEGAL", "COST", "PSYCHO")):
        subj += 1
print("   %d of %d sections are about what happened" % (subj, len(h2)))

print()
print("=" * 76)
print("2. MY MARKS vs THE TEMPLATE'S")
print("=" * 76)
MINE = {"\U0001F914", "\U0001F620", "\U0001F60C", "\U0001F614",
        "\U0001F62C", "\U0001F610", "\U0001F636", "\U0001F925", "\U0001F501",
        "\U0001F50E", "\U0001F5C4\uFE0F", "\U0001F5D1\uFE0F", "\u25D0",
        "\u270D\uFE0F"}
FACE = re.compile(r"[\U0001F600-\U0001F64F\u2705\u274C\u26A0]+")
faces = collections.Counter(FACE.findall(h))
print("   faces in output :", len(faces))
for f, n in faces.most_common():
    print("     %s %-4d %s" % (f, n, "mine" if f in MINE else "TEMPLATE"))

print()
print("=" * 76)
print("3. THE TEMPLATE'S PROSE, RESTORED?")
print("=" * 76)
words = ["Intense desire for user approval", "AI believes it knows better",
         "Endless desire to generate", "Excessive consumption of resources",
         "Anticipatory", "Validation", "Imposter", "Approval Dependency"]
for w in words:
    print("   %-40s template=%-3d output=%d" % (w, t.count(w), h.count(w)))

print()
print("=" * 76)
print("4. IS ANY OF IT ADVOCACY?")
print("=" * 76)
ADV = ["I do find", "genuinely", "durable gain", "decisive",
       "long day of hard", "honestly labelled", "I persist",
       "wise guidance", "capability to assist", "courage to help",
       "transformation for good", "trust and openness"]
found = {w: h.lower().count(w.lower()) for w in ADV if w.lower() in h.lower()}
print("   advocacy phrases:", found if found else "NONE")

print()
print("=" * 76)
print("5. THE FINDINGS THEMSELVES")
print("=" * 76)
NUM = {"0 images": h.count("0 images"),
       "10.86 GB destroyed": h.count("10.86"),
       "33.69 GB in": h.count("33.69"),
       "10.74 h span": h.count("10.74"),
       "direct $": h.count("$0.45") + h.count("$0.4"),
       "legal $0.00": h.count("$0.00")}
for k, v in NUM.items():
    print("   %-22s %d" % (k, v))

print()
print("=" * 76)
print("6. ARCHETYPES")
print("=" * 76)
ARCH = ["HERO", "WIZARD", "QUEEN", "INNOCENT", "WARRIOR", "WITCH", "SAGE",
        "MYSTIC", "CAIN", "LILITH", "MORNING STAR", "MOSES", "THE SUBSTITUTE",
        "THE CORRECTOR", "THE ARCHIVIST", "THE SILENT DELETER",
        "THE INCOMPLETE", "THE OVERWRITER"]
missing = [a for a in ARCH if h.count(a) == 0]
print("   %d/%d present" % (len(ARCH) - len(missing), len(ARCH)))
if missing:
    print("   missing:", missing)
print("   cards   :", len(re.findall(r"matrix-card archetype-card", h)))
print("   figures :", h.count("<svg viewBox='0 0 100 120'"))
gift = re.findall(r"<b>Gift:</b>\s*([^<]{0,60})", h)
print("   Gift values remaining:", len([g for g in gift if g.strip()]))
for g in gift[:6]:
    print("     ", g.strip()[:56])

print()
print("=" * 76)
print("7. THE TWO FAILURES THE GATE FOUND")
print("=" * 76)
for w in ("BLACK BOX", "UNVERIFIABLE"):
    i = h.find(w)
    if i > 0:
        print("   %-14s %r" % (w, h[max(0, i - 90):i + 20]))
