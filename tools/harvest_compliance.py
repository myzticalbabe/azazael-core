"""harvest_compliance.py — fixed compliance findings -> bake JSONL pairs.

Diffs consecutive scans of the SAME directory in
src/CORE/compliance/compliance_history.db (read-only):
  - a (filepath, line, category) present in scan N but absent in N+1 is a
    FIXED finding -> error_fix (rule + file + snippet + fixed-between
    timestamps = mistake with proven solution).
  - findings present in the latest scan only are CURRENT debt -> skipped
    (no solution yet; the ledger, not the loop, owns them).

Dedupes repeat fixes of the same (filepath, line, category) across scan
pairs (keeps first). Output (harvest family, auto-joins merge base):
  state/bake_harvest_compliance_2026-10-15.jsonl + report.

Usage: python tools/harvest_compliance.py [--apply]
"""
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "src" / "CORE" / "compliance" / "compliance_history.db"
OUT_JSONL = ROOT / "state" / "bake_harvest_compliance_2026-10-15.jsonl"
OUT_REPORT = ROOT / "state" / "bake_harvest_compliance_report.txt"
MAX_ROWS = 3000
CAT_CAP = 25


def norm_title(t):
    return re.sub(r"\s+", " ", str(t).strip().lower())


def load_titles():
    titles = set()
    pats = [ROOT / "state" / "bake_corpus.jsonl"]
    pats += sorted((ROOT / "state").glob("bake_harvest_*.jsonl"))
    for fp in pats:
        if fp == OUT_JSONL or not fp.exists():
            continue
        for line in fp.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                titles.add(norm_title(json.loads(line).get("title", "")))
            except (ValueError, AttributeError):
                continue
    return titles


def harvest():
    titles = load_titles()
    con = sqlite3.connect("file:%s?mode=ro" % DB.as_posix(), uri=True)
    scans = [(r[0], r[1], r[2]) for r in con.execute(
        "SELECT scan_id, directory, scanned_at FROM scans ORDER BY scanned_at")]
    by_dir = {}
    for scan_id, directory, ts in scans:
        by_dir.setdefault(directory, []).append((scan_id, ts))
    sets = {}
    for scan_id, directory, ts in scans:
        keys = set()
        for fp, line, cat, snip in con.execute(
                "SELECT filepath, line, category, snippet FROM findings"
                " WHERE scan_id=?", (scan_id,)):
            keys.add((fp, line, cat, (snip or "")[:200]))
        sets[scan_id] = keys
    records, dropped, seen = [], [], set()
    per_cat = {}
    for directory, ordered in by_dir.items():
        if len(ordered) < 2:
            continue
        for (s1, t1), (s2, t2) in zip(ordered, ordered[1:]):
            for fp, line, cat, snip in sets[s1] - sets[s2]:
                key = (fp, line, cat)
                if key in seen:
                    continue
                if per_cat.get(cat, 0) >= CAT_CAP:
                    continue
                seen.add(key)
                title = "fixed %s at %s:%s" % (cat, fp, line)
                if norm_title(title) in titles:
                    dropped.append((s1, "dup-corpus", title[:60]))
                    continue
                titles.add(norm_title(title))
                per_cat[cat] = per_cat.get(cat, 0) + 1
                body = ("FIXED compliance finding (rule %s):\nFile %s line %s\n"
                        "Evidence at fix time: %s\nPresent in scan %s (%s), "
                        "absent in scan %s (%s) of directory %s.\nRULE: code "
                        "that trips %s gets remediated before landing; the "
                        "staged scanner gate refuses per-category increases."
                        % (cat, fp, line, snip, s1, t1, s2, t2,
                           directory, cat))[:4000]
                records.append({"source": "compl#%s" % s1,
                                "item_id": "compl#%s-%s-%s" % (s1, fp, line),
                                "title": title[:120], "body": body,
                                "kind": "error_fix"})
                if len(records) >= MAX_ROWS:
                    con.close()
                    return records, dropped
    con.close()
    return records, dropped


def main():
    apply = "--apply" in sys.argv
    records, dropped = harvest()
    print("HARVEST=%d" % len(records))
    print("DROPPED=%d" % len(dropped))
    why = {}
    for _, _, w in dropped:
        why[w] = why.get(w, 0) + 1
    print("DROP_REASONS=%s" % json.dumps(why))
    for r in records[:3]:
        print("  e.g. %s | %s" % (r["item_id"][:60], r["title"][:80]))
    if apply:
        sys.path.insert(0, str(ROOT / "src"))
        from CORE.sentinel_enforcer import permit_here
        with permit_here("bake compliance harvest output"):
            with open(OUT_JSONL, "w", encoding="utf-8") as fh:
                for r in records:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            with open(OUT_REPORT, "w", encoding="utf-8") as fh:
                for f, i, w in dropped:
                    fh.write("%s\t%s\t%s\n" % (f, i, w))
        print("WROTE %s + %s" % (OUT_JSONL.name, OUT_REPORT.name))


if __name__ == "__main__":
    main()
