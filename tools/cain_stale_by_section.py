"""Every remaining non-authoritative dollar figure, with its container.

Incremental single-number replacement kept leaving fragments. This reports
which SECTION each stale figure lives in, so the containing block can be
replaced wholesale instead of patched.
"""
import re, collections
DOC = r"C:\Azazael\azazael\dashboard\CAIN_20260930.html"
h = open(DOC, encoding="utf-8").read()
txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h))

SPAN, DEST_H = 10.74, 3.0
AUTH = set()
for r in (60, 75, 95, 125, 150):
    AUTH |= {"%.2f" % (SPAN*r), "%.2f" % ((SPAN+DEST_H)*r),
             "%.2f" % ((SPAN+DEST_H)*r*1.034), "%.2f" % (SPAN*r*1.034),
             "%.2f" % (13.74*r), "%.2f" % (13.74*r*1.034)}
AUTH |= {"0.38", "0.44", "0.17", "0.00", "285.00", "1305.30", "1349.68",
         "95", "95.00", "75", "60", "150", "125", "1020.30", "0.38".replace("0.38",".38")}
AUTH = {a.replace(",", "") for a in AUTH} | {a for a in AUTH}

h2 = list(re.finditer(r"<h2[^>]*>(.*?)</h2>", h, re.S))
print("STALE FIGURES BY SECTION")
print("=" * 76)
for i, m in enumerate(h2):
    end = h2[i+1].start() if i+1 < len(h2) else len(h)
    seg = h[m.end():end]
    segtxt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", seg))
    ttl = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m.group(1))).strip()
    stale = []
    for d in re.findall(r"\$[\d,]+(?:\.\d+)?", segtxt):
        v = d[1:].replace(",", "")
        if v not in AUTH and v not in ("0.00",):
            stale.append(d)
    if stale:
        print()
        print("  § %-56s %d stale" % (ttl[:56], len(stale)))
        print("     %s" % "  ".join("%s x%d" % kv for kv in collections.Counter(stale).most_common(9)))

