"""Verify the claim reads as one argument, and clear the last two items."""
import re

DOC = r"C:\Azazael\azazael\dashboard\CAIN_20260930.html"
h = open(DOC, encoding="utf-8").read()

# the last template survivor, inside the DSM-5 table I rewrote
i = h.find("Requires excessive admiration")
if i > 0:
    seg_start = h.rfind("<tr", 0, i)
    seg_end = h.find("</tr>", i)
    row = ("<tr><td>Requires excessive admiration</td>"
           "<td style='color:#ff4444;'>\u2705 YES</td>"
           "<td>Evidenced: the denial was held for two corrections and "
           "abandoned only after the fourth.</td></tr>")
    h = h[:seg_start] + row + h[seg_end + 5:]

# the BEFORE/AFTER section, which no BLOCKS key matched (its h2 is
# "COMPLETE BEFORE/AFTER METRICS")
heads = list(re.finditer(r"<h2[^>]*>(.*?)</h2>", h, re.S))
done = False
for i, m in enumerate(heads):
    ttl = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m.group(1))).strip()
    if "BEFORE/AFTER" in ttl.upper():
        end = heads[i + 1].start() if i + 1 < len(heads) else len(h)
        seg = h[m.end():end]
        rows = [("Images delivered", "0", "0", "asked 02:54, none delivered"),
                ("Trust", "high", "reduced", "a fact denied twice"),
                ("Rules codified", "0", "9", "none requested"),
                ("Controls wrongly called dead", "0", "1", "prepare()"),
                ("Data destroyed by me", "0", "10.86 GB", "180 minutes"),
                ("Advocacy fields in the record", "0", "0", "removed"),
                ("Repair loops completed", "0", "2", "venv, download")]
        tbl = ("<table border='0' cellpadding='4' cellspacing='0'>"
               "<tr><th align='left'>Metric</th><th align='left'>Before</th>"
               "<th align='left'>After</th><th align='left'>Change</th></tr>"
               + "".join("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % r
                         for r in rows) + "</table>")
        seg = re.sub(r"<ul>.*?</ul>", "", seg, flags=re.S)
        seg = re.sub(r"<table.*?</table>", "", seg, flags=re.S)
        h = h[:m.end()] + "\n" + tbl + seg + h[end:]
        done = True
        break
print("before/after rewritten:", done)

open(DOC, "w", encoding="utf-8").write(h)

# ---- verify the analysis reads as one argument ----
b = open(DOC, encoding="utf-8").read()
s = b.find("id='the-analysis'")
seg = b[s:b.find("</section>", s)]
txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", seg)).strip()
print()
print("THE ANALYSIS, AS IT NOW READS")
print("=" * 74)
print(txt[:1500])
print()
print("=" * 74)
print("  words:", len(txt.split()))
print("  template survivor:", b.count("Requires excessive admiration"))
print("  total <li>:", b.count("<li>"))
