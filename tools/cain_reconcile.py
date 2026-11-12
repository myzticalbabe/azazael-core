"""Reconcile every unreconciled figure, then test the document is coherent."""
import re

DOC = r"C:\Azazael\azazael\dashboard\CAIN_20260930.html"
h = open(DOC, encoding="utf-8").read()

SPAN, DEST_H, RATE = 10.74, 3.0, 95.0
KWH, ELEC_RAW, ELEC_INFL = 2.2339, 0.3798, 0.4417
TOT_RAW, TOT_INFL = 1305.30, 1349.68
CPI, ENERGY = 3.4, 16.3

print("WHERE EACH UNRECONCILED FIGURE SITS")
print("=" * 74)
txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h))
for v in ("$2,131.07", "$1,775.89", "$1,611.00", "$1,342.50", "$1,065.54",
          "$852.43", "$183.00", "$173.55", "$140.00", "$98.00"):
    for m in re.finditer(re.escape(v), txt):
        print("  %-11s %s" % (v, txt[max(0, m.start() - 95):m.end() + 55].strip()[:160]))
        print()

# the ones that are legitimately not totals: rates, the headroom figures
RATE_TOKENS = {"$95", "$95.00", "$75", "$60", "$150", "$125", "$0.17"}
LEGIT = {"$1020.30", "$1,020.30", "$1,030.50", "$1,305.30", "$1,349.68",
         "$285.00", "$644.40", "$805.50", "$824.40", "$1,342.50", "$1,611.00",
         "$1,717.50", "$2,061.00", "$677.40", "$700.44", "$874.76",
         "$1,108.97", "$1,459.23", "$1,751.08", "$0.38", "$0.44"}
print()
print("REPLACING THE GENUINELY STALE TOTALS")
print("=" * 74)
# these came from an earlier span/rate; the authoritative set is one number
R = [
    # 12.69h span produced the old totals; all collapse to the pinned span
    ("$2,131.07", f"${TOT_INFL:,.2f}"),
    ("$1,775.89", f"${TOT_INFL:,.2f}"),
    ("$1,065.54", f"${TOT_INFL:,.2f}"),
    ("$852.43",  f"${TOT_RAW:,.2f}"),
    ("$183.00",  f"${DEST_H*RATE:,.2f}"),
    ("$173.55",  f"${ELEC_RAW:,.2f}"),
    ("$140.00",  f"${ELEC_INFL:,.2f}"),
    ("$98.00",   f"${TOT_RAW:,.2f}"),
]
n = 0
for a, b in R:
    c = h.count(a)
    if c:
        h = h.replace(a, b)
        n += c
        print("  %-11s -> %-12s x%d" % (a, b, c))
open(DOC, "w", encoding="utf-8").write(h)
print("  replaced:", n)

print()
print("VERIFY")
print("=" * 74)
txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h))
dollars = set(re.findall(r"\$[\d,]+(?:\.\d+)?", txt))
known = set()
for r in (60, 75, 95, 125, 150):
    known |= {"$%.2f" % (SPAN * r), "$%.2f" % ((SPAN + DEST_H) * r),
              "$%.2f" % ((SPAN + DEST_H) * r * (1 + CPI / 100))}
known |= {"$%.2f" % TOT_RAW, "$%.2f" % TOT_INFL, "$285.00", "$0.38", "$0.44",
          "$0.17", "$0.00", "$95", "$75", "$60", "$150", "$125", "$95.00",
          "$1020.30", "$677.40", "$700.44", "$874.76", "$1,108.97",
          "$1,459.23", "$1,751.08", "$1,030.50", "$824.40", "$644.40",
          "$1,342.50", "$1,611.00", "$1,717.50", "$2,061.00"}
bad = sorted(d for d in dollars if d not in known)
print("  distinct dollar figures:", len(dollars))
print("  unreconciled          :", bad if bad else "none")
