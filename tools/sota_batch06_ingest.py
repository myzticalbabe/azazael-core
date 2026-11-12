"""Ingest batch06 technique steals into Engram as sota-steal/* learnings.

Idempotent: the skip set is read from the Engram SQLite store, so a re-run only
writes entries whose topic_key is genuinely absent.

Previous version built the skip set from `engram search`'s first page, which
never contains topic_keys (the CLI summary omits them), so it always reported
0 skips and re-wrote every row.

Usage:  python sota_batch06_ingest.py [limit]
"""
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

ENGRAM = os.environ.get("ENGRAM_BIN", r"C:\Users\clint\go\bin\engram.exe")
ENGRAM_DB = os.environ.get("ENGRAM_DB", r"C:\Users\clint\.engram\engram.db")
B = Path(__file__).resolve().parents[2] / "state" / "sota_batches" / "batch06"

entries = json.loads((B / "techniques_manifest.json").read_text(encoding="utf-8"))
limit = int(sys.argv[1]) if len(sys.argv) > 1 else len(entries)


def slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:48].strip("-")


def trim(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "\u2026"


def existing_topics() -> set:
    """topic_keys already present in the Engram store (read-only)."""
    try:
        con = sqlite3.connect("file:%s?mode=ro" % ENGRAM_DB.replace("\\", "/"), uri=True)
        rows = con.execute(
            "SELECT topic_key FROM observations "
            "WHERE deleted_at IS NULL AND topic_key LIKE 'sota-steal/%'").fetchall()
        con.close()
        return {r[0] for r in rows}
    except Exception as exc:
        print("WARN: could not read store (%s); treating all as new" % exc, file=sys.stderr)
        return set()


seen = existing_topics()
print("existing sota-steal topics:", len(seen))

ok = skip = fail = 0
for e in entries[:limit]:
    topic = "sota-steal/ep%d-%s" % (e["ep"], slug(e["name"]))
    if topic in seen:
        skip += 1
        continue
    content = (
        "**What**: {name} steal (EP{ep}, {arxiv}) \u2014 {mech}\n"
        "**Why**: SOTA step 5 technique extract for mixer; batch06/141\n"
        "**Where**: C:/Azazael/state/sota_batches/batch06/TECHNIQUES.md\n"
        "**Mixer**: {mix}"
    ).format(name=e["name"], ep=e["ep"], arxiv=e["arxiv"],
             mech=trim(e["mechanism"], 700), mix=trim(e["mixer"], 500))
    r = subprocess.run([ENGRAM, "save", "%s steal" % e["name"], content,
                        "--type", "learning", "--project", "azazael",
                        "--scope", "project", "--topic", topic],
                       capture_output=True, text=True, timeout=120,
                       encoding="utf-8", errors="replace")
    if r.returncode == 0:
        ok += 1
        seen.add(topic)
    else:
        fail += 1
        print("FAIL ep%s: %s | %s" % (e["ep"], (r.stderr or "").strip()[:160],
                                      (r.stdout or "").strip()[:160]))

print("saved=%d skipped=%d failed=%d of %d" % (ok, skip, fail, min(limit, len(entries))))
