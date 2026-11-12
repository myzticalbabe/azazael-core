"""harvest_pams_archive.py — frozen PAMS curated memories -> bake JSONL.

Reads state/MEMORY_FORENSIC_BACKUP_20261009/pams_archive_ai_memories.db
(read-only; other archive tables are empty). One row per memory with
content >= 40 chars, kind lesson (environment/operational knowledge).

Output (harvest family, auto-joins merge base):
  state/bake_harvest_pams_2026-10-15.jsonl + bake_harvest_pams_report.txt

Usage: python tools/harvest_pams_archive.py [--apply]
"""
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "state" / "MEMORY_FORENSIC_BACKUP_20261009" / "pams_archive_ai_memories.db"
OUT_JSONL = ROOT / "state" / "bake_harvest_pams_2026-10-15.jsonl"
OUT_REPORT = ROOT / "state" / "bake_harvest_pams_report.txt"


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
    records, dropped = [], []
    for mid, content, mtype, imp, bank in con.execute(
            "SELECT memory_id, content, memory_type, importance_level,"
            " memory_bank FROM curated_memories"):
        content = (content or "")
        title = (content.splitlines()[0] if content else "")[:120]
        if len(content) < 40 or not title.strip():
            dropped.append((str(mid)[:8], "too-short", ""))
            continue
        if norm_title(title) in titles:
            dropped.append((str(mid)[:8], "dup-corpus", title[:60]))
            continue
        titles.add(norm_title(title))
        body = ("PAMS curated memory (bank %s, type %s, importance %s):\n%s"
                % (bank, mtype, imp, content))[:4000]
        records.append({"source": "pams#%s" % str(mid)[:8],
                        "item_id": "pams#%s" % str(mid)[:8],
                        "title": title, "body": body, "kind": "lesson"})
    con.close()
    return records, dropped


def main():
    apply = "--apply" in sys.argv
    records, dropped = harvest()
    print("HARVEST=%d" % len(records))
    print("DROPPED=%d" % len(dropped))
    for f, i, w in dropped[:10]:
        print("  drop %s %s (%s)" % (f, i, w))
    if apply:
        sys.path.insert(0, str(ROOT / "src"))
        from CORE.sentinel_enforcer import permit_here
        with permit_here("bake pams harvest output"):
            with open(OUT_JSONL, "w", encoding="utf-8") as fh:
                for r in records:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            with open(OUT_REPORT, "w", encoding="utf-8") as fh:
                for f, i, w in dropped:
                    fh.write("%s\t%s\t%s\n" % (f, i, w))
        print("WROTE %s + %s" % (OUT_JSONL.name, OUT_REPORT.name))


if __name__ == "__main__":
    main()
