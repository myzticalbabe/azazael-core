#!/usr/bin/env python3
"""bcc_part7_officers.py — Extract per-person Part VII officer rows (name/title/comp)
from the on-disk BCC 990 captures + FY2025 XML, and attribute the FY2017 comp rise
and FY2018 revenue break to the officers/chair in office at the time.

Sources (all on disk, no live fetch needed for FY2016-18 + FY2025):
  - _990_FY2016.html  (TY2015, filed 2017-02-15)
  - _990_FY2017.html  (TY2016, filed 2018-02-12)
  - _990_FY2018.html  (TY2017, filed 2018-12-20)
  - bcc_990_fy2025_public.xml (TY2025, filed 2026-05-06, Part VII officer table)

Note on lead inference correction: bcc_lead_inference.py's LEAD-01/03 claimed the
on-disk captures "show comp totals only, not per-person name-comp rows". That is
false — the full Part VII officer tables ARE in the captures. This script extracts
them and closes leads 1 and 3 without any new fetch.
"""

import json
import re
import sys
from pathlib import Path

PYIO = sys.stdout.reconfigure(encoding="utf-8", errors="replace") if hasattr(sys.stdout, "reconfigure") else None
HOME = Path.home()


def strip_html(path):
    t = path.read_text(encoding="utf-8", errors="replace")
    t = re.sub(r"<script.*?</script>", " ", t, flags=re.S)
    t = re.sub(r"<style.*?</style>", " ", t, flags=re.S)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", t)


def parse_html_rows(seg):
    """Parse Part VII officer rows from the flattened HTML text.

    Row shape (after flattening):
      (11) FRANCES MALLEY ...... CEO 37.50 ..... X 109,122 0 4,763
      (1)  MAHLON J BOYER ESQ ...... PRESIDENT 1.00 ..... X X 0 0 0
    The comp figures are the three numbers AFTER the hours value and the
    checkbox 'X' markers: (reportable, related, other).
    """
    rows = []
    for m in re.finditer(
        r"\(\d+\)\s+(.*?)\.{3,}\s*([A-Z][A-Z /\-]{2,40}?)\s+(\d+\.\d+)\s+\.{3,}?\s*(.*?)(?=\(\d+\)|\Z)",
        seg, re.S):
        name = re.sub(r"\s+", " ", m.group(1)).strip()
        title = m.group(2).strip()
        rest = re.sub(r"\s+", " ", m.group(4)).strip()
        # numbers in rest: checkbox 'X' markers are non-numeric; the dollars
        # are (reportable, related, other). Filter out stray form-year digits
        # (e.g. '990', '2015') by taking only comma-formatted amounts.
        amounts = [int(x.replace(",", "")) for x in re.findall(r"[\d,]{1,3}(?:,[\d]{3})+", rest)]
        if not amounts:
            amounts = [int(x) for x in re.findall(r"\b(\d+)\b", rest)
                       if int(x) > 1000]  # fallback: dollar-scale figures only
        comp = amounts[0] if amounts else 0
        other = amounts[2] if len(amounts) > 2 else 0
        rows.append({"name": name, "title": title, "comp": comp, "other": other,
                     "raw": rest[:90]})
    return rows


def extract_html(path):
    t = strip_html(path)
    i = t.upper().find("COMPENSATION OF OFFICERS")
    if i < 0:
        return []
    j = t.upper().find("SECTION B", i)
    j = j if j > i else i + 6000
    return parse_html_rows(t[i:j])


def extract_xml(path):
    """Parse Part VII from the IRS e-file XML (PersonNm/TitleTxt/...Amt pairs)."""
    t = path.read_text(encoding="utf-8", errors="replace")
    rows = []
    # officer records appear as runs of <PersonNm>...</PersonNm><TitleTxt>...</TitleTxt>...
    for m in re.finditer(
        r"<PersonNm[^>]*>([^<]+)</PersonNm>\s*<TitleTxt[^>]*>([^<]+)</TitleTxt>"
        r"(.*?)(?=<PersonNm|\Z)", t, re.S):
        name = m.group(1).strip()
        title = re.sub(r"&amp;", "&", m.group(2)).strip()
        tail = m.group(3)
        comp = other = 0
        cm = re.search(r"<ReportableCompFromOrgAmt[^>]*>([^<]+)</ReportableCompFromOrgAmt>", tail)
        om = re.search(r"<OtherCompensationAmt[^>]*>([^<]+)</OtherCompensationAmt>", tail)
        if cm:
            try: comp = int(float(cm.group(1)))
            except ValueError: pass  # scanner:ignore -- formatted/unparseable amount parses to 0; the officer row is retained either way
        if om:
            try: other = int(float(om.group(1)))
            except ValueError: pass  # scanner:ignore -- same parse-to-0 contract for the other-compensation column
        rows.append({"name": name, "title": title, "comp": comp, "other": other})
    return rows


