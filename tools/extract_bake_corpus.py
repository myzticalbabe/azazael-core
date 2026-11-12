"""extract_bake_corpus.py — ERROR/LESSONS/MANDATES/SOP -> bake JSONL pairs.

Reads canonical docs, emits one JSON record per extractable item:
  {source, item_id, title, body, kind}
Kinds: error_fix, lesson, mandate, sop_rule, violation, anti_pattern.
Dedupes known merge artifacts (ERROR_LOG #92-95 x2, MASTER #55-59 x2,
06-04 #54-58 overlapped by MASTER, staged ERROR_LOG_189_entry.md skipped).
Writes state/bake_corpus.jsonl + state/bake_dup_report.txt
Usage: python tools/extract_bake_corpus.py [--apply]
  default = dry run (counts only). --apply writes files.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_JSONL = ROOT / "state" / "bake_corpus.jsonl"
OUT_REPORT = ROOT / "state" / "bake_dup_report.txt"

SOURCES = {
    "ERROR_LOG.md": "error_fix",
    "LESSONS_MASTER.md": "lesson",
    "LESSONS_2026-06-01.md": "lesson",
    "LESSONS_2026-06-04.md": "lesson",
    "LESSONS_DESTRUCTIVE_BEHAVIOR.md": "lesson",
    "LESSONS_NEURAL_ADVANCEMENT.md": "lesson",
    "LESSONS_PLATFORM.md": "lesson",
    "MANDATES.md": "mandate",
    "SOP.md": "sop_rule",
    "PROTOCOL_VIOLATIONS.md": "violation",
    "MEMORIES.md": "anti_pattern",
    "MEMORIES_PLATFORM.md": "anti_pattern",
}
ANNEX = sorted((ROOT / "docs").glob("LESSONS_*.md"))

SKIP_FILES = {"ERROR_LOG_189_entry.md"}  # staged draft, collides with canonical #189
DROP_SECOND = {("ERROR_LOG.md", "#92"), ("ERROR_LOG.md", "#93"),
               ("ERROR_LOG.md", "#94"), ("ERROR_LOG.md", "#95"),
               ("LESSONS_MASTER.md", "#55"), ("LESSONS_MASTER.md", "#56"),
               ("LESSONS_MASTER.md", "#57"), ("LESSONS_MASTER.md", "#58"),
               ("LESSONS_MASTER.md", "#59")}
# 06-04 #54-58 overlapped by MASTER canonical copies
DROP_FILE_RANGE = {("LESSONS_2026-06-04.md", "#54"), ("LESSONS_2026-06-04.md", "#55"),
                   ("LESSONS_2026-06-04.md", "#56"), ("LESSONS_2026-06-04.md", "#57"),
                   ("LESSONS_2026-06-04.md", "#58")}

HDR = re.compile(r"^##\s+(.+?)\s*$")
IDP = re.compile(r"#(\d+)")


def split_sections(text):
    cur_title, cur_lines, out = None, [], []
    for line in text.splitlines():
        m = HDR.match(line)
        if m:
            if cur_title is not None:
                out.append((cur_title, "\n".join(cur_lines).strip()))
            cur_title, cur_lines = m.group(1), []
        elif cur_title is not None:
            cur_lines.append(line)
    if cur_title is not None:
        out.append((cur_title, "\n".join(cur_lines).strip()))
    return out


def item_id(title):
    m = IDP.search(title)
    return "#" + m.group(1) if m else title[:40]


def extract():
    records, dropped, seen = [], [], set()
    files = [(ROOT / f) for f in SOURCES] + ANNEX
    for fp in files:
        if fp.name in SKIP_FILES:
            dropped.append((fp.name, "SKIPPED_FILE", "staged draft"))
            continue
        if not fp.exists():
            continue
        kind = SOURCES.get(fp.name, "lesson")
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            dropped.append((fp.name, "UNREADABLE", str(e)[:80]))
            continue
        for title, body in split_sections(text):
            iid = item_id(title)
            key = (fp.name, iid)
            if key in DROP_SECOND or key in DROP_FILE_RANGE or key in seen and key in DROP_SECOND:
                dropped.append((fp.name, iid, "dup-range"))
                continue
            if key in seen:
                dropped.append((fp.name, iid, "dup-seen"))
                continue
            seen.add(key)
            if len(body) < 40:
                dropped.append((fp.name, iid, "too-short"))
                continue
            records.append({"source": fp.name, "item_id": iid, "title": title[:120],
                            "body": body[:4000], "kind": kind})
    return records, dropped


def main():
    apply = "--apply" in sys.argv
    records, dropped = extract()
    by_kind: dict = {}
    for r in records:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
    print("RECORDS=%d %s" % (len(records), json.dumps(by_kind)))
    print("DROPPED=%d" % len(dropped))
    for f, i, why in dropped[:20]:
        print("  drop %s %s (%s)" % (f, i, why))
    if apply:
        OUT_JSONL.parent.mkdir(parents=True, exist_ok=True)
        with open(OUT_JSONL, "w", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        with open(OUT_REPORT, "w", encoding="utf-8") as fh:
            for f, i, why in dropped:
                fh.write("%s\t%s\t%s\n" % (f, i, why))
        print("WROTE %s + %s" % (OUT_JSONL.name, OUT_REPORT.name))


if __name__ == "__main__":
    main()
