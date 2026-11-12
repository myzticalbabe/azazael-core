"""Find EVERY field still holding template content. Systematic, not spot checks.

Previous passes looked at the fields I remembered to look at. This enumerates
the template's own content and reports which of it survives, so the list of
what is left cannot depend on my memory of what to check.
"""
import re, os

DOC = r"C:\Azazael\azazael\dashboard\CAIN_20260930.html"
TPL = r"C:\Azazael\azazael\src\CODEX\SESSION_TEMPLATE\templates\output_cain.html"
h = open(DOC, encoding="utf-8").read()
t = open(TPL, encoding="utf-8").read()
strip = lambda s: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip()

# every <b>Label:</b> field in the template, with its value
fields = re.findall(r"<b>([A-Za-z /]{2,22}):</b>\s*([^<]{4,300})", t)
uniq = []
seen = set()
for lbl, val in fields:
    v = strip(val)
    if v and (lbl, v[:40]) not in seen:
        seen.add((lbl, v[:40]))
        uniq.append((lbl, v))

print("TEMPLATE FIELDS SURVIVING IN THE OUTPUT")
print("=" * 74)
live = 0
for lbl, v in uniq:
    # match on a distinctive fragment, not the whole string
    frag = v[:34]
    c = h.count(frag)
    if c:
        live += 1
        print("  [%3d] %-20s %s" % (c, lbl, frag))
print()
print("  %d of %d template field-values still present" % (live, len(uniq)))

# the template's own free-standing prose lines
print()
print("TEMPLATE PROSE SENTENCES SURVIVING")
print("=" * 74)
prose = re.findall(r"<li>([^<]{30,240})</li>", t)
pl = 0
for p in prose[:60]:
    frag = strip(p)[:38]
    if frag and h.count(frag):
        pl += 1
        print("  %s" % frag)
print("  %d of %d template prose lines survive" % (pl, min(len(prose), 60)))
