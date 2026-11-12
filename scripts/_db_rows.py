# coding: utf-8
"""Real evolution_result rows, read-only, as the monitor reads them."""
import os
import json
import sqlite3

DB = os.environ.get("AZAZAEL_MEMORY_DIR", r"C:\Users\clint\.azazael")
if not DB.endswith(".db"):
    DB = os.path.join(DB, "memory_store.db")
# NOTE (runtime trace 2026-10-18): with mode=ro a missing DB raises
# OperationalError at import. Report absence instead of traceback.
if not os.path.exists(DB):
    print("no database at %s (set AZAZAEL_MEMORY_DIR)" % DB)
    raise SystemExit(0)
con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
cur = con.cursor()
cur.execute("SELECT COUNT(*) FROM memories WHERE event_type='evolution_result'")
print("evolution_result rows:", cur.fetchone()[0])
cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='memories'")
cur.execute("PRAGMA table_info(memories)")
print("memories columns:", [c[1] for c in cur.fetchall()])
cur.execute("SELECT timestamp, substr(data,1,600) FROM memories "
            "WHERE event_type='evolution_result' ORDER BY id DESC LIMIT 2")
for ts, data in cur.fetchall():
    print("\nts:", ts)
    try:
        d = json.loads(data)
        print("  keys:", sorted(d.keys()))
        for k in ("margin", "best_total", "baseline", "pool_id", "promoted", "holdout",
                  "sprt", "genes", "lr", "learning_rate", "epsilon", "discount",
                  "epsilon_decay", "generation"):
            if k in d:
                print("   ", k, "=", str(d[k])[:160])
    except Exception as e:
        print("  unparsed:", e, data[:200])
con.close()
