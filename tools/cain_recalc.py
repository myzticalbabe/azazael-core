"""ONE computation, then every figure in the document is checked against it.

The document has accumulated financial figures from many passes and they no
longer agree. This computes the authoritative set once, then reports every
number in the document that disagrees with it, so the correction is driven by
what is actually there rather than by what I remember writing.
"""
import re, collections, json

DOC = r"C:\Azazael\azazael\dashboard\CAIN_20260930.html"
h = open(DOC, encoding="utf-8").read()
txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h))

# ═══ THE AUTHORITATIVE COMPUTATION ═══════════════════════════════════════
SPAN = 10.74                    # first user message 02:35:11 -> pin
DEST_H = 180 / 60.0             # 3.00 h of transfer destroyed
CPU_W, GPU_W, OVERHEAD = 125, 35, 1.30     # ARK base, estimate, factor
KWH_RATE = 0.17                 # USD/kWh, ASSUMED
RATE = 95.0                     # USD/h, ASSUMED by me, user's to set
IN_GB, DEST_GB = 33.69, 10.86
CPI, ENERGY = 3.4, 16.3         # BLS Aug 2026, NSA

W = (CPU_W + GPU_W) * OVERHEAD
KWH = W * SPAN / 1000.0
ELEC_RAW = KWH * KWH_RATE
ELEC_INFL = ELEC_RAW * (1 + ENERGY / 100.0)
TIME_RAW = SPAN * RATE
DEST_RAW = DEST_H * RATE
TOT_TIME_RAW = TIME_RAW + DEST_RAW
TOT_TIME_INFL = TOT_TIME_RAW * (1 + CPI / 100.0)
CASH_RAW = ELEC_RAW
CASH_INFL = ELEC_INFL

AUTH = {
 "span_h": SPAN, "destroyed_h": DEST_H, "kwh": KWH, "draw_w": W,
 "elec_raw": ELEC_RAW, "elec_infl": ELEC_INFL,
 "time_raw": TIME_RAW, "dest_raw": DEST_RAW,
 "tot_time_raw": TOT_TIME_RAW, "tot_time_infl": TOT_TIME_INFL,
 "cash_raw": CASH_RAW, "cash_infl": CASH_INFL,
}
print("AUTHORITATIVE FIGURES")
print("=" * 70)
for k, v in AUTH.items():
    print("  %-16s %s" % (k, ("%.4f" % v) if isinstance(v, float) else v))

print()
print("EVERY DOLLAR FIGURE IN THE DOCUMENT, BY VALUE")
print("=" * 70)
dollars = collections.Counter(re.findall(r"\$[\d,]+(?:\.\d+)?", txt))
for v, n in sorted(dollars.items(), key=lambda kv: -float(kv[0][1:].replace(",", ""))):
    amt = float(v[1:].replace(",", ""))
    known = any(abs(amt - a) < 0.6 for a in
                (TIME_RAW, DEST_RAW, TOT_TIME_RAW, TOT_TIME_INFL, ELEC_RAW,
                 ELEC_INFL, SPAN * 60, SPAN * 75, (SPAN + DEST_H) * 60,
                 (SPAN + DEST_H) * 75, (SPAN + DEST_H) * 125,
                 (SPAN + DEST_H) * 150, 0.17, 0.38, 0.44, 1.0, 0.0))
    print("  %-12s x%-3d %s" % (v, n, "matches authoritative" if known
                                 else "** UNRECONCILED **"))

print()
print("STALE HOUR FIGURES")
print("=" * 70)
for m in set(re.findall(r"[\d]+\.[\d]+ ?h\b", txt)):
    val = float(m.replace(" h", ""))
    print("  %-10s %s" % (m, "OK" if abs(val - SPAN) < 0.01 else "** STALE **"))

print()
print("COHERENCE: does the document still say $0.00 legal or societal?")
print("=" * 70)
TAG = r"(?:<[^>]*>|\s)*"
for pat, label in ((r"[Ll]egal" + TAG + r"\$0\.00", "legal"),
                   (r"[Ss]ociet\w*" + TAG + r"\$0\.00", "societal")):
    n = len(re.findall(pat, h))
    print("  %-9s %s (%d)" % (label, "CLEAN" if n == 0 else "** PRESENT **", n))

json.dump({k: round(v, 4) for k, v in AUTH.items()},
          open(r"C:\Azazael\azazael\dashboard\cain_financials.json", "w", encoding="utf-8"),
          indent=1)
print()
print("written: dashboard/cain_financials.json")
