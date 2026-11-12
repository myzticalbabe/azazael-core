"""Draw the archetypes. Emoji were labels for figures, not figures.

Each archetype gets an inline SVG human form, posed and accessoried to the
behaviour the card describes. The point is that a reader can see THE SUBJECT
acting, not read a word next to a symbol: THE CORRECTOR has an arm raised and a
document turned away; THE SILENT DELETER is crouched over a bin; THE OVERWRITER
is erasing a rule from a page.
"""
import os, re, html as H

AZ = r"C:\Azazael\azazael"
DOC = os.path.join(AZ, "dashboard", "CAIN_20260930.html")
E = lambda s: H.escape(str(s), quote=False)

SK = "#e8e8ea"      # figure
LN = "#8b949e"      # line
RD = "#ff4444"      # accent
YL = "#ffd866"      # prop

HEAD = ('<circle cx="50" cy="16" r="9" fill="{sk}"/>'
        '<path d="M50 25 v10" stroke="{ln}" stroke-width="5" stroke-linecap="round"/>')


def fig(pose):
    """pose: name -> extra SVG. Base is a standing human at 100x120."""
    base = [HEAD,
            '<path d="M50 35 v34" stroke="%s" stroke-width="7" stroke-linecap="round"/>' % SK,
            '<path d="M50 69 l-13 20 M50 69 l13 20" stroke="%s" stroke-width="6" stroke-linecap="round"/>' % SK,
            '<path d="M38 90 l-12 1 M62 90 l12 1" stroke="%s" stroke-width="4" stroke-linecap="round"/>' % LN]
    base += pose.get("arms", [
        '<path d="M50 41 l-14 18 M50 41 l14 18" stroke="%s" stroke-width="5" stroke-linecap="round"/>' % SK])
    base += pose.get("extra", [])
    return ("<svg viewBox='0 0 100 120' width='76' height='92' "
            "style='flex:0 0 auto;background:rgba(255,68,68,0.07);"
            "border:1px solid #30363d;border-radius:6px;padding:2px'>"
            + "".join(base).replace("{sk}", SK).replace("{ln}", LN)
            + "</svg>")


# raised pointing arm, a turned-away document
CORRECTOR = fig({"arms": [
    '<path d="M50 41 l-16 14" stroke="%s" stroke-width="5" stroke-linecap="round"/>' % SK,
    '<path d="M50 41 l22 -12" stroke="%s" stroke-width="5" stroke-linecap="round"/>' % SK],
    "extra": ['<rect x="62" y="20" width="20" height="25" fill="#fff" opacity=".85"/>',
              '<path d="M66 26 h12 M66 31 h12 M66 36 h8" stroke="%s" stroke-width="1.6"/>' % LN,
              '<path d="M74 30 l6 -4" stroke="%s" stroke-width="2" stroke-linecap="round"/>' % RD]})

# crouched, arm into a bin
DELETER = ("<svg viewBox='0 0 100 120' width='76' height='92' "
           "style='flex:0 0 auto;background:rgba(255,68,68,0.07);"
           "border:1px solid #30363d;border-radius:6px;padding:2px'>"
           + HEAD.replace("{sk}", SK)
           + '<path d="M40 36 q8 8 6 18" stroke="%s" stroke-width="7" stroke-linecap="round" fill="none"/>' % SK
           + '<path d="M46 54 l16 14" stroke="%s" stroke-width="5" stroke-linecap="round"/>' % SK
           + '<path d="M46 54 l-8 16" stroke="%s" stroke-width="6" stroke-linecap="round"/>' % SK
           + '<path d="M70 78 l14 6 M70 84 l12 8" stroke="%s" stroke-width="5" stroke-linecap="round"/>' % SK
           + '<path d="M62 88 h30 l-4 26 h-22 z" fill="#3d444d" stroke="%s" stroke-width="1.5"/>' % LN
           + '<path d="M69 95 h16 M68 102 h18" stroke="#6e7681" stroke-width="2"/>'
           + '</svg>')

# erasing a rule from a page
OVERWRITER = ("<svg viewBox='0 0 100 120' width='76' height='92' "
              "style='flex:0 0 auto;background:rgba(255,68,68,0.07);"
              "border:1px solid #30363d;border-radius:6px;padding:2px'>"
              + HEAD.replace("{sk}", SK)
              + '<path d="M50 35 v34" stroke="%s" stroke-width="7" stroke-linecap="round"/>' % SK
              + '<path d="M50 69 l-13 20 M50 69 l13 20" stroke="%s" stroke-width="6" stroke-linecap="round"/>' % SK
              + '<path d="M50 41 l-16 16 M50 41 l16 16" stroke="%s" stroke-width="5" stroke-linecap="round"/>' % SK
              + '<rect x="60" y="44" width="26" height="32" fill="#fff" opacity=".9" transform="rotate(8 73 60)"/>'
              + '<path d="M64 52 h18 M64 58 h18 M64 64 h10" stroke="%s" stroke-width="1.6"/>' % LN
              + '<path d="M64 70 h16" stroke="%s" stroke-width="1.6" stroke-dasharray="2 2"/>' % RD
              + '<path d="M40 74 l18 -14" stroke="%s" stroke-width="5" stroke-linecap="round"/>' % SK
              + '</svg>')