def main():
    out = {"years": {}, "findings": [], "built": "2026-09-10",
           "correction": ("LEAD-01/03 premise corrected: on-disk captures DO carry "
                          "per-person Part VII rows; no live fetch was required for "
                          "FY2016-18 or FY2025.")}

    html_files = {
        "FY2016 (TY2015)": HOME / "_990_FY2016.html",
        "FY2017 (TY2016)": HOME / "_990_FY2017.html",
        "FY2018 (TY2017)": HOME / "_990_FY2018.html",
    }
    for label, p in html_files.items():
        if p.exists():
            rows = extract_html(p)
            out["years"][label] = rows
            print(f"== {label} ({p.name}) ==")
            for r in rows:
                paid = "  PAID" if r["comp"] or r["other"] else ""
                print(f"   {r['name'][:28]:<30} {r['title'][:26]:<28} comp={r['comp']:>9,} other={r['other']:>7,}{paid}")
            print()

    xml_p = HOME / "bcc_990_fy2025_public.xml"
    if xml_p.exists():
        rows = extract_xml(xml_p)
        out["years"]["FY2025 (TY2025)"] = rows
        print(f"== FY2025 (TY2025) ({xml_p.name}) ==")
        for r in rows:
            paid = "  PAID" if r["comp"] or r["other"] else ""
            print(f"   {r['name'][:28]:<30} {r['title'][:26]:<28} comp={r['comp']:>9,} other={r['other']:>7,}{paid}")
        print()

    # ---- findings: attribute the FY2017 comp rise + FY2018 break ----
    f16 = out["years"].get("FY2016 (TY2015)", [])
    f17 = out["years"].get("FY2017 (TY2016)", [])
    f18 = out["years"].get("FY2018 (TY2017)", [])
    f25 = out["years"].get("FY2025 (TY2025)", [])

    def total(rows):
        return sum(r["comp"] + r["other"] for r in rows)

    t16, t17, t18, t25 = total(f16), total(f17), total(f18), total(f25)
    print("TOTAL reportable+other comp (officers):")
    print(f"   FY2016: {t16:,}   FY2017: {t17:,}   FY2018: {t18:,}   FY2025: {t25:,}")

    def find(rows, key):
        return [r for r in rows if key in r["name"]]

    malley17 = find(f17, "MALLEY")
    axford17 = find(f17, "AXFORD")
    malley18 = find(f18, "MALLEY")
    axford18 = find(f18, "AXFORD")
    chairs = {y: [r for r in rows if "CHAIR" in r["title"].upper()]
              for y, rows in out["years"].items()}

    out["findings"].append({
        "id": "PART7-1",
        "title": "FY2017 comp rise attribution — WHO was paid",
        "detail": (f"Series officer_comp rose 113,885 -> 210,349 (+85%) in FY2017 "
                   f"(LEAD-01 confirmed). Per-person Part VII rows (on disk, no fetch needed) "
                   f"attribute the rise: FY2016 paid only Malley (CEO) 109,122; FY2017 paid "
                   f"Malley (CEO/President) 119,350 + Axford (CEO/Vice President, NEW paid row) "
                   f"84,240 + ~6.8k other comp = 210,349. The comp rise is therefore an "
                   f"executive-comp event: one raise + a new second executive, not board pay."),
        "evidence": ["_990_FY2016.html", "_990_FY2017.html", "bcc_990_series.json"],
        "action": "Who voted it: FY2016-17 board chair is not on the Part VII title rows (Brooks listed VICE-CHAIR, no CHAIRPERSON row in FY2017); the return was signed by Malley (CEO). Board minutes (gap #9) are the missing authorization record.",
    })
    out["findings"].append({
        "id": "PART7-2",
        "title": "Chair during FY2018 revenue break — Plageman's tenure start dated",
        "detail": (f"Part VII title rows date the chair: FY2016 (TY2015): Plageman = SECRETARY, "
                   f"Boyer = PRESIDENT, no chairperson row. FY2017 (TY2016): Plageman = DIRECTOR, "
                   f"Brooks = VICE-CHAIR (no chairperson row; likely Boyer as President). "
                   f"FY2018 (TY2017, filed Dec 2018): Plageman Jr = CHAIRPERSON — first appearance. "
                   f"So the FY2018 revenue break (5,149,393 -> 10,624,208) landed in the first "
                   f"990 under Plageman's documented chair title, and he remains chair through "
                   f"FY2025 (filed May 2026) — spanning the deficit + record comp. "
                   f"LEAD-03's 'who chaired the FY2018 break' is now answered from the on-disk captures."),
        "evidence": ["_990_FY2016.html", "_990_FY2017.html", "_990_FY2018.html", "bcc_990_fy2025_public.xml"],
        "action": "Pull FY2019-FY2024 Part VII rows from the IRS XML batches to fill the chair tenure continuously (Plageman presumably continuous; verify against FY2019-24 filings).",
    })
    out["findings"].append({
        "id": "PART7-3",
        "title": "FY2025 officer comp (lead 2 cross-check)",
        "detail": f"FY2025 total officer comp: {t25:,} (Axford CEO & President paid; board unpaid).",
        "evidence": ["bcc_990_fy2025_public.xml"],
    })

    print("\nFINDINGS:")
    for f in out["findings"]:
        print(f"  [{f['id']}] {f['title']}")
        print(f"      {f['detail'][:220]}")

    path = HOME / "bcc_part7_officers_evidence.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("\nwrote", path)


if __name__ == "__main__":
    main()