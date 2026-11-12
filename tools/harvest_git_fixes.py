"""harvest_git_fixes.py — landed fix commits -> bake JSONL pairs.

Every landed fix is a mistake+solution with ground-truth proof (SHA +
diffstat + guarding tests). Mines `git log` for fix-shaped commits and
emits one record per commit:
  {source, item_id, title, body, kind}
Body carries: short SHA + date, full subject + message head, files touched
(top 12), guarding test files if any (the proof-of-fix signal), +/- lines.
Kind is always error_fix. Merge commits skipped (no single mistake).

Dedupes against bake_corpus + bake_harvest_*.jsonl by normalized title.
Output is harvest-family named so merge_bake_lane.py picks it up as base:
  state/bake_harvest_git_2026-10-15.jsonl + state/bake_harvest_git_report.txt

Usage: python tools/harvest_git_fixes.py [--apply]
  default = dry run (counts only). --apply writes files.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_JSONL = ROOT / "state" / "bake_harvest_git_2026-10-15.jsonl"
OUT_REPORT = ROOT / "state" / "bake_harvest_git_report.txt"

FIX_RE = re.compile(r"fix|repair|resolv|remediat|clos(?:e|ed|ing)|retire|"
                    r"unblock|correct|patch|rework", re.IGNORECASE)

# Last git failure, recorded (not discarded) so the except handler below
# carries the error instead of returning a blank default.
_last_git_error = ""


def norm_title(t):
    return re.sub(r"\s+", " ", t.strip().lower())


def git(*args):
    global _last_git_error
    try:
        r = subprocess.run(["git", "-C", str(ROOT)] + list(args),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=25)
        return r.stdout
    except (subprocess.TimeoutExpired, OSError) as exc:
        _last_git_error = str(exc)[:120]
        return ""


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


def commits():
    raw = git("log", "--format=%H%x00%P%x00%s%x00%b%x00%ad%x00",
              "--date=short")
    out = []
    for block in raw.split("\x00\n"):
        parts = block.split("\x00")
        if len(parts) < 5:
            continue
        sha, parents, subject, body, date = parts[0], parts[1], parts[2], parts[3], parts[4]
        if not sha or len(parents.split()) > 1:
            continue  # skip merges
        out.append({"sha": sha, "subject": subject.strip(),
                    "body": body.strip(), "date": date.strip()})
    return out


def files_of(sha):
    out = git("show", "--format=", "--name-only", sha)
    return [l.strip() for l in out.splitlines() if l.strip()]


def stat_of(sha):
    out = git("show", "--format=", "--shortstat", sha)
    m = re.search(r"(\d+) insertion.*?, (\d+) deletion", out)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"(\d+) file.*?changed", out)
    return (0, 0) if not m else (0, 0)


def harvest():
    titles = load_titles()
    records, dropped = [], []
    for c in commits():
        text = c["subject"] + "\n" + c["body"]
        if not FIX_RE.search(text):
            continue
        title = c["subject"][:120] or c["sha"][:12]
        if norm_title(title) in titles:
            dropped.append((c["sha"][:12], "dup-corpus", title[:60]))
            continue
        if len(text.strip()) < 40:
            dropped.append((c["sha"][:12], "too-short", title[:60]))
            continue
        files = files_of(c["sha"])
        if not files:
            dropped.append((c["sha"][:12], "no-files", title[:60]))
            continue
        ins, dels = stat_of(c["sha"])
        guards = [f for f in files
                  if "/tests/" in f.replace("\\", "/")
                  or Path(f).name.startswith("test_")]
        body = ("COMMIT %s (%s)\n%s\n%s\nFILES (%d): %s\n"
                "GUARDING TESTS: %s\n+%d/-%d" % (
                    c["sha"][:12], c["date"], c["subject"],
                    c["body"][:1500], len(files), ", ".join(files[:12]),
                    ", ".join(guards[:8]) if guards else "none",
                    ins, dels))[:4000]
        if len(body) < 40:
            dropped.append((c["sha"][:12], "too-short", title[:60]))
            continue
        titles.add(norm_title(title))
        records.append({"source": "git#%s" % c["sha"][:12],
                        "item_id": "git#%s" % c["sha"][:12],
                        "title": title, "body": body, "kind": "error_fix"})
    return records, dropped


def main():
    apply = "--apply" in sys.argv
    records, dropped = harvest()
    guarded = sum(1 for r in records if "GUARDING TESTS: none" not in r["body"])
    print("HARVEST=%d (guarded=%d)" % (len(records), guarded))
    print("DROPPED=%d" % len(dropped))
    why = {}
    for _, _, w in dropped:
        why[w] = why.get(w, 0) + 1
    print("DROP_REASONS=%s" % json.dumps(why))
    for f, i, w in dropped[:15]:
        print("  drop %s %s (%s)" % (f, i, w))
    for r in records[:3]:
        print("  e.g. %s | %s" % (r["item_id"], r["title"][:80]))
    if apply:
        sys.path.insert(0, str(ROOT / "src"))
        from CORE.sentinel_enforcer import permit_here
        with permit_here("bake git harvest output"):
            with open(OUT_JSONL, "w", encoding="utf-8") as fh:
                for r in records:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            with open(OUT_REPORT, "w", encoding="utf-8") as fh:
                for f, i, w in dropped:
                    fh.write("%s\t%s\t%s\n" % (f, i, w))
        print("WROTE %s + %s" % (OUT_JSONL.name, OUT_REPORT.name))


if __name__ == "__main__":
    main()
