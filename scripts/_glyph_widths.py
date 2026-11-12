# coding: utf-8
"""Which candidate glyphs are width-SAFE?

Rules the monitor needs:
  emoji tier   : single codepoint, EAW = W (renders 2 columns)
  unicode tier : EAW = N or Na (renders 1 column everywhere)
  ascii tier   : isascii()

EAW = 'A' (ambiguous) is NOT safe: a terminal with a CJK font renders it
2 columns, so a line modelled as N columns comes out N+k long and wraps.
"""
import unicodedata

CAND = {
    "status ok": "\u2713", "status bad": "\u2717", "warn": "!",
    "speculum U+2205": "\u2205", "null U+2205": "\u2205",
    "integral": "\u222b", "approx": "\u2248", "propto": "\u221d",
    "infinity": "\u221e", "sqrt": "\u221a", "sum": "\u2211",
    "contour": "\u222e", "increment": "\u2206", "nabla": "\u2207",
    "minus": "\u2212", "bullet": "\u2022", "middot": "\u00b7",
    "lozenge": "\u25ca", "diamond": "\u25c6", "circle-dot": "\u25c9",
    "triangle": "\u25b3", "square": "\u25a1", "rot": "\u21bb",
    "not-equal": "\u2260", "le": "\u2264", "ge": "\u2265",
    "element": "\u2208", "empty-set": "\u2205", "nabla2": "\u2207",
    "dbl-bar": "\u2225", "perp": "\u22a5", "star-op": "\u22c6",
    "xor": "\u2295", "otimes": "\u2297", "subset": "\u2282",
    "dagger": "\u2020", "section": "\u00a7", "degree": "\u00b0",
    "block": "\u2588", "half-block": "\u2584", "hbar": "\u210f",
    "there4": "\u2234", "because": "\u2235", "prop": "\u221d",
    "check-heavy": "\u2714", "cross-heavy": "\u2718",
    "gear": "\u2699", "arrow-r": "\u2192", "arrow-u": "\u2191",
    "arrow-dn": "\u2193", "arrow-l": "\u2190", "hook": "\u21a9",
}

WIDTH = {"W": 2, "F": 2, "N": 1, "Na": 1, "H": 1, "A": 0}
print("%-22s %-4s %s" % ("name", "EAW", "verdict"))
safe_n, safe_w, unsafe = [], [], []
for name, ch in sorted(CAND.items()):
    eaw = unicodedata.east_asian_width(ch)
    if eaw in ("N", "Na"):
        verdict = "SAFE 1-col (unicode tier)"
        safe_n.append("%s=%s" % (name, ch))
    elif eaw in ("W", "F"):
        verdict = "2-col (emoji tier, single cp)"
        safe_w.append("%s=%s" % (name, ch))
    elif eaw == "A":
        verdict = "UNSAFE ambiguous (terminal decides)"
        unsafe.append(name)
    else:
        verdict = "narrow-other"
    print("%-22s %-4s %s" % (name, eaw, verdict))

print("\n-- safe 1-column (unicode tier) --")
print(" ".join(safe_n))
print("\n-- 2-column single codepoint (emoji tier) --")
print(" ".join(safe_w))
print("\n-- unsafe (ambiguous) --")
print(", ".join(sorted(unsafe)))
