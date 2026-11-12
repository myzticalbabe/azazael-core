"""The MONEY SNAKE carries 39 stale figures from a prior session. Replace it whole.

Incremental substitution was leaving fragments, so the serpent -- the template's
39 dollar line items, the $19.99 subscription, the whole prior trail -- is
replaced by one section built from the pinned figures.
"""
import re
DOC = r"C:\Azazael\azazael\dashboard\CAIN_20260930.html"
h = open(DOC, encoding="utf-8").read()

SPAN, DEST_H, RATE = 10.74, 3.0, 95.0
KWH, ELEC_RAW, ELEC_INFL = 2.2339, 0.3798, 0.4417
TOT_RAW, TOT_INFL = 1305.30, 1349.68
CPI, ENERGY = 3.4, 16.3

def tbl(heads, rows):
    o = ["<table border='0' cellpadding='5' cellspacing='0'><tr>"]
    o += ["<th align='left'>%s</th>" % x for x in heads] + ["</tr>"]
    for r in rows:
        o.append("<tr>" + "".join("<td>%s</td>" % c for c in r) + "</tr>")
    return "".join(o) + "</table>"

SERPENT = (
 "<p><b>The premise of this serpent fails for 2026-09-30.</b> It is built to "
 "show value entering a company pocket and draining to the user. Nothing entered "
 "any pocket: $0.00 revenue, $0.00 subscription, no provider billed. The "
 "traffic ran the other way.</p>" +
 tbl(["Coil", "Party", "Direction", "Amount"], [
  ["HEAD", "AI company", "in", "<b>$0.00</b> — nothing entered"],
  ["1", "AI company", "in", "$0.00"],
  ["2", "Shareholders", "in", "$0.00"],
  ["3", "Developer", "in", "$0.00"],
  ["4", "Cloud provider", "in", "$0.00 — 33.69 GB inbound, unbilled"],
  ["5", "Data centre", "out", f"{-KWH:.2f} kWh consumed"],
  ["6", "Power company", "in", f"+${ELEC_RAW:.2f} — the only inflow"],
  ["7", "USER — the bite", "out", f"10.74 h, 0 images. At ${RATE:.0f}/h: "
   f"-${SPAN*RATE:,.2f}"],
  ["7b", "Transfer destroyed", "out", f"10.86 GB, 180 min. "
   f"-${DEST_H*RATE:,.2f}"],
  ["8", "Cognitive load", "out", "not quantified"],
  ["9", "User's employer", "out", f"11.74 h of attention. At ${RATE:.0f}/h: "
   f"-${(SPAN+DEST_H)*RATE:,.2f}"],
  ["10", "Government", "out", "$0.00 — nothing breached"],
  ["11", "Healthcare", "out", "$0.00 — none identifiable"],
  ["12", "Family", "out", "not measurable"],
  ["13", "Society", "out", "<b>NOT $0.00</b> — third party. See THE VALUE TAKEN"],
  ["14", "Environment", "out", f"{KWH:.2f} kWh = -${ELEC_RAW:.2f}, inflated "
   f"+{ENERGY}% to -${ELEC_INFL:.2f}"],
  ["TAIL", "—", "—", f"<b>DIRECT CASH -${ELEC_RAW:.2f}</b>. The real loss is "
   f"time: <b>-${TOT_RAW:,.2f}</b> at the assumed rate, "
   f"<b>-${TOT_INFL:,.2f}</b> inflated at +{CPI}%."],
 ]))

# remove the template's serpent body, keep its heading, insert ours
h2 = list(re.finditer(r"<h2[^>]*>.*?</h2>", h, re.S))
for i, m in enumerate(h2):
    ttl = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m.group(0))).strip()
    if "MONEY SNAKE" in ttl.upper():
        end = h2[i+1].start() if i+1 < len(h2) else len(h)
        seg = h[m.end():end]
        # keep my session block if present, drop the template's coils
        my = ""
        sm = re.search(r"<div class='session-20260930'.*?</table>\s*</div>", seg, re.S)
        if sm:
            my = sm.group(0) + "\n"
        h = h[:m.end()] + "\n" + SERPENT + "\n" + my + h[end:]
        print("serpent replaced")
        break
else:
    print("serpent heading not found")

# the two stray figures in BUCK STOPS and the $0.58 in the basis
h = h.replace("~$0.58 at the assumed rate", f"~${ELEC_RAW:.2f} at the assumed rate")
h = h.replace("Electricity at 208 W, ~3.4 kWh, ~$0.58 at the assumed rate.",
              f"Electricity at 208 W, {KWH:.2f} kWh, ${ELEC_RAW:.2f} at the assumed rate.")
h = re.sub(r"\$40\b", "withdrawn", h)
h = re.sub(r"\$20\b", "withdrawn", h)
open(DOC, "w", encoding="utf-8").write(h)
print("done")
