# coding: utf-8
"""Do emotional emoji actually survive the monitor's width model?

Measures, for candidate mood glyphs:
  codepoints, len(), _vis_len() (the monitor's column model), true terminal width
and flags any place the model disagrees with the terminal.
"""
import sys
import unicodedata

sys.path.insert(0, "scripts")
import live_monitor as lm

# NOTE (runtime trace 2026-10-18): detect_glyph_tier lives in
# exmachina_live_monitor, not live_monitor — the probe imported the wrong
# module and died with AttributeError. Resolve from the right one.
try:
    from exmachina_live_monitor import detect_glyph_tier as _detect_tier
except ImportError:
    _detect_tier = None

# Single-codepoint mood/severity glyphs
SINGLE = {
    "green_circle": "\U0001f7e2", "yellow_circle": "\U0001f7e1", "red_circle": "\U0001f534",
    "fire": "\U0001f525", "warning": "\u26a0", "check": "\u2705", "cross": "\u274c",
    "smile": "\U0001f642", "neutral": "\U0001f610", "worried": "\U0001f61f",
    "dizzy": "\U0001f635", "skull": "\U0001f480", "mind_blown": "\U0001f92f",
    "party": "\U0001f389", "melting": "\U0001fae0",
}
# Composite (ZWJ / variation-selector) glyphs -- the most expressive ones
COMPOSITE = {
    "heart_on_fire": "\u2764\ufe0f\u200d\U0001f525",
    "scientist": "\U0001f9d1\u200d\U0001f52c",
    "face_in_clouds": "\U0001f636\u200d\U0001f32b\ufe0f",
    "eye_speech": "\U0001f441\ufe0f\u200d\U0001f5e8\ufe0f",
}

# What the terminal actually renders: a composed ZWJ sequence collapses to one
# 2-column glyph; a lone wide char is 2; a narrow symbol is 1.
TRUE_W = {
    "single": lambda cp: 2 if unicodedata.east_asian_width(cp[0]) in ("W", "F") else 1,
    "composite": lambda cp: 2,   # ZWJ sequences render as a single 2-col glyph
}


def row(name, s, true_w):
    n = len(s)
    vis = lm._vis_len(s)
    flag = "  <-- MODEL OVERCOUNTS" if vis != true_w else ""
    return "  %-16s cp=%-2d len=%-2d _vis_len=%-2d terminal=%-2d%s" % (
        name, n, n, vis, true_w, flag)


def main():
    print("=== glyph tier capability ===")
    print("  detected tier :", _detect_tier("auto") if _detect_tier else "unknown (tier module unavailable)")
    print("  stdout enc    :", sys.stdout.encoding)
    try:
        import ctypes
        print("  console CP    :", ctypes.windll.kernel32.GetConsoleOutputCP())
    except Exception as e:
        print("  console CP    : unreadable (%s)" % e)
    print("  AZAZAEL_EMOJI :", __import__("os").environ.get("AZAZAEL_EMOJI", "<unset>"))

    print()
    print("=== single-codepoint mood glyphs ===")
    bad = 0
    for name, s in SINGLE.items():
        tw = TRUE_W["single"](s)
        out = row(name, s, tw)
        if "_vis_len" in out and "OVERC" in out:
            bad += 1
        print(out)

    print()
    print("=== composite (ZWJ / VS16) mood glyphs ===")
    for name, s in COMPOSITE.items():
        out = row(name, s, TRUE_W["composite"](s))
        if "OVERC" in out:
            bad += 1
        print(out)

    print()
    print("mis-modelled glyphs:", bad)

    print()
    print("=== does a width error actually break the never-wrap invariant? ===")
    width = 120
    # The panel pads with the MODEL's width, then places a right-edge sentinel
    # that is supposed to land exactly on column `width`. If the model
    # over-counts the glyph, the rendered line stops SHORT of it.
    #
    # drift = modeled_columns - true_columns of the same string. Positive drift
    # means the terminal draws the sentinel to the LEFT of column `width`.
    print("  glyph            modeled  true  drift  rendered")
    for name, s in list(COMPOSITE.items()) + list(SINGLE.items()):
        text = s + " EX MACHINA"
        rest = len(" EX MACHINA")
        glyph_true = TRUE_W["composite"](s) if s in COMPOSITE else TRUE_W["single"](s)
        true_text = glyph_true + rest
        pad = max(0, width - lm._vis_len(text))
        line = text + " " * pad + "|"
        modeled = lm._vis_len(line)
        true_total = true_text + pad + 1
        drift = modeled - true_total
        rendered = "SHORT by %d" % drift if drift > 0 else (
            "OK" if drift == 0 else "LONG by %d (WRAPS)" % -drift)
        print("  %-16s %-8d %-5d %+5d  %s" % (
            name, modeled, true_total, drift, rendered))


main()
