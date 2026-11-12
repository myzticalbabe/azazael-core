"""The last three: one stale total, two income-bracket labels."""
import re
DOC = r"C:\Azazael\azazael\dashboard\CAIN_20260930.html"
h = open(DOC, encoding="utf-8").read()

# $5.26 is a prior session's TOTAL ESTIMATED for a single incident
h = re.sub(r"[^<>]{0,60}TOTAL ESTIMATED \$5\.26\+",
           "TOTAL ESTIMATED (prior session figure, not this one)", h)
h = h.replace("TOTAL ESTIMATED $5.26+",
              "TOTAL ESTIMATED — prior session, not this one")

# the $40/$20 are income-bracket labels, not losses. Say so where they appear.
h = h.replace("($40k-$75k)", "($40k&#8211;$75k, income bracket, not a loss)")
h = h.replace("($20k-$40k)", "($20k&#8211;$40k, income bracket, not a loss)")

open(DOC, "w", encoding="utf-8").write(h)
print("done")
