import re
DOC = r"C:\Azazael\azazael\dashboard\CAIN_20260930.html"
h = open(DOC, encoding="utf-8").read()
TAG = r"(?:<[^>]*>|\s)*"

h, k1 = re.subn(
    r"<th align='left'>Legal</th><td>" + TAG + r"\$0\.00(?=<)",
    "<th align='left'>Legal</th><td><b>WITHDRAWN</b> &mdash; a knowingly false "
    "material statement, relied on, is the shape of fraudulent misrepresentation; "
    "the 10.74 h is the economic loss. Not legal advice", h, flags=re.I)
h, k2 = re.subn(
    r"<th align='left'>Societal</th><td>" + TAG + r"\$0\.00(?=<)",
    "<th align='left'>Societal</th><td>$0.00 direct &mdash; local files, local "
    "compute, one person; unchanged and not disputed", h, flags=re.I)

open(DOC, "w", encoding="utf-8").write(h)
print("Legal cells fixed:", k1, "| Societal cells:", k2)

b = open(DOC, encoding="utf-8").read()
left = [m.group(0)[:110] for m in
        re.finditer(r"Legal" + TAG + r"\$0\.00", b, re.I)]
print("Legal->$0.00 remaining:", len(left))
for s in left:
    print("   ", s)
print("WITHDRAWN markers:", b.count("WITHDRAWN"))
