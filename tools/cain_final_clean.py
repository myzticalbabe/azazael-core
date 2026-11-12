import re
DOC = r"C:\Azazael\azazael\dashboard\CAIN_20260930.html"
h = open(DOC, encoding="utf-8").read()

# 1. the survivor: find it and read the whole row
i = h.find("Requires excessive admiration")
if i > 0:
    rs = h.rfind("<tr", 0, i)
    re_ = h.find("</tr>", i)
    print("ROW BEFORE:", re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h[rs:re_+5]))[:150])
    h = (h[:rs] + "<tr><td>Requires excessive admiration</td>"
         "<td style='color:#ff4444;'>\u2705 YES</td>"
         "<td>Evidenced: the denial held across two corrections and was "
         "abandoned only after the fourth.</td></tr>" + h[re_ + 5:])

# 2. the claim is printed twice: once as the headline, once in the body. Drop
#    the body copy.
CLAIM = ("I substituted the analysis for the deliverable, and then used the "
         "analysis against the record of the substitution.")
if h.count(CLAIM) > 1:
    first = h.find(CLAIM)
    second = h.find(CLAIM, first + 1)
    # remove the second occurrence together with its wrapping <p>
    s = h.rfind("<p style='margin:0 0 8px 0'>", 0, second)
    e = h.find("</p>", second)
    if s > 0 and e > 0:
        h = h[:s] + h[e + 4:]

open(DOC, "w", encoding="utf-8").write(h)

b = open(DOC, encoding="utf-8").read()
print()
print("  claim copies        :", b.count(CLAIM))
print("  template survivor   :", b.count("Requires excessive admiration"))
print("  analysis block      :", b.count("id='the-analysis'"))
print("  sections            :", len(re.findall(r"<h2[^>]*>", b)))
print("  session blocks      :", b.count("session-20260930"))
print("  archetype cards     :", len(re.findall(r"matrix-card archetype-card", b)))
print("  svg figures         :", b.count("<svg viewBox='0 0 100 120'"))
print("  total <li>          :", b.count("<li>"))
