# coding: utf-8
"""Does the Ex Machina panel hold its never-wrap invariant at every tier?

Renders the real panel against the real snapshot at several widths, heights
and glyph tiers, and checks that no line exceeds its window measured in
COLUMNS. A wrapped line is the one failure this whole layout exists to
prevent, and it is invisible in a screenshot of a wide terminal.
"""
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import exmachina_live_monitor as lm

ANSI = re.compile("\x1b\\[[0-9;]*m")
fails = []


def check(name, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + "%-38s %s" % (name, detail))
    if not cond:
        fails.append(name)


print("[glyph tier guard]")
illegal_zwj = {"x": "\U0001f9d1\u200d\U0001f52c"}          # scientist: 3 codepoints
illegal_wide_uni = {"x": "\u25b3"}                          # triangle: EAW 'A'
ok_emoji = {"x": "\U0001f600"}
ok_uni = {"x": "\u2713"}
check("rejects ZWJ in emoji tier", len(lm._glyph_violations("emoji", illegal_zwj)) == 1,
      str(lm._glyph_violations("emoji", illegal_zwj)))
check("rejects 2-col in unicode tier", len(lm._glyph_violations("unicode", illegal_wide_uni)) == 1,
      str(lm._glyph_violations("unicode", illegal_wide_uni)))
check("accepts a legal emoji", lm._glyph_violations("emoji", ok_emoji) == [])
check("accepts a legal unicode glyph", lm._glyph_violations("unicode", ok_uni) == [])
for tier in ("emoji", "unicode", "ascii"):
    bad = lm._glyph_violations(tier, lm._GLYPH_TIERS[tier])
    check("shipped %s tier is legal" % tier, not bad, ", ".join(bad))

print("\n[glyph tier selection]")
for forced in ("ascii", "unicode", "emoji"):
    got = lm.set_glyphs(forced)
    check("forced %s honoured" % forced, got == forced, got)
lm.set_glyphs("auto")
print("  auto -> %s (stdout %s, console cp %s)" % (
    lm.GLYPH_TIER, sys.stdout.encoding, getattr(sys.stdout, "encoding", "?")))

print("\n[snapshot]")
snap = lm.exmachina_snapshot()
check("snapshot has no error", not snap.get("error"), str(snap.get("error")))
check("report present", bool(snap.get("report")))
check("mood resolved", (snap.get("mood") or {}).get("mood") is not None,
      str((snap.get("mood") or {}).get("mood")))
check("signals present", len(snap.get("signals") or {}) >= 6,
      "%d signals" % len(snap.get("signals") or {}))
check("operators present", sum(1 for k in ("radix", "vela", "nexus", "opus", "navis")
                               if (snap.get("operators") or {}).get(k)) >= 4)

print("\n[reachability: every declared row must appear at the DEFAULT window]")
# A row gated above the window the monitor actually uses is a row nobody can
# read. That is not a hypothetical: the TENSOR rows were first written with a
# height>=40 gate while live_monitor defaults to 100x30, so the deliverable
# would have been invisible in the default window. Gate vs. window is checked
# here rather than trusted.
_DEFAULT_W, _DEFAULT_H = 100, 30
default_lines = [ANSI.sub("", l) for l in lm.exmachina_lines(_DEFAULT_W, _DEFAULT_H, snap)]
if snap.get("tensor"):
    check("TENSOR row visible at default window",
          any("TENSOR" in l for l in default_lines),
          "rows at %dx%d: %d" % (_DEFAULT_W, _DEFAULT_H, len(default_lines)))
else:
    check("tensor artifact present to display", False, "snapshot has no tensor")
# The MUTAGEN per-gene split is the row that says which genes the ledger ever
# actually TESTED, so the same reachability rule applies to it: it was written
# at a height>=34 gate while this window is 30, i.e. invisible where anyone
# would look.
if snap.get("mutagen"):
    check("MUTAGEN split row visible at default window",
          any("c/" in l and "i" in l for l in default_lines),
          "a gene token like \"0c/35i\" at %dx%d" % (_DEFAULT_W, _DEFAULT_H))
else:
    check("mutagen ledger present to display", False, "snapshot has no mutagen")
check("no overflow at default window", len(default_lines) <= _DEFAULT_H,
      "%d lines for %d" % (len(default_lines), _DEFAULT_H))

print("\n[render: never wraps]")
for tier in ("ascii", "unicode"):
    lm.set_glyphs(tier)
    worst = 0
    for width in (70, 80, 100, 104, 120, 160, 200):
        for height in (20, 22, 26, 28, 30, 32, 34, 36, 40, 60):
            lines = lm.exmachina_lines(width, height, snap)
            if height < 22:
                if lines:
                    check("height<22 renders nothing", False)
                continue
            for ln in lines:
                plain = ANSI.sub("", ln)
                w = lm._vis_len(ln)
                worst = max(worst, w)
                if w > width:
                    check("line within width (%s %dx%d)" % (tier, width, height), False,
                          "%d cols > %d: %r" % (w, width, plain[:60]))
                    break
                if "\n" in ln or "\r" in ln:
                    check("line has no newline", False, repr(plain[:40]))
                    break
    check("%s tier renders within width" % tier, True, "max %d cols" % worst)

print("\n[render: ascii, 120x34]")
lm.set_glyphs("ascii")
for ln in lm.exmachina_lines(120, 34, snap):
    print("|" + ANSI.sub("", ln))
print("\n[render: unicode, 120x34]")
lm.set_glyphs("unicode")
for ln in lm.exmachina_lines(120, 34, snap):
    print("|" + ANSI.sub("", ln))
print("\n[render: ascii, 80x26 (narrow and short)]")
lm.set_glyphs("ascii")
for ln in lm.exmachina_lines(80, 26, snap):
    print("|" + ANSI.sub("", ln))

print("\n%s  (%d failures)" % ("ALL OK" if not fails else "FAILURES: " + ", ".join(fails), len(fails)))
sys.exit(1 if fails else 0)
