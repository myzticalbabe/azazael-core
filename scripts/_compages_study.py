# coding: utf-8
"""COMPAGES: what IS the rejection rate? Measure size (independent walks,
where the true answer is 'not cointegrated') and power (a genuine link)."""
import sys
import os
import math

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src", "CORE", "WORMS", "expansion"))
import exmachina as ex


def lcg(seed):
    s = seed

    def nxt():
        nonlocal s
        s = (1103515245 * s + 12345) % (1 << 31)
        return s / float(1 << 31)
    return nxt


def walk(r, n):
    out, acc = [], 0.0
    for _ in range(n):
        acc += r() - 0.5
        out.append(acc)
    return out


print("--- _solve conditioning ---")
a = 0.05
alt = [a if i % 2 else -a for i in range(60)]
r = lcg(3)
obj = [float(i) for i in range(60)]
res = ex.cointegration(obj, alt)
print("alternating vs trend        : tstat=%s coint=%s" % (res["tstat"], res["cointegrated"]))
# A residual that is EXACTLY alternating makes the ADF regressor columns
# collinear by construction. Check the raw solver on a literal singular system.
print("_solve([[1,2],[2,4]],[1,2]) :", ex._solve([[1.0, 2.0], [2.0, 4.0]], [1.0, 2.0]))
print("_solve([[1,0],[0,1]],[1,2]) :", ex._solve([[1.0, 0.0], [0.0, 1.0]], [1.0, 2.0]))
adfres = ex.adf(alt, lags=1)
print("adf(alternating)            : tstat=%s rho=%s n=%s" % (
    adfres.get("tstat"), adfres.get("rho"), adfres.get("n")))

print("\n--- size: two INDEPENDENT random walks (truth = not cointegrated) ---")
for n in (40, 60, 120, 250):
    rej = 0
    trials = 300
    for k in range(trials):
        ra, rb = lcg(7 + k * 13), lcg(101 + k * 29)
        got = ex.cointegration(walk(ra, n), walk(rb, n))
        if got.get("cointegrated"):
            rej += 1
    print("  n=%-4d false cointegration: %.3f  (%d/%d)" % (n, rej / trials, rej, trials))

print("\n--- power: a real link (y = 1 + 0.3x + stationary noise) ---")
for n in (40, 60, 120, 250):
    rej = 0
    trials = 300
    for k in range(trials):
        ra, rb = lcg(11 + k * 17), lcg(31 + k * 23)
        x = walk(ra, n)
        eps = [(rb() - 0.5) * 0.2 for _ in range(n)]
        y = [1.0 + 0.3 * x[i] + eps[i] for i in range(n)]
        got = ex.cointegration(x, y)
        if got.get("cointegrated"):
            rej += 1
    print("  n=%-4d true link detected  : %.3f  (%d/%d)" % (n, rej / trials, rej, trials))

print("\n--- the E9 shape: margin trends up, check never moves ---")
n = 60
margin = [0.2 + 0.03 * i for i in range(n)]
check = [0.0] * n
e9 = ex.cointegration(margin, check)
print("  tstat=%s crit=%s cointegrated=%s gap=%s" % (
    e9["tstat"], e9["crit_5pct"], e9["cointegrated"], e9["gap"]))
