"""The three legal rows my regexes missed. Read the markup, not a pattern."""
import re

DOC = r"C:\Azazael\azazael\dashboard\CAIN_20260930.html"
h = open(DOC, encoding="utf-8").read()

for probe in ("00 Societal $0.00 Legal $0", "User 0% Legal $0.00",
              "Legal: $0.00", "Societal: $0.00 direct"):
    i = h.find(probe)
    if i > 0:
        print("FOUND: %r" % probe)
        print("   %r" % h[max(0, i - 150):i + 90])
        print()

# replacements keyed on the exact cell markup
R = [
    ("<td>Societal</td><td>$0.00</td>", "<td>Societal</td><td>$0.00 direct</td>"),
    ("<td>Legal</td><td>$0.00</td>",
     "<td>Legal</td><td><b>WITHDRAWN</b> - knowingly false material statement, "
     "relied on; time is economic loss. Not legal advice</td>"),
    ("<td>Legal $0.00</td>", "<td>Legal WITHDRAWN</td>"),
    ("<td>Legal: $0.00</td>", "<td>Legal: WITHDRAWN - see the basis</td>"),
]
n = 0
for a, b in R:
    c = h.count(a)
    if c:
        h = h.replace(a, b)
        n += c
    else:
        print("  no match:", a[:50])
open(DOC, "w", encoding="utf-8").write(h)
print("replaced", n)
b = open(DOC, encoding="utf-8").read()
txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", b))
left = [m.group(0).strip()[:120] for m in
        re.finditer(r"[^.|]{0,70}\$0\.00[^.|]{0,70}", txt)
        if re.search(r"legal|exposure|societ|contract|breach|regulat", m.group(0), re.I)]
print("legal-context $0.00 remaining:", len(left))
for s in left:
    print("   ", s)
print("WITHDRAWN markers:", b.count("WITHDRAWN"))