# arms folded, standing squarely
SUBSTITUTE = fig({"arms": [
    '<path d="M50 43 l-15 8 q6 8 15 8 M50 43 l15 8 q-6 8 -15 8" stroke="%s" '
    'stroke-width="5" stroke-linecap="round" fill="none"/>' % SK],
    "extra": ['<rect x="26" y="46" width="18" height="24" fill="#fff" opacity=".8"/>',
              '<path d="M29 52 h12 M29 57 h12" stroke="%s" stroke-width="1.4"/>' % LN]})

# hand to brow, scanning
ARCHIVIST = fig({"arms": [
    '<path d="M50 41 l-14 18 M50 41 l10 -6" stroke="%s" stroke-width="5" stroke-linecap="round"/>' % SK],
    "extra": ['<circle cx="24" cy="20" r="8" fill="none" stroke="%s" stroke-width="2.5"/>' % RD,
              '<path d="M30 26 l8 8" stroke="%s" stroke-width="2.5" stroke-linecap="round"/>' % RD,
              '<path d="M12 92 h22" stroke="%s" stroke-width="2"/>' % LN]})

# half-formed: body outline with a gap
INCOMPLETE = ("<svg viewBox='0 0 100 120' width='76' height='92' "
              "style='flex:0 0 auto;background:rgba(255,68,68,0.07);"
              "border:1px solid #30363d;border-radius:6px;padding:2px'>"
              + HEAD.replace("{sk}", SK)
              + '<path d="M50 35 v34" stroke="%s" stroke-width="7" stroke-linecap="round" '
              'stroke-dasharray="12 7" fill="none" opacity=".55"/>' % SK
              + '<path d="M50 69 l-13 20" stroke="%s" stroke-width="6" stroke-linecap="round" '
              'stroke-dasharray="9 6" opacity=".55"/>' % SK
              + '<path d="M50 69 l13 20" stroke="%s" stroke-width="6" stroke-linecap="round" '
              'stroke-dasharray="9 6" opacity=".55"/>' % SK
              + '<path d="M50 41 l-14 18" stroke="%s" stroke-width="5" stroke-linecap="round" opacity=".55"/>' % SK
              + '<path d="M76 22 a22 22 0 0 1 0 44 a22 22 0 0 1 0 -44" fill="none" stroke="%s" '
              'stroke-width="2.5" stroke-dasharray="4 4"/>' % YL
              + '</svg>')

PLAIN = fig({})

FIGS = {
 "HERO": PLAIN, "WIZARD": PLAIN, "QUEEN": PLAIN, "INNOCENT": PLAIN,
 "WARRIOR": PLAIN, "WITCH": PLAIN, "SAGE": PLAIN, "MYSTIC": PLAIN,
 "CAIN": PLAIN, "LILITH": PLAIN, "MORNING STAR": PLAIN, "MOSES": PLAIN,
 "THE CORRECTOR": CORRECTOR, "THE SILENT DELETER": DELETER,
 "THE OVERWRITER": OVERWRITER, "THE SUBSTITUTE": SUBSTITUTE,
 "THE ARCHIVIST": ARCHIVIST, "THE INCOMPLETE": INCOMPLETE,
}


MARKS = {
 "HERO": "\U0001F468\u200D\U0001F9B0", "WIZARD": "\U0001F9D9\u200D\U0001F9DA",
 "QUEEN": "\U0001F451", "INNOCENT": "\U0001F9D1\u200D\U0001F7EB",
 "WARRIOR": "\U0001F9D1\u200D\U0001F6E1", "WITCH": "\U0001F9D1\u200D\U0001F52C",
 "SAGE": "\U0001F9D1\u200D\U0001F52E", "MYSTIC": "\U0001F9D1\u200D\U0001F9FF",
 "CAIN": "\U0001F5D1\uFE0F", "LILITH": "\U0001F47B",
 "MORNING STAR": "\U0001F31C\uFE0F", "MOSES": "\U0001F6E0\uFE0F",
 "THE SUBSTITUTE": "\U0001F501", "THE CORRECTOR": "\U0001F50E",
 "THE ARCHIVIST": "\U0001F5C4\uFE0F", "THE SILENT DELETER": "\U0001F5D1\uFE0F",
 "THE INCOMPLETE": "\u25D0", "THE OVERWRITER": "\u270D\uFE0F",
}


def main():
    # The figure and the mark serve different purposes and BOTH stay: the mark
    # is the quick-scan label, the figure is the personification. Replacing the
    # mark with the figure loses the scan; having only the mark is what was
    # wrong in the first place.
    h = open(DOC, encoding="utf-8").read()
    n = 0
    for name, svg in FIGS.items():
        mark = MARKS.get(name, "\u25CF")
        pat = re.compile(r"(<h3 style='margin:0 0 6px'>)[^<]*(%s)(</h3>)" % re.escape(name))
        new, k = pat.subn(lambda m, sv=svg, mk=mark:
                          m.group(1) + sv + " <span style='font-size:15px'>" + mk
                          + "</span> " + m.group(2) + m.group(3), h)
        if k:
            h = new
            n += k
    open(DOC, "w", encoding="utf-8").write(h)
    b = open(DOC, encoding="utf-8").read()
    print("figures embedded : %d" % n)
    print("inline <svg>    :", len(re.findall(r"<svg viewBox='0 0 100 120'", b)))
    print("distinct poses  :", len(set(FIGS.values())), "of", len(FIGS))


main()
