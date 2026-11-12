"""harvest_bake_errors.py — session mistakes + solutions -> bake JSONL pairs.

Covers what extract_bake_corpus.py misses: the live session stores.
Sources:
  1. Engram export JSON (default: --export path): every type==bugfix
     observation -> error_fix, plus correction-shaped discoveries/decisions
     (ruling/verdict/retraction/root-cause/predicate-fix language) that carry
     both a mistake AND its solution. Pure status notes (no solution signal)
     are dropped to the report, never silently kept.
  2. state/FULL_SUITE_SWEEP_2026-10-14.md rows 1-14 -> error_fix each
     (row 12 already fixed in-commit; rows 1-11 carry owner-or-fix pointers).
  3. type==lesson observations -> lesson (small set, usually complements docs).

Dedupes against state/bake_corpus.jsonl by normalized title, and intra-run
by engram id / sweep row. Same record schema as extract_bake_corpus.py:
  {source, item_id, title, body, kind}

Usage: python tools/harvest_bake_errors.py [--apply]
  default = dry run (counts only). --apply writes
  state/bake_harvest_2026-10-15.jsonl + state/bake_harvest_report.txt
"""
import json
import re
import sys
from pathlib import Path
import argparse

ROOT = Path(__file__).resolve().parents[1]
OUT_JSONL = ROOT / "state" / "bake_harvest_2026-10-15.jsonl"
OUT_REPORT = ROOT / "state" / "bake_harvest_report.txt"

SOLUTION_RE = re.compile(
    r"fix|repair|resolv|correct|ruling|verdict|landed|retire|quarantine|"
    r"root.caus|predicate|re-baseline|refus|patch|revert|reword|reworded",
    re.IGNORECASE,
)
SWEEP_ROW_RE = re.compile(r"^\|\s*(\d+)\s*\|\s*(`[^`]+`)\s*\|\s*([A-E][^|]*?)\s*\|"
                          r"\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|")


def norm_title(t):
    return re.sub(r"\s+", " ", t.strip().lower())


def load_corpus_titles():
    titles = set()
    fp = ROOT / "state" / "bake_corpus.jsonl"
    if not fp.exists():
        return titles
    for line in fp.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            titles.add(norm_title(json.loads(line).get("title", "")))
        except (ValueError, AttributeError):
            continue
    return titles


def harvest_export(path, corpus_titles):
    records, dropped, seen_ids = [], [], set()
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    except OSError as e:
        return records, [("export", "UNREADABLE", str(e)[:80])]
    obs = data.get("observations", []) if isinstance(data, dict) else []
    for o in obs:
        oid = o.get("id")
        otype = (o.get("type") or "")
        title = (o.get("title") or "")[:120]
        body = (o.get("content") or "")[:4000]
        if not title or len(body) < 40:
            dropped.append(("engram#%s" % oid, "too-short", otype))
            continue
        if norm_title(title) in corpus_titles:
            dropped.append(("engram#%s" % oid, "dup-corpus", title[:60]))
            continue
        if otype == "bugfix":
            kind = "error_fix"
        elif otype == "lesson":
            kind = "lesson"
        elif otype in ("discovery", "decision") and SOLUTION_RE.search(title + "\n" + body):
            kind = "error_fix"
        else:
            dropped.append(("engram#%s" % oid, "no-solution-signal", otype))
            continue
        if oid in seen_ids:
            dropped.append(("engram#%s" % oid, "dup-seen", title[:60]))
            continue
        seen_ids.add(oid)
        records.append({"source": "engram#%s" % oid, "item_id": "engram#%s" % oid,
                        "title": title, "body": body, "kind": kind})
    return records, dropped


def harvest_sweep(path, corpus_titles):
    records, dropped = [], []
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return records, [("sweep", "UNREADABLE", str(e)[:80])]
    for line in text.splitlines():
        m = SWEEP_ROW_RE.match(line.strip())
        if not m:
            continue
        n, test, cls, count, cause, owner = m.groups()
        title = "sweep row %s: %s (%s)" % (n, test.strip("`"), cls.strip())
        if norm_title(title) in corpus_titles:
            dropped.append(("sweep", "row-%s" % n, "dup-corpus"))
            continue
        body = ("Class %s, %s. Root cause: %s Owner-or-fix: %s"
                % (cls.strip(), count.strip(), cause.strip(), owner.strip()))[:4000]
        records.append({"source": "sweep-2026-10-14", "item_id": "row-%s" % n,
                        "title": title[:120], "body": body, "kind": "error_fix"})
    return records, dropped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--export", default="C:/Users/clint/AppData/Local/Temp/opencode/engram-azazael.json")
    ap.add_argument("--sweep", default=str(ROOT / "state" / "FULL_SUITE_SWEEP_2026-10-14.md"))
    args = ap.parse_args()

    corpus_titles = load_corpus_titles()
    recs, drops = harvest_export(args.export, corpus_titles)
    srecs, sdrops = harvest_sweep(args.sweep, corpus_titles)
    records, dropped = recs + srecs, drops + sdrops

    by_kind = {}
    for r in records:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
    print("HARVEST=%d %s" % (len(records), json.dumps(by_kind)))
    print("DROPPED=%d" % len(dropped))
    why = {}
    for _, _, w in dropped:
        why[w] = why.get(w, 0) + 1
    print("DROP_REASONS=%s" % json.dumps(why))
    for f, i, w in dropped[:25]:
        print("  drop %s %s (%s)" % (f, i, w))
    if args.apply:
        sys.path.insert(0, str(ROOT / "src"))
        from CORE.sentinel_enforcer import permit_here
        with permit_here("bake harvest output"):
            with open(OUT_JSONL, "w", encoding="utf-8") as fh:
                for r in records:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            with open(OUT_REPORT, "w", encoding="utf-8") as fh:
                for f, i, w in dropped:
                    fh.write("%s\t%s\t%s\n" % (f, i, w))
        print("WROTE %s + %s" % (OUT_JSONL.name, OUT_REPORT.name))


if __name__ == "__main__":
    main()
