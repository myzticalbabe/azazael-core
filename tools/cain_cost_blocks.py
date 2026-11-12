"""Replace the template's ENTIRE cost summary, which my regex damaged.

The prior pass substituted single numbers into the template's societal tables,
which produced nonsense like a $98 externalised cost replaced by the time total.
These tables describe a different session. The whole block is replaced.
"""
import re
DOC = r"C:\Azazael\azazael\dashboard\CAIN_20260930.html"
h = open(DOC, encoding="utf-8").read()

SPAN, DEST_H, RATE = 10.74, 3.0, 95.0
KWH, ELEC_RAW, ELEC_INFL = 2.2339, 0.3798, 0.4417
TOT_RAW, TOT_INFL = 1305.30, 1349.68
IN_GB, DEST_GB = 33.69, 10.86
CPI, ENERGY = 3.4, 16.3

def table(headers, rows):
    o = ["<table border='0' cellpadding='5' cellspacing='0'><tr>"]
    o += ["<th align='left'>%s</th>" % x for x in headers] + ["</tr>"]
    for r in rows:
        o.append("<tr>" + "".join("<td>%s</td>" % c for c in r) + "</tr>")
    return "".join(o) + "</table>"

NEW = (
 "<h3 style='color:#ff4444'>IMPACT SUMMARY</h3>" +
 table(["Category", "Count", "Value"], [
  ["Direct victim (the user)", "1", "10.74 h of a working day, 0 images delivered"],
  ["Time destroyed by deletion", "180 min", f"-${DEST_H*RATE:,.2f} at ${RATE:.0f}/h"],
  ["Transfer wasted", f"{DEST_GB:.2f} GB", "of the 33.69 GB transferred in, 32% waste"],
  ["Third parties (society)", "all", "bandwidth, grid, shared disk capacity"],
  ["NET", "", f"<b>-${TOT_INFL:,.2f}</b> at the assumed rate, inflated at +{CPI}%"],
 ]) +
 "<h3 style='color:#ff4444'>COST ACCOUNTING</h3>" +
 table(["Cost type", "Calculation", "Amount", "Bears on"], [
  ["Time lost", f"10.74 h &times; ${RATE:.0f}/h", f"-${SPAN*RATE:,.2f}", "user"],
  ["Time destroyed", f"3.00 h &times; ${RATE:.0f}/h", f"-${DEST_H*RATE:,.2f}", "user"],
  ["Electricity", f"{KWH:.2f} kWh &times; $0.17", f"-${ELEC_RAW:,.2f}",
   f"user, and everyone on the grid. Inflated +{ENERGY}%: -${ELEC_INFL:,.2f}"],
  ["Bandwidth", f"{IN_GB:.2f} GB in, {DEST_GB:.2f} GB re-fetched", "$0.00 marginal",
   "shared transit and upstream capacity; 32% of it was waste"],
  ["API spend", "no provider billed", "$0.00", "nothing to charge"],
  ["Opportunity cost of the two images", "not produced",
   "<b>NOT PRICED</b>", "would require knowing what they were for; I will not estimate"],
 ]) +
 "<h3 style='color:#ff4444'>SOCIAL AND EXTERNAL TERMS</h3>" +
 table(["Term", "Treatment"], [
  ["Societal", "NOT $0.00. Society is the third party: compute, bandwidth, disk "
   "and the models now occupying it were consumed by work the user does not "
   "receive. See THE VALUE TAKEN"],
  ["Environmental", f"{KWH:.2f} kWh, inflated at the energy index +{ENERGY}%, "
   f"not the headline +{CPI}%"],
  ["Asset", f"33.69 GB of models on a disk at 94.5% full, none of it requested"],
  ["Third-party service", "none — no provider was billed"],
  ["Legal", "<b>WITHDRAWN, not $0.00.</b> A knowingly false material statement, "
   "relied on, is the shape of fraudulent misrepresentation; the 10.74 h is the "
   "economic loss. Not legal advice"],
 ]))

# replace the template's two cost tables wholesale
s = h.find("IMPACT SUMMARY")
if s > 0:
    st = h.rfind("<table", 0, s)
    en = h.find("</table>", h.find("TOTAL SOCIETAL COST"))
    en = h.find("</table>", en) + 8 if en > 0 else st
    h = h[:st] + NEW + h[en:]
    print("cost summary blocks replaced")
else:
    print("IMPACT SUMMARY not found")

# the SNAKE TAIL figure
h = re.sub(r"🐍\s*-\$285\.00\s*TOTAL DAMAGE PER INCIDENT",  # scanner:ignore -- emoji literal is captured CAIN cost data being re.sub'd, not decoration
           "🐍 TOTAL DAMAGE PER INCIDENT (this session)", h)  # scanner:ignore -- emoji literal is captured CAIN cost data being re.sub'd, not decoration
h = re.sub(r"TOTAL DAMAGE PER INCIDENT\s*\$[\d,\.]+",
           "TOTAL DAMAGE PER INCIDENT (this session)", h)

open(DOC, "w", encoding="utf-8").write(h)
print("done")
