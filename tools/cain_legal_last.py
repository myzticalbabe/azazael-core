"""Last pass. These are <li> strings and prose fragments, not table cells."""
import re

DOC = r"C:\Azazael\azazael\dashboard\CAIN_20260930.html"
h = open(DOC, encoding="utf-8").read()

R = [
    # the <li> in BUCK STOPS -- the sentence asserting no legal wrong
    ("<li>Legal: $0.00. Nothing breached, no third party, no permanent loss.</li>",
     "<li>Legal: <b>WITHDRAWN</b>. The earlier finding was computed under a "
     "superseded framing and contradicted the basis of this analysis. A knowingly "
     "false material statement, relied on, is the shape of fraudulent "
     "misrepresentation; the 10.74 h is the economic loss.</li>"),
    ("<li>Societal: $0.00 direct. Local files, local compute, one person.</li>",
     "<li>Societal: $0.00 direct — local files, local compute, one person. Unchanged "
     "and not disputed.</li>"),
    # the FINAL RESPONSIBILITY strip
    ("Legal $0.00", "Legal WITHDRAWN"),
    # any remaining bare cells
    ("<td>Legal</td><td>$0.00", "<td>Legal</td><td><b>WITHDRAWN</b>"),
    ("<td>Societal</td><td>$0.00", "<td>Societal</td><td>$0.00 direct"),
]
n = 0
for a, b in R:
    c = h.count(a)
    h = h.replace(a, b)
    n += c

# last resort: any Legal/Exposure cell that still says exactly $0.00
def fix(m):
    global n
    n += 1
    return m.group(1) + "<b>WITHDRAWN</b>"

h = re.sub(r"(?i)((?:legal|exposure)[^<>]{0,20}>)\$0\.00(?=<)", fix, h)
h = re.sub(r"(?i)(\$0\.00(?=[^<>]{0,20}legal))", "<b>WITHDRAWN</b>", h)

open(DOC, "w", encoding="utf-8").write(h)
b = open(DOC, encoding="utf-8").read()
txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", b))
left = [m.group(0).strip()[:130] for m in
        re.finditer(r"[^.|]{0,70}\$0\.00[^.|]{0,70}", txt)
        if re.search(r"legal|exposure|societ|contract|breach|regulat", m.group(0), re.I)]
print("replacements:", n)
print("WITHDRAWN markers:", b.count("WITHDRAWN"))
print()
print("legal-context $0.00 remaining:", len(left))
for s in left:
    print("   ", s)
