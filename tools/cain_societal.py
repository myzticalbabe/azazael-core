"""Remove the surviving $0.00 societal claims, cell by cell."""
import re
DOC = r"C:\Azazael\azazael\dashboard\CAIN_20260930.html"
h = open(DOC, encoding="utf-8").read()
TAG = r"(?:<[^>]*>|\s)*"

print("BEFORE:")
for m in re.finditer(r"[Ss]ociet\w*" + TAG + r"\$0\.00", h):
    print("   %r" % h[max(0, m.start() - 80):m.end() + 60])

h, k1 = re.subn(
    r"[Ss]ocietal(?:" + TAG + r")\$0\.00",
    "Societal: <b>WITHDRAWN</b> &mdash; society is the third party. Electricity, "
    "bandwidth, disk and the models now occupying it were consumed by work the "
    "user does not receive. See THE VALUE TAKEN",
    h)
h, k2 = re.subn(
    r"(?i)(societal\s*[:=]?\s*)<b>WITHDRAWN</b>\s*&mdash;\s*society is the third party\.\s*Society \$0\.00\s*direct[^<]*",
    r"\1<b>WITHDRAWN</b> &mdash; society is the third party; see THE VALUE TAKEN, IN TODAY'S MONEY",
    h)
h, k3 = re.subn(
    r"Societal \$0\.00\s*<",
    "Societal WITHDRAWN &mdash; see THE VALUE TAKEN<", h)

open(DOC, "w", encoding="utf-8").write(h)
b = open(DOC, encoding="utf-8").read()
left = [m.group(0)[:90] for m in re.finditer(r"[Ss]ociet\w*" + TAG + r"\$0\.00", b)]
print()
print("replacements:", k1, k2, k3)
print("Societal->$0.00 remaining:", len(left))
for s in left:
    print("   ", s)
print("WITHDRAWN markers:", b.count("WITHDRAWN"))
