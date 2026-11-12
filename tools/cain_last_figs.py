"""The last stale figures, in the two sections that still carry them."""
import re
DOC = r"C:\Azazael\azazael\dashboard\CAIN_20260930.html"
h = open(DOC, encoding="utf-8").read()
SPAN, DEST_H, RATE, KWH = 10.74, 3.0, 95.0, 2.2339
ELEC, ELEC_I, CPI, ENERGY = 0.38, 0.44, 3.4, 16.3

h2 = list(re.finditer(r"<h2[^>]*>(.*?)</h2>", h, re.S))
for i, m in enumerate(h2):
    ttl = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m.group(1))).strip()
    if "FINANCIAL LEDGER" in ttl.upper():
        end = h2[i+1].start() if i+1 < len(h2) else len(h)
        seg = h[m.end():end]
        rows = [("Time lost", f"10.74 h &times; ${RATE:.0f}/h", f"-${SPAN*RATE:,.2f}"),
                ("Time destroyed", f"3.00 h &times; ${RATE:.0f}/h", f"-${DEST_H*RATE:,.2f}"),
                ("Electricity", f"{KWH:.2f} kWh &times; $0.17", f"-${ELEC:.2f}"),
                ("Electricity, inflated", f"+{ENERGY}% energy index", f"-${ELEC_I:.2f}"),
                ("Bandwidth", "33.69 GB in, 10.86 GB re-fetched", "$0.00 marginal"),
                ("API spend", "no provider billed", "$0.00"),
                ("Storage", "no rental implied", "$0.00"),
                ("Opportunity cost of the images", "not produced", "<b>NOT PRICED</b>"),
                ("TOTAL DIRECT CASH", "electricity only", f"<b>-${ELEC:.2f}</b>"),
                ("TOTAL incl. time, inflated +%.1f%%" % CPI, "at the assumed rate",
                 f"<b>-${1349.68:,.2f}</b>")]
        t = ["<table border='0' cellpadding='5' cellspacing='0'><tr>",
             "<th align='left'>Stage</th><th align='left'>Calculation</th>",
             "<th align='left'>Amount</th></tr>"]
        for r in rows:
            t.append("<tr>" + "".join("<td>%s</td>" % c for c in r) + "</tr>")
        seg = re.sub(r"<table.*?</table>", "", seg, flags=re.S) + "".join(t)
        h = h[:m.end()] + "\n" + seg + h[end:]
        print("financial ledger rebuilt")

    if "BUCK STOPS" in ttl.upper():
        end = h2[i+1].start() if i+1 < len(h2) else len(h)
        seg = h[end:]
        h = h[:end]
        h = re.sub(r"\$40\b", "withdrawn", h)
        h = re.sub(r"\$20\b", "withdrawn", h)
        h = h + seg
        print("buck stops cleaned")
        break

# the three malformed "$0," cells the serpent left
h = h.replace("$0,", "$0.00,")
h = re.sub(r"\$0,(\d)", r"$0.0\1", h)

open(DOC, "w", encoding="utf-8").write(h)
print("done")
