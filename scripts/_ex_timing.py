# coding: utf-8
"""How long does one full epistemic report take on real rows?

The monitor refreshes every 30s; a report that takes longer than the refresh
interval would back the panel up, so this is a budget question, not a
curiosity.
"""
import json
import os
import sqlite3
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src", "CORE", "WORMS", "expansion"))
import exmachina as ex

DB = os.environ.get("AZAZAEL_MEMORY_DIR", r"C:\Users\clint\.azazael")
if not DB.endswith(".db"):
    DB = os.path.join(DB, "memory_store.db")
# NOTE (runtime trace 2026-10-18): with mode=ro a missing DB raises
# OperationalError at import. Report absence instead of traceback.
if not os.path.exists(DB):
    print("no database at %s (set AZAZAEL_MEMORY_DIR)" % DB)
    raise SystemExit(0)
con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
rows = []
for ts, data in con.execute("SELECT timestamp, data FROM memories "
                            "WHERE event_type='evolution_result' "
                            "ORDER BY id DESC LIMIT 120").fetchall():
    try:
        d = json.loads(data)
    except Exception:
        continue
    if isinstance(d, dict):
        rows.append((ts, d))
con.close()
print("rows:", len(rows))

for label, kwargs in (("organs only", {"predictor_keys": []}),
                      ("full (with operators)", {})):
    t0 = time.time()
    rep = ex.epistemic_report(rows, ages={"oracle": 30.0}, **kwargs)
    dt = time.time() - t0
    print("  %-22s %6.3fs   severity=%s mood=%s" % (
        label, dt, rep["severity"]["score"], rep["mood"]["mood"]))

# Repeated calls: the monitor does this every frame.
t0 = time.time()
for _ in range(3):
    ex.epistemic_report(rows, ages={"oracle": 30.0})
print("  3x full                %6.3fs total" % (time.time() - t0))

# RADIX scaling check: the conditioning search is the combinatorial part.
for nf in (3, 4, 6):
    keys = ["learning_rate", "epsilon", "discount", "epsilon_decay",
            "population", "episodes"][:nf]
    t0 = time.time()
    ex.organ_readings(rows, predictor_keys=keys)
    print("  organ_readings %d feats  %6.3fs" % (nf, time.time() - t0))
