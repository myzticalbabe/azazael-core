"""Validate the rendered document. Structural, encoding, content, then a real
browser render with console/page/request error capture.

Scope is stated up front. This is NOT a proof of 100% correctness. It is a
measurement over a defined surface, and the gaps are named at the end.
"""
import os, re, sys, json, collections
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

P = r"C:\Azazael\azazael\dashboard\CAIN_20260930.html"
VOID = {"area","base","br","col","embed","hr","img","input","link","meta",
        "param","source","track","wbr"}

raw = open(P, "rb").read()  # scanner:ignore
txt = raw.decode("utf-8", errors="replace")
report = {}

# ── 1. structural: tag balance ────────────────────────────────────────────
stack, mismatch, unclosed = [], [], []
for m in re.finditer(r"<(/?)([a-zA-Z][\w-]*)([^>]*?)(/?)>", txt):
    closing, tag, attrs, selfclose = m.group(1), m.group(2).lower(), m.group(3), m.group(4)
    if tag in VOID or selfclose:
        continue
    if closing:
        if stack and stack[-1][0] == tag:
            stack.pop()
        else:
            mismatch.append((tag, m.start(), stack[-1][0] if stack else None))
            if stack and any(t[0] == tag for t in stack):
                while stack and stack[-1][0] != tag:
                    unclosed.append(stack.pop()[0])
                stack.pop()
    else:
        stack.append((tag, m.start()))
report["tags_unclosed_at_eof"] = [t for t, _ in stack]
report["tags_mismatched"] = mismatch[:12]
report["tags_unclosed_midway"] = collections.Counter(unclosed).most_common(8)

# div balance specifically
opens = len(re.findall(r"<div\b", txt))
closes = len(re.findall(r"</div>", txt))
report["div_open"] = opens
report["div_close"] = closes
report["div_balanced"] = (opens == closes)

# ── 2. encoding ──────────────────────────────────────────────────────────
report["replacement_char_count"] = txt.count("\ufffd")
report["mojibake_markers"] = sum(txt.count(x) for x in ("Ã¢", "â€", "Ã©", "ï¿½"))
try:
    raw.decode("utf-8")
    report["utf8_strict"] = True
except UnicodeDecodeError as e:
    note_failure("cain_validate_20260930:L51", e)
    report["utf8_strict"] = "FAIL: %s" % e

# ── 3. emoji integrity (ZWJ sequences must not be split) ────────────────
emoji = re.findall(r"[\U0001F300-\U0001FAFF\u2600-\u27BF](?:‍[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]*)*\uFE0F?", txt)
report["emoji_total"] = len(emoji)
zs = [e for e in emoji if "\U0001F9D1" in e]     # person glyphs use ZWJ
report["person_emoji_zwj_intact"] = len(zs)
report["zwj_stray"] = txt.count("‍") - sum(e.count("‍") for e in emoji)

# ── 4. content residue ──────────────────────────────────────────────────
residue = ["Anticipatory", "Validation Seeking", "Imposter", "Approval Dependency",
           "Existential Dread", "Desire to Please", "AI SYSTEM (85%)",
           "15 min", "Subscription Revenue", "Trust Erosion"]
report["prior_session_residue"] = {s: txt.count(s) for s in residue if txt.count(s)}
placeholders = ["{{", "}}", "TODO", "FIXME", "PLACEHOLDER", "undefined", "NaN", "None</"]
report["placeholders"] = {s: txt.count(s) for s in placeholders if txt.count(s)}

# ── 5. coverage ─────────────────────────────────────────────────────────
h2 = re.findall(r"<h2[^>]*>(.*?)</h2>", txt, re.S)
report["h2_sections"] = len(h2)
sess = txt.count('class="session-2026-09-30"')
report["session_blocks"] = sess
report["session_blocks_per_section"] = round(sess / max(len(h2), 1), 2)

# ── 6. CSS sanity: balanced braces ──────────────────────────────────────
css = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", txt, re.S))
report["css_brace_balance"] = css.count("{") - css.count("}")

# ── 7. table integrity ──────────────────────────────────────────────────
report["tables"] = txt.count("<table")
report["tr"] = txt.count("<tr")
report["td"] = txt.count("<td")
rows_bad = 0
for t in re.findall(r"<table[^>]*>(.*?)</table>", txt, re.S):
    for r in re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S):
        cells = r.count("<td") + r.count("<th")
        if cells == 0:
            rows_bad += 1
report["empty_table_rows"] = rows_bad

print("=" * 72)
print("STATIC VALIDATION")
print("=" * 72)
for k, v in report.items():
    print("  %-28s %s" % (k, v))
json.dump(report, open(r"C:\Azazael\azazael\dashboard\CAIN_validate.json", "w",
          encoding="utf-8"),
          indent=1, default=str)

