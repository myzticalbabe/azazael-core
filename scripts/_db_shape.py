# coding: utf-8
"""What do the real outcome rows look like? (read-only)"""
import os
import json
import sqlite3

DB = os.environ.get("AZAZAEL_MEMORY_DIR", r"C:\Users\clint\.azazael")
if not DB.endswith(".db"):
    DB = os.path.join(DB, "memory_store.db")
print("db:", DB, "exists:", os.path.exists(DB))
# NOTE (runtime trace 2026-10-18): with mode=ro a missing DB raises
# OperationalError at import. Report absence instead of traceback.
if not os.path.exists(DB):
    print("no database at %s (set AZAZAEL_MEMORY_DIR)" % DB)
    raise SystemExit(0)
con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
cur = con.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%evolution%'")
print("evolution tables:", [r[0] for r in cur.fetchall()])
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
print("all tables:", [r[0] for r in cur.fetchall()][:40])
for t in ("evolution_result", "evolution_results"):
    try:
        cur.execute("PRAGMA table_info(%s)" % t)
        cols = cur.fetchall()
        if not cols:
            continue
        print("\n%s columns:" % t, [c[1] for c in cols])
        cur.execute("SELECT COUNT(*) FROM %s" % t)
        print("%s rows:" % t, cur.fetchone()[0])
        cur.execute("SELECT * FROM %s ORDER BY rowid DESC LIMIT 2" % t)
        for r in cur.fetchall():
            print("  sample:", str(r)[:400])
    except Exception as e:
        print(t, "->", e)
con.close()
