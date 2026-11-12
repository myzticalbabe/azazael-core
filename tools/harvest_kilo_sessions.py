"""harvest_kilo_sessions.py — Kilo assistant history -> bake JSONL pairs.

Mines the Kilo CLI session store (104 sessions, 12k messages, 48k parts):
  1. tool calls with state.status=error -> error_fix (tool + input + error
     output; recorded failures are the highest-signal rows).
  2. assistant text parts matching fix language -> error_fix (mistake+fix
     narrative window around the match).
  3. completed todos (>=40 chars) -> lesson (finished operational work).

Copies the LIVE db to temp first (frozen snapshot; Kilo is usually
running). Dedupes against bake_corpus + bake_harvest_*.jsonl by normalized
title. Harvest-family output so the merge gate picks it up as base:
  state/bake_harvest_kilo_2026-10-15.jsonl + bake_harvest_kilo_report.txt

Usage: python tools/harvest_kilo_sessions.py [--apply]
  default = dry run (counts only). --apply writes files.
"""
import json
import re
import shutil
import sqlite3
import sys
from pathlib import Path
import argparse

ROOT = Path(__file__).resolve().parents[1]
LIVE_DB = Path.home() / ".local" / "share" / "kilo" / "kilo.db"
SNAP_DB = Path("C:/Users/clint/AppData/Local/Temp/opencode/kilo_harvest.db")
OUT_JSONL = ROOT / "state" / "bake_harvest_kilo_2026-10-15.jsonl"
OUT_REPORT = ROOT / "state" / "bake_harvest_kilo_report.txt"
FIX_RE = re.compile(r"fix|repair|resolv|root cause|error|fail|wrong|broken|"
                    r"workaround|patch|revert", re.IGNORECASE)


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


def snap(live_db, snap_db, live):
    if live:
        return sqlite3.connect("file:%s?mode=ro" % live_db, uri=True)
    shutil.copyfile(live_db, snap_db)
    return sqlite3.connect("file:%s?mode=ro" % snap_db.as_posix(), uri=True)


def harvest(live_db=LIVE_DB, prefix="kilo", live=False):
    titles = load_titles()
    con = snap(live_db, SNAP_DB, live)
    sessions = {}
    for sid, title in con.execute("SELECT id, title FROM session"):
        sessions[sid] = title or sid
    roles = {}
    for mid, mdata in con.execute("SELECT id, data FROM message"):
        try:
            roles[mid] = json.loads(mdata).get("role", "?")
        except Exception:
            continue
    records, dropped = [], []

    def emit(source, item_id, title, body, kind):
        title, body = title[:120], body[:4000]
        if len(body) < 40:
            dropped.append((source, "too-short", title[:60]))
            return
        if norm_title(title) in titles:
            dropped.append((source, "dup-corpus", title[:60]))
            return
        titles.add(norm_title(title))
        records.append({"source": source, "item_id": item_id,
                        "title": title, "body": body, "kind": kind})

    for pid, mid, sid, pdata in con.execute(
            "SELECT id, message_id, session_id, data FROM part"):
        try:
            o = json.loads(pdata)
        except Exception:
            continue
        stitle = sessions.get(sid, sid)
        if o.get("type") == "tool":
            st = o.get("state", {}) or {}
            if st.get("status") != "error":
                continue
            tool = o.get("tool", "?")
            inp = json.dumps(st.get("input", ""))[:800]
            out = str(st.get("output", ""))[:2500]
            firstline = out.strip().splitlines()
            sig = (firstline[0][:60] if firstline else "no output")
            emit("%s#%s" % (prefix, pid), "%s#%s" % (prefix, pid),
                 "tool %s failed: %s (%s)" % (tool, sig, stitle[:40]),
                 "SESSION: %s\nTOOL: %s\nINPUT: %s\nERROR: %s"
                 % (stitle, tool, inp, out), "error_fix")
        elif o.get("type") == "text" and roles.get(mid) == "assistant":
            text = o.get("text", "") or ""
            m = FIX_RE.search(text)
            if not m:
                continue
            i = max(0, m.start() - 1200)
            matchline = text[m.start():m.start() + 80].replace("\n", " ")
            emit("%s#%s" % (prefix, pid), "%s#%s" % (prefix, pid),
                 "fix narrative: %s (%s)" % (matchline[:70], stitle[:35]),
                 "SESSION: %s\n%s" % (stitle, text[i:i + 2800]),
                 "error_fix")
    for sid, content in con.execute(
            "SELECT session_id, content FROM todo WHERE status='completed'"):
        if len((content or "")) < 40:
            continue
        emit("%s-todo#%s" % (prefix, sid), "%s-todo#%s" % (prefix, sid),
             "Completed: %s" % content[:90],
             "SESSION: %s\nCOMPLETED TODO: %s"
             % (sessions.get(sid, sid), content[:3000]), "lesson")
    con.close()
    return records, dropped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--db", default=str(LIVE_DB))
    ap.add_argument("--prefix", default="kilo")
    ap.add_argument("--out", default=str(OUT_JSONL))
    ap.add_argument("--report", default=str(OUT_REPORT))
    ap.add_argument("--live", action="store_true",
                    help="read the live DB read-only instead of copying "
                         "to temp first (needed when the DB is bigger "
                         "than free disk)")
    args = ap.parse_args()
    records, dropped = harvest(Path(args.db), args.prefix, args.live)
    kinds = {}
    for r in records:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    print("HARVEST=%d %s" % (len(records), json.dumps(kinds)))
    print("DROPPED=%d" % len(dropped))
    why = {}
    for _, _, w in dropped:
        why[w] = why.get(w, 0) + 1
    print("DROP_REASONS=%s" % json.dumps(why))
    for f, i, w in dropped[:15]:
        print("  drop %s %s (%s)" % (f, i, w))
    for r in records[:3]:
        print("  e.g. %s | %s" % (r["item_id"], r["title"][:80]))
    if args.apply:
        sys.path.insert(0, str(ROOT / "src"))
        from CORE.sentinel_enforcer import permit_here
        out_jsonl, out_report = Path(args.out), Path(args.report)
        with permit_here("bake session harvest output"):
            with open(out_jsonl, "w", encoding="utf-8") as fh:
                for r in records:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            with open(out_report, "w", encoding="utf-8") as fh:
                for f, i, w in dropped:
                    fh.write("%s\t%s\t%s\n" % (f, i, w))
        print("WROTE %s + %s" % (out_jsonl.name, out_report.name))


if __name__ == "__main__":
    main()
